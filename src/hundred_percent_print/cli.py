from __future__ import annotations

import argparse
import os
import plistlib
import signal
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from . import __version__
from .options import (
    DEFAULT_MEDIA,
    DEFAULT_QUEUE,
    DEFAULT_SERVICE_NAME,
    PrintSettings,
    lp_option_args,
    lpadmin_option_args,
    normalize_extra_options,
)
from .pdf import write_calibration_pdf


DEFAULT_PORT = 8799
DEFAULT_FRONTEND_QUEUE = "Hundred_Percent_Patterns"
DEFAULT_BACKEND_SERVICE_NAME = "Hundred Percent Print Private Backend"
DEFAULT_AIRPRINT_SERVICE_NAME = "100 Percent Pattern Print SAFE"
CUPS_FRONTEND_RETRY_ATTEMPTS = 3
CUPS_FRONTEND_RETRY_DELAY = 0.5
SUPPORTED_MIME_TYPES = "application/pdf,image/jpeg,image/png,image/pwg-raster,image/urf"
REPO_ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hundred-percent-print",
        description="Run an exact-scale local AirPrint proxy for CUPS/Canon pattern printing.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="List CUPS printers and discoverable devices.")
    discover.add_argument("--timeout", type=int, default=5, help="Discovery timeout in seconds.")
    discover.set_defaults(func=cmd_discover)

    serve = subparsers.add_parser("serve", help="Run the local AirPrint-compatible IPP proxy server.")
    add_common_print_settings(serve)
    serve.add_argument("--upstream", default=DEFAULT_QUEUE, help="Existing CUPS destination to forward jobs to.")
    serve.add_argument("--name", default=DEFAULT_SERVICE_NAME, help="AirPrint service name shown to clients.")
    serve.add_argument(
        "--backend-name",
        default=DEFAULT_BACKEND_SERVICE_NAME,
        help="Private backend name used internally when --mode=cups.",
    )
    serve.add_argument(
        "--mode",
        choices=("cups", "direct"),
        default="cups",
        help="Use a CUPS-shared AirPrint front end, or expose the backend directly.",
    )
    serve.add_argument(
        "--cups-frontend-queue",
        default=DEFAULT_FRONTEND_QUEUE,
        help="CUPS queue name to create/update when --mode=cups.",
    )
    serve.add_argument(
        "--airprint-name",
        default=DEFAULT_AIRPRINT_SERVICE_NAME,
        help="AirPrint DNS-SD service name advertised when --mode=cups.",
    )
    serve.add_argument(
        "--no-airprint-advertise",
        action="store_true",
        help="Do not publish an explicit AirPrint _universal DNS-SD service when --mode=cups.",
    )
    serve.add_argument(
        "--allow-missing-upstream",
        action="store_true",
        help="Start safely while the upstream queue is unavailable; real jobs remain fail-closed.",
    )
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="IPP port for the local proxy.")
    serve.add_argument("--spool", type=Path, default=default_spool_dir(), help="Directory for ippeveprinter spool files.")
    serve.add_argument("--job-log", type=Path, default=default_job_log(), help="JSONL log path for forwarded jobs.")
    serve.add_argument("--no-job-log", action="store_true", help="Disable structured job logging.")
    serve.add_argument("--keep-spool", action="store_true", help="Keep server spool files for debugging.")
    serve.add_argument("--forward-dry-run", action="store_true", help="Accept AirPrint jobs but do not submit them to CUPS.")
    serve.add_argument("--verbose", action="count", default=1, help="Increase ippeveprinter verbosity.")
    serve.add_argument("--dry-run", action="store_true", help="Print the server command without running it.")
    serve.set_defaults(func=cmd_serve)

    print_cmd = subparsers.add_parser("print", help="Print a file through CUPS with exact-scale options.")
    add_common_print_settings(print_cmd)
    print_cmd.add_argument("file", type=Path, help="PDF or image file to print.")
    print_cmd.add_argument("--queue", default=DEFAULT_QUEUE, help="CUPS destination to print to.")
    print_cmd.add_argument("--copies", type=int, default=1, help="Number of copies.")
    print_cmd.add_argument("--dry-run", action="store_true", help="Print the lp command without running it.")
    print_cmd.set_defaults(func=cmd_print)

    calibration = subparsers.add_parser("calibration", help="Generate a measurable calibration PDF.")
    calibration.add_argument("--media", default=DEFAULT_MEDIA, help="Calibration media size: Letter, Legal, A4, or A5.")
    calibration.add_argument("--output", type=Path, help="Output PDF path.")
    calibration.add_argument("--print", dest="print_after", action="store_true", help="Print the PDF after generating it.")
    calibration.add_argument("--queue", default=DEFAULT_QUEUE, help="CUPS destination to print to.")
    add_common_print_settings(calibration, include_media=False)
    calibration.set_defaults(func=cmd_calibration)

    configure = subparsers.add_parser(
        "configure-cups",
        help="Create/update a shared CUPS proxy queue with exact-scale defaults.",
    )
    add_common_print_settings(configure)
    configure.add_argument("--queue", default="Hundred_Percent_Patterns", help="Name of the shared CUPS queue to create.")
    configure.add_argument("--description", default=DEFAULT_SERVICE_NAME, help="Printer description.")
    source = configure.add_mutually_exclusive_group(required=True)
    source.add_argument("--device-uri", help="Printer device URI to use.")
    source.add_argument("--from-destination", help="Copy the device URI from an existing CUPS destination.")
    configure.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    configure.set_defaults(func=cmd_configure_cups)

    status = subparsers.add_parser("status", help="Show CUPS destination status and options.")
    status.add_argument("--queue", default=DEFAULT_QUEUE, help="CUPS destination to inspect.")
    status.set_defaults(func=cmd_status)

    self_test = subparsers.add_parser("self-test", help="Run a no-paper end-to-end proxy self-test.")
    add_common_print_settings(self_test)
    self_test.add_argument("--upstream", default=DEFAULT_QUEUE, help="Existing CUPS destination to validate.")
    self_test.add_argument(
        "--mode",
        choices=("direct", "cups"),
        default="direct",
        help="Test the direct backend or the CUPS front-end AirPrint path.",
    )
    self_test.add_argument(
        "--cups-frontend-queue",
        help="Temporary CUPS queue name for --mode=cups. Defaults to a generated test queue.",
    )
    self_test.add_argument("--port", type=int, default=0, help="IPP port to use. 0 selects a free local port.")
    self_test.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait for server/job completion.")
    self_test.add_argument("--keep-artifacts", action="store_true", help="Keep temporary self-test files.")
    self_test.set_defaults(func=cmd_self_test)

    launch_agent = subparsers.add_parser("launch-agent", help="Print a macOS LaunchAgent plist for the server.")
    add_common_print_settings(launch_agent)
    launch_agent.add_argument("--upstream", default=DEFAULT_QUEUE, help="Existing CUPS destination to forward jobs to.")
    launch_agent.add_argument("--name", default=DEFAULT_SERVICE_NAME, help="AirPrint service name shown to clients.")
    launch_agent.add_argument(
        "--backend-name",
        default=DEFAULT_BACKEND_SERVICE_NAME,
        help="Private backend name used internally when --mode=cups.",
    )
    launch_agent.add_argument(
        "--mode",
        choices=("cups", "direct"),
        default="cups",
        help="Use a CUPS-shared AirPrint front end, or expose the backend directly.",
    )
    launch_agent.add_argument(
        "--cups-frontend-queue",
        default=DEFAULT_FRONTEND_QUEUE,
        help="CUPS queue name to create/update when --mode=cups.",
    )
    launch_agent.add_argument(
        "--airprint-name",
        default=DEFAULT_AIRPRINT_SERVICE_NAME,
        help="AirPrint DNS-SD service name advertised when --mode=cups.",
    )
    launch_agent.add_argument(
        "--no-airprint-advertise",
        action="store_true",
        help="Do not publish an explicit AirPrint _universal DNS-SD service when --mode=cups.",
    )
    launch_agent.add_argument(
        "--allow-missing-upstream",
        action="store_true",
        help="Start safely while the upstream queue is unavailable.",
    )
    launch_agent.add_argument("--port", type=int, default=DEFAULT_PORT, help="IPP port for the local proxy.")
    launch_agent.add_argument("--label", default="com.hundred-percent-print.server", help="LaunchAgent label.")
    launch_agent.add_argument("--spool", type=Path, default=default_spool_dir(), help="Directory for server spool files.")
    launch_agent.add_argument("--job-log", type=Path, default=default_job_log(), help="JSONL log path for forwarded jobs.")
    launch_agent.set_defaults(func=cmd_launch_agent)

    return parser


