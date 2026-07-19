from __future__ import annotations

import json
import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path, PurePath
from unittest.mock import patch

from hundred_percent_print.forward_job import (
    _log_info,
    build_forward_command,
    main,
    parse_pdfinfo_page_size,
    prepare_input_for_forwarding,
)
from hundred_percent_print.options import PrintSettings


class ForwardJobTests(unittest.TestCase):
    def test_logging_pipe_failure_does_not_fail_the_job(self) -> None:
        closed_stream = io.StringIO()
        closed_stream.close()
        with patch("sys.stderr", closed_stream):
            _log_info("Forwarding dry-run job")

    def test_forward_command_uses_locked_options(self) -> None:
        command = build_forward_command(
            PrintSettings(upstream_queue="Canon_TR150_series", media="Letter"),
            Path("/tmp/pattern.pdf"),
        )

        self.assertEqual(PurePath(command[0]).name, "lp")
        self.assertEqual(command[1:3], ["-d", "Canon_TR150_series"])
        self.assertIn("print-scaling=none", command)
        self.assertIn("fit-to-page=false", command)
        self.assertEqual(command[-1], "/tmp/pattern.pdf")

    def test_forwarder_dry_run_writes_structured_job_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = temp_path / "pattern.pdf"
            document.write_bytes(b"%PDF-1.4\n")
            job_log = temp_path / "jobs.jsonl"

            with patch.dict(
                "os.environ",
                {
                    "HPP_UPSTREAM_QUEUE": "Canon_TR150_series",
                    "HPP_MEDIA": "Letter",
                    "HPP_DRY_RUN": "1",
                    "HPP_JOB_LOG": str(job_log),
                    "CONTENT_TYPE": "application/pdf",
                    "IPP_JOB_NAME": "Pattern Test",
                    "IPP_COPIES": "2",
                },
                clear=True,
            ):
                with patch(
                    "hundred_percent_print.forward_job.pdf_page_size_points",
                    return_value=(612.0, 792.0),
                ), redirect_stderr(io.StringIO()):
                    self.assertEqual(main([str(document)]), 0)

            event = json.loads(job_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["status"], "dry-run")
            self.assertTrue(event["dry_run"])
            self.assertEqual(event["upstream_queue"], "Canon_TR150_series")
            self.assertEqual(event["media"], "Letter")
            self.assertEqual(event["page_size"], "Letter")
            self.assertEqual(event["content_type"], "application/pdf")
            self.assertEqual(event["copies"], 2)
            self.assertEqual(event["pdf_size_check"], "pass")
            self.assertEqual(event["pdf_page_size_inches"], [8.5, 11.0])
            self.assertEqual(event["upstream_check"], "skipped-dry-run")
            self.assertRegex(event["source_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(event["source_sha256"], event["submitted_sha256"])
            self.assertIn("print-scaling=none", event["command"])
            self.assertIn("Pattern Test", event["command"])

    def test_forwarder_pads_printable_area_pdf_before_forwarding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = temp_path / "pattern.pdf"
            document.write_bytes(b"%PDF-1.4\n")
            job_log = temp_path / "jobs.jsonl"

            with patch.dict(
                "os.environ",
                {
                    "HPP_UPSTREAM_QUEUE": "Canon_TR150_series",
                    "HPP_MEDIA": "Letter",
                    "HPP_DRY_RUN": "1",
                    "HPP_JOB_LOG": str(job_log),
                    "CONTENT_TYPE": "application/pdf",
                    "IPP_JOB_NAME": "Pattern Test",
                },
                clear=True,
            ):
                with patch(
                    "hundred_percent_print.forward_job.pdf_page_size_points",
                    side_effect=[(540.0, 720.0), (612.0, 792.0)],
                ), patch(
                    "hundred_percent_print.forward_job.pad_pdf_to_media",
                    side_effect=lambda _input, output, _expected: Path(output).write_bytes(b"%PDF-1.4 normalized\n"),
                ), redirect_stderr(io.StringIO()) as stderr:
                    self.assertEqual(main([str(document)]), 0)

            event = json.loads(job_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["status"], "dry-run")
            self.assertEqual(event["pdf_size_check"], "normalized-to-media")
            self.assertEqual(event["pdf_page_size_inches"], [7.5, 10.0])
            self.assertEqual(event["expected_page_size_inches"], [8.5, 11.0])
            self.assertEqual(event["normalized_page_size_inches"], [8.5, 11.0])
            self.assertEqual(event["pdf_normalization"]["scale"], 1.0)
            self.assertIn(".normalized-Letter.pdf", event["submitted_file"])
            self.assertRegex(event["submitted_sha256"], r"^[0-9a-f]{64}$")
            self.assertIn("print-scaling=none", event["command"])
            self.assertIn("Dry run enabled", stderr.getvalue())

    def test_forwarder_rejects_empty_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = temp_path / "pattern.pdf"
            document.write_bytes(b"")
            job_log = temp_path / "jobs.jsonl"

            with patch.dict(
                "os.environ",
                {
                    "HPP_UPSTREAM_QUEUE": "Canon_TR150_series",
                    "HPP_MEDIA": "Letter",
                    "HPP_DRY_RUN": "1",
                    "HPP_JOB_LOG": str(job_log),
                    "CONTENT_TYPE": "application/pdf",
                },
                clear=True,
            ):
                with redirect_stderr(io.StringIO()) as stderr:
                    self.assertEqual(main([str(document)]), 1)

            event = json.loads(job_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["status"], "rejected-input")
            self.assertIn("empty document", event["error"])
            self.assertIn("empty document", stderr.getvalue())

    def test_forwarder_rejects_pdf_without_header(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = temp_path / "pattern.pdf"
            document.write_bytes(b"not a pdf")
            job_log = temp_path / "jobs.jsonl"

            with patch.dict(
                "os.environ",
                {
                    "HPP_UPSTREAM_QUEUE": "Canon_TR150_series",
                    "HPP_MEDIA": "Letter",
                    "HPP_DRY_RUN": "1",
                    "HPP_JOB_LOG": str(job_log),
                    "CONTENT_TYPE": "application/pdf",
                },
                clear=True,
            ):
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(main([str(document)]), 1)

            event = json.loads(job_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["status"], "rejected-input")
            self.assertIn("missing PDF header", event["error"])

    def test_forwarder_rejects_malformed_pdf_reported_by_pdfinfo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = temp_path / "pattern.pdf"
            document.write_bytes(b"%PDF-1.4\nbroken")
            job_log = temp_path / "jobs.jsonl"

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                self.assertEqual(command[0], "/tmp/pdfinfo")
                return subprocess.CompletedProcess(command, 1, "", "Syntax Error: Couldn't read xref table")

            with patch.dict(
                "os.environ",
                {
                    "HPP_UPSTREAM_QUEUE": "Canon_TR150_series",
                    "HPP_MEDIA": "Letter",
                    "HPP_DRY_RUN": "1",
                    "HPP_JOB_LOG": str(job_log),
                    "CONTENT_TYPE": "application/pdf",
                    "HPP_PDFINFO_COMMAND": "/tmp/pdfinfo",
                },
                clear=True,
            ):
                with patch("subprocess.run", side_effect=fake_run), redirect_stderr(io.StringIO()):
                    self.assertEqual(main([str(document)]), 1)

            event = json.loads(job_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["status"], "rejected-input")
            self.assertIn("could not inspect page size", event["error"])
            self.assertIn("Syntax Error", event["error"])

    def test_forwarder_reports_helper_timeout_as_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = temp_path / "pattern.pdf"
            document.write_bytes(b"%PDF-1.4\n")
            job_log = temp_path / "jobs.jsonl"

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                raise subprocess.TimeoutExpired(command, timeout=0.1)

            with patch.dict(
                "os.environ",
                {
                    "HPP_UPSTREAM_QUEUE": "Canon_TR150_series",
                    "HPP_MEDIA": "Letter",
                    "HPP_DRY_RUN": "1",
                    "HPP_JOB_LOG": str(job_log),
                    "CONTENT_TYPE": "application/pdf",
                    "HPP_PDFINFO_COMMAND": "/tmp/pdfinfo",
                    "HPP_COMMAND_TIMEOUT_SECONDS": "0.1",
                },
                clear=True,
            ):
                with patch("subprocess.run", side_effect=fake_run), redirect_stderr(io.StringIO()):
                    self.assertEqual(main([str(document)]), 1)

            event = json.loads(job_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["status"], "configuration-error")
            self.assertIn("Command timed out", event["error"])

    def test_forwarder_rejects_non_pdf_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = temp_path / "pattern.jpg"
            document.write_bytes(b"\xff\xd8not actually scale-safe")
            job_log = temp_path / "jobs.jsonl"

            with patch.dict(
                "os.environ",
                {
                    "HPP_UPSTREAM_QUEUE": "Canon_TR150_series",
                    "HPP_MEDIA": "Letter",
                    "HPP_DRY_RUN": "1",
                    "HPP_JOB_LOG": str(job_log),
                    "CONTENT_TYPE": "image/jpeg",
                },
                clear=True,
            ):
                with redirect_stderr(io.StringIO()):
                    self.assertEqual(main([str(document)]), 1)

            event = json.loads(job_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["status"], "rejected-input")
            self.assertEqual(event["pdf_size_check"], "rejected-non-pdf")
            self.assertIn("cannot be scale-verified", event["error"])

    def test_forwarder_reports_unavailable_upstream_before_lp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = temp_path / "pattern.pdf"
            document.write_bytes(b"%PDF-1.4\n")
            job_log = temp_path / "jobs.jsonl"
            calls: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                calls.append(command)
                if command[:2] == ["/tmp/lpstat", "-v"]:
                    return subprocess.CompletedProcess(command, 1, "", "unknown printer")
                return subprocess.CompletedProcess(command, 0, f"{command[-1]} accepting requests since now", "")

            with patch.dict(
                "os.environ",
                {
                    "HPP_UPSTREAM_QUEUE": "Missing_Printer",
                    "HPP_MEDIA": "Letter",
                    "HPP_JOB_LOG": str(job_log),
                    "CONTENT_TYPE": "application/pdf",
                    "HPP_LP_COMMAND": "/tmp/lp",
                    "HPP_LPSTAT_COMMAND": "/tmp/lpstat",
                },
                clear=True,
            ):
                with patch(
                    "hundred_percent_print.forward_job.pdf_page_size_points",
                    return_value=(612.0, 792.0),
                ), patch("subprocess.run", side_effect=fake_run), redirect_stderr(io.StringIO()):
                    self.assertEqual(main([str(document)]), 1)

            event = json.loads(job_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["status"], "upstream-unavailable")
            self.assertIn("Missing_Printer", event["error"])
            self.assertEqual(event["upstream_status"]["device"]["returncode"], 1)
            self.assertTrue(calls)
            self.assertFalse(any(command and command[0] == "/tmp/lp" for command in calls))

    def test_forwarder_logs_lp_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = temp_path / "pattern.pdf"
            document.write_bytes(b"%PDF-1.4\n")
            job_log = temp_path / "jobs.jsonl"

            def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                if command[:2] == ["/tmp/lpstat", "-v"]:
                    return subprocess.CompletedProcess(command, 0, "device for Canon_TR150_series: ipp://printer", "")
                if command[:2] == ["/tmp/lpstat", "-a"]:
                    return subprocess.CompletedProcess(command, 0, "Canon_TR150_series accepting requests since now", "")
                if command[:2] == ["/tmp/lpstat", "-p"]:
                    return subprocess.CompletedProcess(command, 0, "printer Canon_TR150_series is idle. enabled since now", "")
                if command and command[0] == "/tmp/lp":
                    return subprocess.CompletedProcess(command, 1, "", "printer offline")
                raise AssertionError(f"unexpected command: {command}")

            with patch.dict(
                "os.environ",
                {
                    "HPP_UPSTREAM_QUEUE": "Canon_TR150_series",
                    "HPP_MEDIA": "Letter",
                    "HPP_JOB_LOG": str(job_log),
                    "CONTENT_TYPE": "application/pdf",
                    "HPP_LP_COMMAND": "/tmp/lp",
                    "HPP_LPSTAT_COMMAND": "/tmp/lpstat",
                },
                clear=True,
            ):
                with patch(
                    "hundred_percent_print.forward_job.pdf_page_size_points",
                    return_value=(612.0, 792.0),
                ), patch("subprocess.run", side_effect=fake_run), redirect_stderr(io.StringIO()):
                    self.assertEqual(main([str(document)]), 1)

            event = json.loads(job_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["status"], "failed")
            self.assertEqual(event["upstream_check"], "pass")
            self.assertEqual(event["returncode"], 1)
            self.assertEqual(event["stderr"], "printer offline")

    def test_forwarder_rejects_pdf_that_cannot_fit_selected_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            document = temp_path / "pattern.pdf"
            document.write_bytes(b"%PDF-1.4\n")
            job_log = temp_path / "jobs.jsonl"

            with patch.dict(
                "os.environ",
                {
                    "HPP_UPSTREAM_QUEUE": "Canon_TR150_series",
                    "HPP_MEDIA": "Letter",
                    "HPP_DRY_RUN": "1",
                    "HPP_JOB_LOG": str(job_log),
                    "CONTENT_TYPE": "application/pdf",
                },
                clear=True,
            ):
                with patch(
                    "hundred_percent_print.forward_job.pdf_page_size_points",
                    return_value=(700.0, 900.0),
                ), redirect_stderr(io.StringIO()) as stderr:
                    self.assertEqual(main([str(document)]), 1)

            event = json.loads(job_log.read_text(encoding="utf-8").strip())
            self.assertEqual(event["status"], "rejected-page-size")
            self.assertEqual(event["pdf_size_check"], "reject")
            self.assertIn("cannot be safely padded", event["error"])
            self.assertIn("Rejected PDF before printing", stderr.getvalue())

    def test_prepare_input_for_forwarding_preserves_exact_pdf_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            document = Path(temp_dir) / "pattern.pdf"
            document.write_bytes(b"%PDF-1.4\n")
            event = {"content_type": "application/pdf"}

            with patch(
                "hundred_percent_print.forward_job.pdf_page_size_points",
                return_value=(612.0, 792.0),
            ):
                result = prepare_input_for_forwarding(
                    document,
                    PrintSettings(upstream_queue="Canon_TR150_series", media="Letter"),
                    event,
                )

            self.assertEqual(result, document)
            self.assertEqual(event["pdf_size_check"], "pass")

    def test_parse_pdfinfo_page_size_accepts_numbered_page_output(self) -> None:
        output = "Pages:           1\nPage    1 size:  612 x 792 pts (letter)\n"

        self.assertEqual(parse_pdfinfo_page_size(output), (612.0, 792.0))


if __name__ == "__main__":
    unittest.main()
