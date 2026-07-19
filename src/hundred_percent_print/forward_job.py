from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .options import PrintSettings, lp_option_args, parse_extra_options_env
from .pdf import page_size_points


SUFFIX_BY_CONTENT_TYPE = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/pwg-raster": ".pwg",
    "image/urf": ".urf",
    "application/postscript": ".ps",
}
SAFE_CONTENT_TYPES = frozenset({"application/pdf"})
PDF_SIZE_TOLERANCE_POINTS = 1.0
PDF_PAGE_BOXES = ("/MediaBox", "/CropBox", "/BleedBox", "/TrimBox", "/ArtBox")
PDFINFO_PAGE_SIZE_RE = re.compile(
    r"^Page(?:\s+\d+)?\s+size:\s+([0-9.]+)\s+x\s+([0-9.]+)\s+pts\b",
    re.MULTILINE,
)
DEFAULT_COMMAND_TIMEOUT_SECONDS = 30.0


class ConfigurationError(RuntimeError):
    pass


class InputRejected(RuntimeError):
    pass


class PdfPageSizeMismatch(RuntimeError):
    pass


class UpstreamUnavailable(RuntimeError):
    pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cleanup_paths: list[Path] = []
    event: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": os.environ.get("HPP_DRY_RUN") == "1",
        "content_type": normalized_content_type(),
        "ipp_job_id": os.environ.get("IPP_JOB_ID"),
        "ipp_job_name": os.environ.get("IPP_JOB_NAME") or os.environ.get("IPP_JOB_NAME_WITHOUT_EXTENSION"),
    }

    try:
        settings = settings_from_env()
        event.update(settings_event(settings))
        input_path = _materialize_input(argv)
        cleanup_paths.append(input_path)
        event.update(
            {
                "source_file": str(input_path),
                "source_size_bytes": input_path.stat().st_size if input_path.exists() else None,
            }
        )
        validate_source_input(input_path, event)
        event["source_sha256"] = file_sha256(input_path)
        forward_path = prepare_input_for_forwarding(input_path, settings, event)
        if forward_path != input_path:
            cleanup_paths.append(forward_path)
        command = build_forward_command(settings, forward_path)
        event.update(
            {
                "submitted_file": str(forward_path),
                "submitted_size_bytes": forward_path.stat().st_size if forward_path.exists() else None,
                "submitted_sha256": file_sha256(forward_path) if forward_path.exists() else None,
                "command": command,
            }
        )

        copies = _positive_int(os.environ.get("IPP_COPIES"))
        if copies and copies != 1:
            command[1:1] = ["-n", str(copies)]
            event["copies"] = copies
        else:
            event["copies"] = 1
        event["command"] = command

        title = event["ipp_job_name"]
        if title:
            command[1:1] = ["-t", title]
        event["command"] = command

        if event["dry_run"]:
            event["upstream_check"] = "skipped-dry-run"
        else:
            ensure_upstream_available(settings.upstream_queue, event)

        _log_info(f"Forwarding job to {settings.upstream_queue}: {shlex.join(command)}")
        if event["dry_run"]:
            _log_info("Dry run enabled; not submitting to CUPS.")
            event["returncode"] = 0
            event["status"] = "dry-run"
            return 0

        result = run_command(command)
        event["returncode"] = result.returncode
        event["status"] = "submitted" if result.returncode == 0 else "failed"
        event["stdout"] = result.stdout.strip()
        event["stderr"] = result.stderr.strip()
        if result.stdout.strip():
            _log_info(result.stdout.strip())
        if result.stderr.strip():
            _log_info(result.stderr.strip())
        if result.returncode != 0:
            _log_error(f"lp exited with status {result.returncode}")
            return 1
        return 0
    except PdfPageSizeMismatch as exc:
        event["returncode"] = 1
        event["status"] = "rejected-page-size"
        event["error"] = str(exc)
        _log_error(str(exc))
        return 1
    except InputRejected as exc:
        event["returncode"] = 1
        event["status"] = "rejected-input"
        event["error"] = str(exc)
        _log_error(str(exc))
        return 1
    except UpstreamUnavailable as exc:
        event["returncode"] = 1
        event["status"] = "upstream-unavailable"
        event["error"] = str(exc)
        _log_error(str(exc))
        return 1
    except ConfigurationError as exc:
        event["returncode"] = 1
        event["status"] = "configuration-error"
        event["error"] = str(exc)
        _log_error(str(exc))
        return 1
    except Exception as exc:
        event["returncode"] = 1
        event["status"] = "error"
        event["error"] = str(exc)
        _log_error(str(exc))
        return 1
    finally:
        write_job_log(event)
        if os.environ.get("HPP_KEEP_INPUT") != "1":
            for cleanup_path in cleanup_paths:
                _remove_temp(cleanup_path)