def add_common_print_settings(parser: argparse.ArgumentParser, include_media: bool = True) -> None:
    if include_media:
        parser.add_argument("--media", default=DEFAULT_MEDIA, help="CUPS media keyword, e.g. Letter or A4.")
    parser.add_argument("--page-size", help="PPD PageSize keyword. Defaults to --media.")
    parser.add_argument("--color-model", default="RGB", help="CUPS ColorModel value.")
    parser.add_argument("--quality", default="High", help="CUPS cupsPrintQuality value.")
    parser.add_argument("--media-type", default="auto", help="Canon/CUPS MediaType value.")
    parser.add_argument("--resolution", help="Optional printer-resolution value, e.g. 600dpi.")
    parser.add_argument(
        "-o",
        "--option",
        dest="extra_options",
        action="append",
        help="Additional exact-scale lp option in name=value form. May be repeated.",
    )


def cmd_discover(args: argparse.Namespace) -> int:
    print("CUPS destinations:")
    _run_passthrough(["lpstat", "-v"], check=False)
    print()
    print("Discovered printer devices:")
    _run_passthrough(["lpinfo", "--timeout", str(args.timeout), "-v"], check=False)
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    upstream_exists = destination_exists(args.upstream)
    if not upstream_exists and not getattr(args, "allow_missing_upstream", False):
        print(f"Upstream CUPS destination not found: {args.upstream}", file=sys.stderr)
        print("Run `hundred-percent-print discover` to list available queues.", file=sys.stderr)
        return 2
    if not upstream_exists:
        print(
            f"WARNING: upstream CUPS destination {args.upstream!r} is unavailable; "
            "the server will start, but real jobs will be rejected until it appears.",
            file=sys.stderr,
        )

    settings = settings_from_args(args, upstream=args.upstream)
    args.spool.mkdir(parents=True, exist_ok=True)
    job_log = None if args.no_job_log else args.job_log
    if job_log:
        job_log.parent.mkdir(parents=True, exist_ok=True)
    command = ippeveprinter_command(args, forwarder_path(), advertise=args.mode == "direct")
    env = os.environ.copy()
    env.update(env_for_settings(settings, forward_dry_run=args.forward_dry_run, job_log=job_log))
    if args.keep_spool:
        env["HPP_KEEP_INPUT"] = "1"

    if args.dry_run:
        print("Environment:")
        for key in sorted(k for k in env if k.startswith("HPP_")):
            print(f"  {key}={env[key]}")
        print("Command:")
        print(f"  {shlex.join(command)}")
        if args.mode == "cups":
            print("CUPS front-end commands:")
            for cups_command in cups_frontend_commands(args.cups_frontend_queue, args.name, args.port, settings):
                print(f"  {shlex.join(cups_command)}")
            if not args.no_airprint_advertise:
                print("AirPrint advertisement commands:")
                for advertise_command in airprint_advertise_commands(args.airprint_name, args.cups_frontend_queue):
                    print(f"  {shlex.join(advertise_command)}")
        return 0

    if args.mode == "cups":
        print(f"Starting private exact-scale backend {args.backend_name!r} on port {args.port}.")
        print(f"Configuring shared CUPS AirPrint queue {args.cups_frontend_queue!r}.")
    else:
        print(f"Serving direct AirPrint proxy {args.name!r} on port {args.port}.")
    print(f"Forwarding every job to CUPS destination {args.upstream!r} with print-scaling=none.")
    if args.forward_dry_run:
        print("Forward dry-run is enabled; jobs will be accepted but not submitted to CUPS.")
    if job_log:
        print(f"Writing forwarded job logs to {job_log}.")
    print("Keep this process running while printing from AirPrint clients.")
    if args.mode == "cups":
        return run_cups_frontend_server(args, command, env, settings)
    return subprocess.run(command, env=env, check=False).returncode


