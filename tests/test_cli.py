from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path, PurePath
from unittest.mock import patch

from hundred_percent_print.cli import (
    airprint_advertise_commands,
    airprint_txt_records,
    cups_frontend_commands,
    env_for_settings,
    ippeveprinter_command,
    launch_agent_plist,
)
from hundred_percent_print.options import PrintSettings


class CliTests(unittest.TestCase):
    def test_env_for_settings_includes_dry_run_and_job_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            job_log = Path(temp_dir) / "jobs.jsonl"
            env = env_for_settings(
                PrintSettings(upstream_queue="Canon_TR150_series", media="Letter"),
                forward_dry_run=True,
                job_log=job_log,
            )

        self.assertEqual(env["HPP_UPSTREAM_QUEUE"], "Canon_TR150_series")
        self.assertEqual(env["HPP_MEDIA"], "Letter")
        self.assertEqual(env["HPP_PAGE_SIZE"], "Letter")
        self.assertEqual(env["HPP_DRY_RUN"], "1")
        self.assertTrue(env["HPP_JOB_LOG"].endswith("jobs.jsonl"))

    def test_ippeveprinter_command_exposes_airprint_server(self) -> None:
        args = argparse.Namespace(
            verbose=1,
            spool=Path("/tmp/hpp-spool"),
            port=8631,
            keep_spool=False,
            name="100 Percent Pattern Print",
            backend_name="Hundred Percent Print Private Backend",
        )

        command = ippeveprinter_command(args, Path("/tmp/hpp-forward-job"))

        self.assertEqual(PurePath(command[0]).name, "ippeveprinter")
        self.assertIn("--no-web-forms", command)
        self.assertIn("-c", command)
        self.assertIn("/tmp/hpp-forward-job", command)
        self.assertIn("application/pdf,image/jpeg,image/png,image/pwg-raster,image/urf", command)
        self.assertEqual(command[-1], "100 Percent Pattern Print")

    def test_ippeveprinter_backend_can_disable_bonjour_for_cups_frontend(self) -> None:
        args = argparse.Namespace(
            verbose=1,
            spool=Path("/tmp/hpp-spool"),
            port=8631,
            keep_spool=False,
            name="100 Percent Pattern Print",
            backend_name="Hundred Percent Print Private Backend",
        )

        command = ippeveprinter_command(args, Path("/tmp/hpp-forward-job"), advertise=False)

        subtype_index = command.index("-r") + 1
        self.assertEqual(command[subtype_index], "off")
        self.assertEqual(command[-1], "Hundred Percent Print Private Backend")

    def test_cups_frontend_commands_create_shared_queue_to_private_backend(self) -> None:
        commands = cups_frontend_commands(
            "Hundred_Percent_Patterns",
            "100 Percent Pattern Print",
            8631,
            PrintSettings(upstream_queue="Canon_TR150_series", media="Letter"),
        )

        self.assertEqual(commands[0], ["cupsctl", "--share-printers", "--remote-any"])
        lpadmin = commands[1]
        self.assertIn("Hundred_Percent_Patterns", lpadmin)
        self.assertIn("ipp://127.0.0.1:8631/ipp/print", lpadmin)
        self.assertIn("printer-is-shared=true", lpadmin)
        self.assertIn("print-scaling-default=none", lpadmin)
        lpoptions = commands[2]
        self.assertEqual(lpoptions[:3], ["lpoptions", "-p", "Hundred_Percent_Patterns"])
        self.assertIn("print-scaling=none", lpoptions)

    def test_airprint_advertisement_points_to_cups_queue(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/dns-sd"):
            commands = airprint_advertise_commands("100 Percent Pattern Print SAFE", "Hundred_Percent_Patterns")

        self.assertEqual(len(commands), 2)
        self.assertIn("_ipp._tcp,_universal", commands[0])
        self.assertIn("_ipps._tcp,_universal", commands[1])
        self.assertIn("631", commands[0])
        self.assertIn("rp=printers/Hundred_Percent_Patterns", commands[0])
        self.assertIn("URF=V1.4,CP1,PQ3-5,RS300-600,SRGB24,W8,OB9,OFU0,IS1", commands[0])

        txt = airprint_txt_records("Hundred_Percent_Patterns")
        self.assertIn("pdl=application/octet-stream,application/pdf,image/jpeg,image/urf,image/pwg-raster", txt)
        self.assertIn("Color=T", txt)
        self.assertIn("Duplex=F", txt)

    def test_airprint_advertisement_uses_avahi_when_dns_sd_is_unavailable(self) -> None:
        def which(command: str) -> str | None:
            if command == "avahi-publish-service":
                return "/usr/bin/avahi-publish-service"
            return None

        with patch("shutil.which", side_effect=which):
            commands = airprint_advertise_commands("100 Percent Pattern Print SAFE", "Hundred_Percent_Patterns")

        self.assertEqual(commands[0][:4], ["/usr/bin/avahi-publish-service", "--no-fail", "--subtype=_universal._sub._ipp._tcp", "100 Percent Pattern Print SAFE"])
        self.assertIn("_ipp._tcp", commands[0])
        self.assertIn("631", commands[0])
        self.assertIn("rp=printers/Hundred_Percent_Patterns", commands[0])
        self.assertIn("--subtype=_universal._sub._ipps._tcp", commands[1])

    def test_launch_agent_runs_server_with_job_log(self) -> None:
        args = argparse.Namespace(
            label="com.hundred-percent-print.server",
            upstream="Canon_TR150_series",
            name="100 Percent Pattern Print",
            mode="cups",
            cups_frontend_queue="Hundred_Percent_Patterns",
            backend_name="Hundred Percent Print Private Backend",
            airprint_name="100 Percent Pattern Print SAFE",
            no_airprint_advertise=False,
            port=8631,
            spool=Path("/tmp/hpp-spool"),
            job_log=Path("/tmp/hpp/jobs.jsonl"),
            media="Letter",
            page_size=None,
            color_model="RGB",
            quality="High",
            media_type="auto",
            resolution=None,
            extra_options=None,
        )

        plist = launch_agent_plist(args)
        program_args = plist["ProgramArguments"]

        self.assertEqual(plist["Label"], "com.hundred-percent-print.server")
        self.assertIn("serve", program_args)
        self.assertIn("--mode", program_args)
        self.assertIn("cups", program_args)
        self.assertIn("--cups-frontend-queue", program_args)
        self.assertIn("Hundred_Percent_Patterns", program_args)
        self.assertIn("--backend-name", program_args)
        self.assertIn("Hundred Percent Print Private Backend", program_args)
        self.assertIn("--airprint-name", program_args)
        self.assertIn("100 Percent Pattern Print SAFE", program_args)
        self.assertIn("--job-log", program_args)
        self.assertIn("/tmp/hpp/jobs.jsonl", program_args)
        self.assertIn("EnvironmentVariables", plist)


if __name__ == "__main__":
    unittest.main()