def settings_from_env() -> PrintSettings:
    upstream_queue = os.environ.get("HPP_UPSTREAM_QUEUE")
    if not upstream_queue:
        raise RuntimeError("HPP_UPSTREAM_QUEUE is required")

    return PrintSettings(
        upstream_queue=upstream_queue,
        media=os.environ.get("HPP_MEDIA", "Letter"),
        page_size=os.environ.get("HPP_PAGE_SIZE") or None,
        color_model=os.environ.get("HPP_COLOR_MODEL", "RGB"),
        print_quality=os.environ.get("HPP_PRINT_QUALITY", "High"),
        media_type=os.environ.get("HPP_MEDIA_TYPE", "auto"),
        resolution=os.environ.get("HPP_RESOLUTION") or None,
        extra_options=parse_extra_options_env(os.environ.get("HPP_EXTRA_OPTIONS")),
    )


def settings_event(settings: PrintSettings) -> dict[str, Any]:
    return {
        "upstream_queue": settings.upstream_queue,
        "media": settings.media,
        "page_size": settings.normalized_page_size,
        "color_model": settings.color_model,
        "print_quality": settings.print_quality,
        "media_type": settings.media_type,
        "resolution": settings.resolution,
        "extra_options": list(settings.extra_options),
    }


def build_forward_command(settings: PrintSettings, input_path: Path) -> list[str]:
    lp_command = os.environ.get("HPP_LP_COMMAND") or shutil.which("lp") or "/usr/bin/lp"
    return [lp_command, "-d", settings.upstream_queue, *lp_option_args(settings), str(input_path)]


def validate_source_input(input_path: Path, event: dict[str, Any]) -> None:
    if not input_path.exists() or not input_path.is_file():
        raise InputRejected(f"Rejected input before printing: file does not exist: {input_path}")

    size = input_path.stat().st_size
    if size <= 0:
        raise InputRejected("Rejected input before printing: received an empty document")

    content_type = str(event.get("content_type") or "")
    if content_type not in SUFFIX_BY_CONTENT_TYPE:
        event["content_type_supported"] = False
        raise InputRejected(f"Rejected input before printing: unsupported content type {content_type!r}")
    event["content_type_supported"] = True

    if content_type not in SAFE_CONTENT_TYPES and os.environ.get("HPP_ALLOW_NON_PDF") != "1":
        event["pdf_size_check"] = "rejected-non-pdf"
        raise InputRejected(
            f"Rejected input before printing: content type {content_type!r} cannot be scale-verified. "
            "Only PDF is accepted by default for dimension-critical pattern printing."
        )

    if content_type == "application/pdf":
        validate_pdf_header(input_path)