def cmd_print(args: argparse.Namespace) -> int:
    if not args.file.is_file():
        print(f"File not found: {args.file}", file=sys.stderr)
        return 2
    settings = settings_from_args(args, upstream=args.queue)
    command = ["lp", "-d", args.queue, "-n", str(args.copies), *lp_option_args(settings), str(args.file)]
    if args.dry_run:
        print(shlex.join(command))
        return 0
    return _run_passthrough(command)


def cmd_calibration(args: argparse.Namespace) -> int:
    media = args.media
    output = args.output or Path(f"calibration-{media.lower()}.pdf")
    written = write_calibration_pdf(output, media=media)
    print(f"Wrote {written}")

    if args.print_after:
        print_args = argparse.Namespace(
            file=written,
            queue=args.queue,
            copies=1,
            dry_run=False,
            media=media,
            page_size=args.page_size,
            color_model=args.color_model,
            quality=args.quality,
            media_type=args.media_type,
            resolution=args.resolution,
            extra_options=args.extra_options,
        )
        return cmd_print(print_args)
    return 0


def cmd_configure_cups(args: argparse.Namespace) -> int:
    device_uri = args.device_uri or device_uri_for_destination(args.from_destination)
    if not device_uri:
        return 2

    settings = settings_from_args(args, upstream=args.queue)
    commands = [
        ["cupsctl", "--share-printers", "BrowseLocalProtocols=none"],
        [
            "lpadmin",
            "-p",
            args.queue,
            "-E",
            "-v",
            device_uri,
            "-m",
            "everywhere",
            "-D",
            args.description,
            "-L",
            "Local exact-scale AirPrint proxy",
            "-o",
            "printer-is-shared=true",
            "-o",
            "printer-error-policy=retry-job",
            *lpadmin_option_args(settings),
        ],
        ["lpoptions", "-p", args.queue, *lp_option_args(settings)],
        ["cupsenable", args.queue],
        ["cupsaccept", args.queue],
    ]

    for command in commands:
        if args.dry_run:
            print(shlex.join(command))
        else:
            code = _run_passthrough(command)
            if code != 0:
                return code
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    commands = [
        ["lpstat", "-p", args.queue, "-l"],
        ["lpstat", "-v", args.queue],
        ["lpoptions", "-p", args.queue],
        ["lpoptions", "-p", args.queue, "-l"],
    ]
    exit_code = 0
    for command in commands:
        print(f"$ {shlex.join(command)}")
        result = _run_passthrough(command, check=False)
        exit_code = exit_code or result
        print()
    return exit_code


