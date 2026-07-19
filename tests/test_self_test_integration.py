from __future__ import annotations

import os
import subprocess
import unittest


class SelfTestIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("HPP_RUN_INTEGRATION") == "1",
        "set HPP_RUN_INTEGRATION=1 to run the local AirPrint self-test",
    )
    def test_self_test_command_runs_without_printing(self) -> None:
        result = subprocess.run(
            [
                "./scripts/hundred-percent-print",
                "self-test",
                "--upstream",
                os.environ.get("HPP_TEST_UPSTREAM", "Canon_TR150_series"),
                "--media",
                "Letter",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Self-test passed", result.stdout)
        self.assertIn("print-scaling=none", result.stdout)

    @unittest.skipUnless(
        os.environ.get("HPP_RUN_CUPS_FRONTEND_INTEGRATION") == "1",
        "set HPP_RUN_CUPS_FRONTEND_INTEGRATION=1 to run the CUPS front-end self-test",
    )
    def test_cups_frontend_self_test_command_runs_without_printing(self) -> None:
        result = subprocess.run(
            [
                "./scripts/hundred-percent-print",
                "self-test",
                "--mode",
                "cups",
                "--upstream",
                os.environ.get("HPP_TEST_UPSTREAM", "Canon_TR150_series"),
                "--media",
                "Letter",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Self-test passed through CUPS queue", result.stdout)
        self.assertIn("print-scaling=none", result.stdout)


if __name__ == "__main__":
    unittest.main()