def validate_pdf_header(path: Path) -> None:
    header = path.read_bytes()[:1024]
    if b"%PDF-" not in header:
        raise InputRejected("Rejected PDF before printing: missing PDF header")


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def prepare_input_for_forwarding(input_path: Path, settings: PrintSettings, event: dict[str, Any]) -> Path:
    if event.get("content_type") != "application/pdf":
        event["pdf_size_check"] = "skipped-non-pdf"
        return input_path

    if os.environ.get("HPP_DISABLE_PDF_SIZE_CHECK") == "1":
        event["pdf_size_check"] = "disabled"
        return input_path

    expected = page_size_points(settings.normalized_page_size)
    observed = pdf_page_size_points(input_path)
    event["pdf_page_size_points"] = [round(observed[0], 4), round(observed[1], 4)]
    event["pdf_page_size_inches"] = [_round_inches(observed[0]), _round_inches(observed[1])]
    event["expected_page_size_points"] = [round(expected[0], 4), round(expected[1], 4)]
    event["expected_page_size_inches"] = [_round_inches(expected[0]), _round_inches(expected[1])]

    if _page_sizes_match(observed, expected):
        event["pdf_size_check"] = "pass"
        return input_path

    if _can_pad_to_expected_media(observed, expected):
        normalized_path = normalized_pdf_path(input_path, settings.normalized_page_size)
        pad_pdf_to_media(input_path, normalized_path, expected)
        normalized_size = pdf_page_size_points(normalized_path)
        event["normalized_file"] = str(normalized_path)
        event["normalized_page_size_points"] = [round(normalized_size[0], 4), round(normalized_size[1], 4)]
        event["normalized_page_size_inches"] = [
            _round_inches(normalized_size[0]),
            _round_inches(normalized_size[1]),
        ]
        event["pdf_normalization"] = {
            "mode": "pad-to-media",
            "scale": 1.0,
            "left_right_padding_points": round((expected[0] - observed[0]) / 2.0, 4),
            "top_bottom_padding_points": round((expected[1] - observed[1]) / 2.0, 4),
        }
        if not _page_sizes_match(normalized_size, expected):
            event["pdf_size_check"] = "reject"
            raise PdfPageSizeMismatch(
                "Rejected PDF before printing: normalization did not produce selected media size "
                f"{_format_size(expected)}."
            )
        event["pdf_size_check"] = "normalized-to-media"
        return normalized_path

    event["pdf_size_check"] = "reject"
    raise PdfPageSizeMismatch(
        "Rejected PDF before printing: received page size "
        f"{_format_size(observed)} but selected media {settings.normalized_page_size!r} "
        f"expects {_format_size(expected)}. This cannot be safely padded without changing "
        "or clipping pattern geometry."
    )


def pdf_page_size_points(path: Path) -> tuple[float, float]:
    pdfinfo = os.environ.get("HPP_PDFINFO_COMMAND") or shutil.which("pdfinfo")
    if not pdfinfo:
        raise ConfigurationError("pdfinfo is required to validate PDF page size before printing")

    result = run_command([pdfinfo, "-f", "1", "-l", "1", str(path)])
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise InputRejected(f"Rejected PDF before printing: could not inspect page size with pdfinfo: {details}")
    return parse_pdfinfo_page_size(result.stdout)


def parse_pdfinfo_page_size(output: str) -> tuple[float, float]:
    match = PDFINFO_PAGE_SIZE_RE.search(output)
    if not match:
        raise InputRejected("Rejected PDF before printing: could not parse page size from pdfinfo output")
    return (float(match.group(1)), float(match.group(2)))


def normalized_pdf_path(input_path: Path, media: str) -> Path:
    safe_media = re.sub(r"[^A-Za-z0-9_.-]+", "-", media).strip("-") or "media"
    if input_path.name.startswith("hpp-airprint-"):
        with tempfile.NamedTemporaryFile(
            prefix="hpp-normalized-",
            suffix=f".{safe_media}.pdf",
            delete=False,
        ) as temp_file:
            return Path(temp_file.name)
    return input_path.with_name(f"{input_path.stem}.normalized-{safe_media}.pdf")