def cmd_self_test(args: argparse.Namespace) -> int:
    if not destination_exists(args.upstream):
        print(f"Upstream CUPS destination not found: {args.upstream}", file=sys.stderr)
        print("Run `./scripts/hundred-percent-print discover` to list available queues.", file=sys.stderr)
        return 2

    port = args.port or find_free_port()
    cups_queue = args.cups_frontend_queue or f"HPP_Self_Test_{os.getpid()}"
    should_delete_cups_queue = args.mode == "cups" and args.cups_frontend_queue is None
    with tempfile.TemporaryDirectory(prefix="hpp-self-test-") as temp_dir:
        temp_path = Path(temp_dir)
        spool = temp_path / "spool"
        job_log = temp_path / "jobs.jsonl"
        calibration = write_calibration_pdf(temp_path / "calibration.pdf", media=args.media)
        command = [
            sys.executable,
            "-m",
            "hundred_percent_print.cli",
            "serve",
            "--upstream",
            args.upstream,
            "--port",
            str(port),
            "--spool",
            str(spool),
            "--job-log",
            str(job_log),
            "--media",
            args.media,
            "--mode",
            args.mode,
            "--color-model",
            args.color_model,
            "--quality",
            args.quality,
            "--media-type",
            args.media_type,
            "--forward-dry-run",
        ]
        if args.mode == "cups":
            command.extend(["--cups-frontend-queue", cups_queue, "--no-airprint-advertise"])
        if args.page_size:
            command.extend(["--page-size", args.page_size])
        if args.resolution:
            command.extend(["--resolution", args.resolution])
        for option in args.extra_options or []:
            command.extend(["--option", option])

        env = os.environ.copy()
        source_dir = REPO_ROOT / "src"
        if source_dir.exists():
            env["PYTHONPATH"] = str(source_dir)

        proc = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            start_new_session=True,
        )

        try:
            wait_for_ipp_server(port, proc, timeout=args.timeout)
            if args.mode == "cups":
                wait_for_cups_queue(cups_queue, proc, timeout=args.timeout)
                submit_result = submit_cups_queue_self_test_job(cups_queue, calibration)
            else:
                submit_result = submit_self_test_job(port, calibration, timeout=args.timeout)
            if submit_result.returncode != 0:
                print(submit_result.stdout, end="")
                print(submit_result.stderr, end="", file=sys.stderr)
                return submit_result.returncode

            event = wait_for_job_log(job_log, timeout=args.timeout)
            command_options = " ".join(event.get("command", []))
            if event.get("status") != "dry-run" or "print-scaling=none" not in command_options:
                print(f"Self-test job log did not show a locked dry-run job: {event}", file=sys.stderr)
                return 1

            if args.mode == "cups":
                print(f"Self-test passed through CUPS queue {cups_queue}")
            else:
                print(f"Self-test passed on ipp://localhost:{port}/ipp/print")
            print(f"Forwarder dry-run command: {shlex.join(event['command'])}")
            if args.keep_artifacts:
                keep_path = Path.cwd() / "self-test-artifacts"
                if keep_path.exists():
                    shutil.rmtree(keep_path)
                shutil.copytree(temp_path, keep_path)
                print(f"Kept artifacts in {keep_path}")
            return 0
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        finally:
            terminate_process_group(proc)
            if should_delete_cups_queue:
                delete_cups_queue(cups_queue)


def cmd_launch_agent(args: argparse.Namespace) -> int:
    plist = launch_agent_plist(args)
    sys.stdout.buffer.write(plistlib.dumps(plist, sort_keys=False))
    return 0


def launch_agent_plist(args: argparse.Namespace) -> dict[str, object]:
    program_args = [
        sys.executable,
        "-m",
        "hundred_percent_print.cli",
        "serve",
        "--upstream",
        args.upstream,
        "--name",
        args.name,
        "--mode",
        args.mode,
        "--port",
        str(args.port),
        "--spool",
        str(args.spool),
        "--job-log",
        str(args.job_log),
        "--media",
        args.media,
        "--color-model",
        args.color_model,
        "--quality",
        args.quality,
        "--media-type",
        args.media_type,
    ]
    if args.mode == "cups":
        program_args.extend(["--cups-frontend-queue", args.cups_frontend_queue])
        program_args.extend(["--backend-name", args.backend_name])
        program_args.extend(["--airprint-name", args.airprint_name])
        if args.no_airprint_advertise:
            program_args.append("--no-airprint-advertise")
    if getattr(args, "allow_missing_upstream", False):
        program_args.append("--allow-missing-upstream")
    if args.page_size:
        program_args.extend(["--page-size", args.page_size])
    if args.resolution:
        program_args.extend(["--resolution", args.resolution])
    for option in args.extra_options or []:
        program_args.extend(["--option", option])

    environment = {}
    source_dir = REPO_ROOT / "src"
    if source_dir.exists():
        environment["PYTHONPATH"] = str(source_dir)

    plist: dict[str, object] = {
        "Label": args.label,
        "ProgramArguments": program_args,
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(default_log_dir() / "server.out.log"),
        "StandardErrorPath": str(default_log_dir() / "server.err.log"),
    }
    if environment:
        plist["EnvironmentVariables"] = environment

    return plist