def pad_pdf_to_media(input_path: Path, output_path: Path, expected_size: tuple[float, float]) -> None:
    qpdf = find_qpdf_command()
    with tempfile.TemporaryDirectory(prefix="hpp-qpdf-") as temp_dir:
        temp_path = Path(temp_dir)
        json_path = temp_path / "input.json"
        modified_json_path = temp_path / "modified.json"

        json_result = run_command([qpdf, "--json-output", "--json-stream-data=none", str(input_path)])
        if json_result.returncode not in (0, 3) or not json_result.stdout.strip():
            details = json_result.stderr.strip() or json_result.stdout.strip() or f"exit status {json_result.returncode}"
            raise InputRejected(f"Rejected PDF before printing: could not read PDF structure with qpdf: {details}")

        json_path.write_text(json_result.stdout, encoding="utf-8")
        try:
            data = json.loads(json_result.stdout)
        except json.JSONDecodeError as exc:
            raise InputRejected(f"Rejected PDF before printing: qpdf returned invalid JSON: {exc}") from exc
        modified_pages = _pad_qpdf_page_boxes(data, expected_size)
        if modified_pages == 0:
            raise InputRejected("Rejected PDF before printing: could not find any PDF pages to normalize")
        modified_json_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        qpdf_result = run_command([qpdf, str(input_path), f"--update-from-json={modified_json_path}", str(output_path)])
        if qpdf_result.returncode not in (0, 3):
            details = qpdf_result.stderr.strip() or qpdf_result.stdout.strip() or f"exit status {qpdf_result.returncode}"
            raise InputRejected(f"Rejected PDF before printing: could not write normalized PDF with qpdf: {details}")


def find_qpdf_command() -> str:
    configured = os.environ.get("HPP_QPDF_COMMAND")
    if configured:
        return configured
    for candidate in ("qpdf", "/opt/homebrew/bin/qpdf", "/usr/local/bin/qpdf"):
        found = shutil.which(candidate) if "/" not in candidate else candidate
        if found and Path(found).exists():
            return found
    raise ConfigurationError("qpdf is required to pad AirPrint PDF pages to the selected media size")


def _pad_qpdf_page_boxes(data: dict[str, Any], expected_size: tuple[float, float]) -> int:
    objects = data.get("qpdf", [{}, {}])[1]
    modified_pages = 0
    for obj in objects.values():
        value = obj.get("value")
        if not isinstance(value, dict) or value.get("/Type") != "/Page":
            continue

        current_box = _box_values(value.get("/MediaBox") or value.get("/CropBox"))
        if current_box is None:
            current_box = [0.0, 0.0, expected_size[0], expected_size[1]]
        width = current_box[2] - current_box[0]
        height = current_box[3] - current_box[1]
        if width <= 0 or height <= 0:
            raise InputRejected(f"Rejected PDF before printing: invalid PDF page box {current_box}")
        if not _page_size_fits_inside((width, height), expected_size):
            raise PdfPageSizeMismatch(
                "Rejected PDF before printing: page box "
                f"{_format_size((width, height))} cannot fit inside selected media {_format_size(expected_size)}."
            )

        left_pad = (expected_size[0] - width) / 2.0
        bottom_pad = (expected_size[1] - height) / 2.0
        new_box = [
            round(current_box[0] - left_pad, 4),
            round(current_box[1] - bottom_pad, 4),
            round(current_box[2] + (expected_size[0] - width - left_pad), 4),
            round(current_box[3] + (expected_size[1] - height - bottom_pad), 4),
        ]
        for box_name in PDF_PAGE_BOXES:
            value[box_name] = new_box
        modified_pages += 1
    return modified_pages


def _box_values(value: object) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    return [float(item) for item in value]


def normalized_content_type() -> str:
    return os.environ.get("CONTENT_TYPE", "application/pdf").split(";", 1)[0].strip()


def _materialize_input(argv: list[str]) -> Path:
    if argv:
        candidate = Path(argv[0])
        if candidate.is_file():
            return candidate

    content_type = normalized_content_type()
    suffix = SUFFIX_BY_CONTENT_TYPE.get(content_type, ".print")
    with tempfile.NamedTemporaryFile(prefix="hpp-airprint-", suffix=suffix, delete=False) as temp_file:
        while True:
            chunk = sys.stdin.buffer.read(1024 * 1024)
            if not chunk:
                break
            temp_file.write(chunk)
        return Path(temp_file.name)


def _positive_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _remove_temp(path: Path) -> None:
    if path.name.startswith("hpp-airprint-"):
        path.unlink(missing_ok=True)