def settings_from_args(args: argparse.Namespace, upstream: str) -> PrintSettings:
    try:
        extra_options = normalize_extra_options(args.extra_options)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    return PrintSettings(
        upstream_queue=upstream,
        media=getattr(args, "media", DEFAULT_MEDIA),
        page_size=args.page_size,
        color_model=args.color_model,
        print_quality=args.quality,
        media_type=args.media_type,
        resolution=args.resolution,
        extra_options=extra_options,
    )


def env_for_settings(
    settings: PrintSettings,
    *,
    forward_dry_run: bool = False,
    job_log: Path | None = None,
) -> dict[str, str]:
    env = {
        "HPP_UPSTREAM_QUEUE": settings.upstream_queue,
        "HPP_MEDIA": settings.media,
        "HPP_PAGE_SIZE": settings.normalized_page_size,
        "HPP_COLOR_MODEL": settings.color_model,
        "HPP_PRINT_QUALITY": settings.print_quality,
        "HPP_MEDIA_TYPE": settings.media_type,
    }
    if settings.resolution:
        env["HPP_RESOLUTION"] = settings.resolution
    if settings.extra_options:
        env["HPP_EXTRA_OPTIONS"] = "\n".join(settings.extra_options)
    if forward_dry_run:
        env["HPP_DRY_RUN"] = "1"
    if job_log:
        env["HPP_JOB_LOG"] = str(job_log)
    return env


def ippeveprinter_command(args: argparse.Namespace, command_path: Path, *, advertise: bool = True) -> list[str]:
    verbose_flag = "-" + ("v" * max(args.verbose, 1))
    ippeveprinter = shutil.which("ippeveprinter") or "/usr/bin/ippeveprinter"
    service_name = args.name if advertise else getattr(args, "backend_name", args.name)
    command = [
        ippeveprinter,
        verbose_flag,
        "--no-web-forms",
        "-r",
        "_print,_universal" if advertise else "off",
        "-s",
        "1,1",
        "-M",
        "Canon",
        "-m",
        "Exact Scale Pattern Proxy",
        "-f",
        SUPPORTED_MIME_TYPES,
        "-F",
        "application/pdf",
        "-c",
        str(command_path),
        "-d",
        str(args.spool),
        "-p",
        str(args.port),
    ]
    if args.keep_spool:
        command.append("-k")
    command.append(service_name)
    return command


def run_cups_frontend_server(
    args: argparse.Namespace,
    command: list[str],
    env: dict[str, str],
    settings: PrintSettings,
) -> int:
    proc = subprocess.Popen(command, env=env)
    advertise_procs: list[subprocess.Popen[str]] = []
    try:
        wait_for_ipp_server(args.port, proc, timeout=15.0)
        configure_cups_frontend(args.cups_frontend_queue, args.name, args.port, settings)
        if not args.no_airprint_advertise:
            advertise_procs = start_airprint_advertisements(args.airprint_name, args.cups_frontend_queue)
            print(f"Advertising AirPrint service {args.airprint_name!r}.")
        print(f"Print from iOS to the AirPrint service named {args.airprint_name!r}.")
        print(f"Backend spool directory: {args.spool}")
        return proc.wait()
    except KeyboardInterrupt:
        return 130
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        for advertise_proc in advertise_procs:
            terminate_process(advertise_proc)
        terminate_process(proc)


def cups_frontend_commands(
    queue: str,
    description: str,
    backend_port: int,
    settings: PrintSettings,
) -> list[list[str]]:
    backend_uri = f"ipp://127.0.0.1:{backend_port}/ipp/print"
    return [
        ["cupsctl", "--share-printers", "--remote-any", "BrowseLocalProtocols=none"],
        [
            "lpadmin",
            "-p",
            queue,
            "-E",
            "-v",
            backend_uri,
            "-m",
            "everywhere",
            "-D",
            description,
            "-L",
            "Local exact-scale AirPrint proxy",
            "-o",
            "printer-is-shared=true",
            "-o",
            "printer-error-policy=retry-job",
            *lpadmin_option_args(settings),
        ],
        ["lpoptions", "-p", queue, *lp_option_args(settings)],
        ["cupsenable", queue],
        ["cupsaccept", queue],
    ]


def configure_cups_frontend(queue: str, description: str, backend_port: int, settings: PrintSettings) -> None:
    for command in cups_frontend_commands(queue, description, backend_port, settings):
        for attempt in range(1, CUPS_FRONTEND_RETRY_ATTEMPTS + 1):
            result = subprocess.run(command, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                break
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            details = stderr or stdout or f"exit status {result.returncode}"
            if "Bad file descriptor" in details and attempt < CUPS_FRONTEND_RETRY_ATTEMPTS:
                time.sleep(CUPS_FRONTEND_RETRY_DELAY)
                continue
            raise RuntimeError(f"CUPS front-end command failed: {shlex.join(command)}\n{details}")


def airprint_advertise_commands(service_name: str, queue: str) -> list[list[str]]:
    txt_records = airprint_txt_records(queue)
    dns_sd = shutil.which("dns-sd")
    if dns_sd:
        return [
            [dns_sd, "-R", service_name, "_ipp._tcp,_universal", "local", "631", *txt_records],
            [dns_sd, "-R", service_name, "_ipps._tcp,_universal", "local", "631", *txt_records],
        ]

    avahi_publish = shutil.which("avahi-publish-service") or shutil.which("avahi-publish")
    if avahi_publish:
        return [
            [
                avahi_publish,
                "--no-fail",
                "--subtype=_universal._sub._ipp._tcp",
                service_name,
                "_ipp._tcp",
                "631",
                *txt_records,
            ],
            [
                avahi_publish,
                "--no-fail",
                "--subtype=_universal._sub._ipps._tcp",
                service_name,
                "_ipps._tcp",
                "631",
                *txt_records,
            ],
        ]

    return [
        ["/usr/bin/dns-sd", "-R", service_name, "_ipp._tcp,_universal", "local", "631", *txt_records],
        ["/usr/bin/dns-sd", "-R", service_name, "_ipps._tcp,_universal", "local", "631", *txt_records],
    ]


def airprint_txt_records(queue: str) -> list[str]:
    stable_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"hundred-percent-print:{queue}")
    hostname = socket.gethostname().rstrip(".")
    if "." not in hostname:
        hostname = f"{hostname}.local"
    return [
        "txtvers=1",
        f"rp=printers/{queue}",
        "qtotal=1",
        "priority=10",
        "ty=Exact-Scale-Pattern-Proxy",
        "product=(Exact-Scale-Pattern-Proxy)",
        "pdl=application/octet-stream,application/pdf,image/jpeg,image/urf,image/pwg-raster",
        "note=Exact-scale-fabric-patterns",
        f"adminurl=http://{hostname}:631/printers/{queue}",
        "usb_MFG=Canon",
        "usb_MDL=Exact-Scale-Pattern-Proxy",
        "usb_CMD=URF",
        "URF=V1.4,CP1,PQ3-5,RS300-600,SRGB24,W8,OB9,OFU0,IS1",
        f"UUID={stable_uuid}",
        "Color=T",
        "Duplex=F",
        "Scan=F",
        "Fax=F",
        "kind=document",
        "PaperMax=legal-A4",
    ]