def ensure_upstream_available(destination: str, event: dict[str, Any]) -> None:
    if os.environ.get("HPP_SKIP_UPSTREAM_CHECK") == "1":
        event["upstream_check"] = "disabled"
        return

    lpstat = os.environ.get("HPP_LPSTAT_COMMAND") or shutil.which("lpstat") or "/usr/bin/lpstat"
    checks = {
        "device": run_command([lpstat, "-v", destination]),
        "accepting": run_command([lpstat, "-a", destination]),
        "printer": run_command([lpstat, "-p", destination, "-l"]),
    }
    event["upstream_status"] = {
        name: {
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
        }
        for name, result in checks.items()
    }

    device = checks["device"]
    accepting = checks["accepting"]
    printer = checks["printer"]
    if device.returncode != 0:
        raise UpstreamUnavailable(
            f"Upstream CUPS destination {destination!r} is not available: "
            f"{_command_details(device)}"
        )
    if accepting.returncode != 0 or " accepting requests " not in f" {accepting.stdout} ":
        raise UpstreamUnavailable(
            f"Upstream CUPS destination {destination!r} is not accepting requests: "
            f"{_command_details(accepting)}"
        )
    printer_text = f"{printer.stdout}\n{printer.stderr}".lower()
    if printer.returncode != 0 or " disabled " in f" {printer_text} " or "not accepting" in printer_text:
        raise UpstreamUnavailable(
            f"Upstream CUPS destination {destination!r} is disabled or unavailable: "
            f"{_command_details(printer)}"
        )
    event["upstream_check"] = "pass"


def run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=command_timeout_seconds(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ConfigurationError(
            f"Command timed out after {command_timeout_seconds():.1f}s: {shlex.join(command)}"
        ) from exc


def command_timeout_seconds() -> float:
    value = os.environ.get("HPP_COMMAND_TIMEOUT_SECONDS")
    if not value:
        return DEFAULT_COMMAND_TIMEOUT_SECONDS
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ConfigurationError(f"HPP_COMMAND_TIMEOUT_SECONDS must be numeric, got {value!r}") from exc
    if parsed <= 0:
        raise ConfigurationError("HPP_COMMAND_TIMEOUT_SECONDS must be greater than zero")
    return parsed


def _command_details(result: subprocess.CompletedProcess[str]) -> str:
    return result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"


def _page_sizes_match(observed: tuple[float, float], expected: tuple[float, float]) -> bool:
    return _same_orientation_match(observed, expected) or _same_orientation_match(
        (observed[1], observed[0]), expected
    )


def _can_pad_to_expected_media(observed: tuple[float, float], expected: tuple[float, float]) -> bool:
    return _page_size_fits_inside(observed, expected) or _page_size_fits_inside(
        (observed[1], observed[0]), expected
    )


def _page_size_fits_inside(observed: tuple[float, float], expected: tuple[float, float]) -> bool:
    return (
        observed[0] <= expected[0] + PDF_SIZE_TOLERANCE_POINTS
        and observed[1] <= expected[1] + PDF_SIZE_TOLERANCE_POINTS
    )


def _same_orientation_match(observed: tuple[float, float], expected: tuple[float, float]) -> bool:
    return (
        abs(observed[0] - expected[0]) <= PDF_SIZE_TOLERANCE_POINTS
        and abs(observed[1] - expected[1]) <= PDF_SIZE_TOLERANCE_POINTS
    )


def _round_inches(points: float) -> float:
    return round(points / 72.0, 4)


def _format_size(size: tuple[float, float]) -> str:
    return (
        f"{size[0]:.2f} x {size[1]:.2f} pt "
        f"({_round_inches(size[0]):.4g} x {_round_inches(size[1]):.4g} in)"
    )


def write_job_log(event: dict[str, Any]) -> None:
    job_log = os.environ.get("HPP_JOB_LOG")
    if not job_log:
        return

    path = Path(job_log)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    except OSError as exc:
        _log_error(f"Could not write job log {path}: {exc}")


def _log_info(message: str) -> None:
    _emit_log("INFO", message)


def _log_error(message: str) -> None:
    _emit_log("ERROR", message)


def _emit_log(level: str, message: str) -> None:
    try:
        for line in message.splitlines():
            print(f"{level}: {line}", file=sys.stderr)
    except OSError:
        # A closed diagnostic stream must never change print-job status.
        return


if __name__ == "__main__":
    raise SystemExit(main())