def start_airprint_advertisements(service_name: str, queue: str) -> list[subprocess.Popen[str]]:
    procs = [subprocess.Popen(command, text=True) for command in airprint_advertise_commands(service_name, queue)]
    time.sleep(0.5)
    for proc in procs:
        if proc.poll() is not None:
            for other in procs:
                terminate_process(other)
            raise RuntimeError(f"AirPrint advertisement command exited early with status {proc.returncode}")
    return procs


def forwarder_path() -> Path:
    installed = shutil.which("hpp-forward-job")
    if installed:
        return Path(installed)

    script = REPO_ROOT / "scripts" / "hpp-forward-job"
    if script.exists():
        return script

    raise RuntimeError("Cannot find hpp-forward-job. Install the package or run from the repo root.")


def destination_exists(destination: str) -> bool:
    return subprocess.run(["lpstat", "-v", destination], capture_output=True, text=True).returncode == 0


def device_uri_for_destination(destination: str) -> str | None:
    result = subprocess.run(["lpstat", "-v", destination], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(result.stderr.strip() or f"Could not inspect destination {destination}", file=sys.stderr)
        return None
    for line in result.stdout.splitlines():
        prefix = f"device for {destination}: "
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    print(f"Could not parse device URI for destination {destination}", file=sys.stderr)
    return None


def _run_passthrough(command: list[str], check: bool = True) -> int:
    result = subprocess.run(command, check=False)
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result.returncode


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_ipp_server(port: int, proc: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    uri = f"ipp://localhost:{port}/ipp/print"
    test_file = "/usr/share/cups/ipptool/get-printer-description-attributes.test"

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=1)
            raise RuntimeError(f"Proxy exited before startup.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")
        result = subprocess.run(
            ["ipptool", "-4", "-T", "1", "-q", uri, test_file],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return
        time.sleep(0.2)

    raise RuntimeError(f"Timed out waiting for proxy at {uri}")


def submit_self_test_job(port: int, calibration: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "ipptool",
            "-4",
            "-T",
            str(max(int(timeout), 1)),
            "-f",
            str(calibration),
            "-d",
            "filetype=application/pdf",
            "-tv",
            f"ipp://localhost:{port}/ipp/print",
            "/usr/share/cups/ipptool/print-job.test",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def submit_cups_queue_self_test_job(queue: str, calibration: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["lp", "-d", queue, "-t", "HPP Self Test", str(calibration)],
        capture_output=True,
        text=True,
        check=False,
    )


def wait_for_cups_queue(queue: str, proc: subprocess.Popen[str], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=1)
            raise RuntimeError(f"Proxy exited before CUPS queue was ready.\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}")

        result = subprocess.run(["lpstat", "-p", queue], capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return
        time.sleep(0.2)

    raise RuntimeError(f"Timed out waiting for CUPS queue {queue}")


def wait_for_job_log(job_log: Path, timeout: float) -> dict[str, object]:
    import json

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if job_log.exists():
            lines = [line for line in job_log.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                return json.loads(lines[-1])
        time.sleep(0.2)

    raise RuntimeError(f"Timed out waiting for job log at {job_log}")


def terminate_process_group(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return

    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        proc.communicate(timeout=5)


def terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def delete_cups_queue(queue: str) -> None:
    subprocess.run(["lpadmin", "-x", queue], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def default_spool_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "hundred-percent-print" / "spool"


def default_log_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "hundred-percent-print"


def default_job_log() -> Path:
    return default_log_dir() / "jobs.jsonl"


if __name__ == "__main__":
    raise SystemExit(main())
