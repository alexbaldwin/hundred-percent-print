from __future__ import annotations

import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_docker_entrypoint_has_valid_shell_syntax(self) -> None:
        result = subprocess.run(
            ["sh", "-n", str(REPO_ROOT / "docker" / "entrypoint.sh")],
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dockerfile_contains_required_runtime_helpers(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

        for package in (
            "avahi-daemon",
            "cups",
            "cups-ipp-utils",
            "poppler-utils",
            "qpdf",
            "tini",
        ):
            self.assertIn(package, dockerfile)

    def test_synology_macvlan_template_persists_data_and_uses_lan_ip(self) -> None:
        compose = (REPO_ROOT / "deploy" / "compose.synology-macvlan.yml").read_text(encoding="utf-8")

        self.assertIn("driver: macvlan", compose)
        self.assertIn("ipv4_address:", compose)
        self.assertIn("HPP_UPSTREAM_DEVICE_URI", compose)
        self.assertIn("./data:/data", compose)
        self.assertIn("ghcr.io/alexbaldwin/hundred-percent-print:latest", compose)
        self.assertNotIn("build:", compose)

    def test_synology_host_mode_recovers_from_offline_printer_and_stale_avahi(self) -> None:
        compose = (REPO_ROOT / "deploy" / "compose.synology-host.yml").read_text(encoding="utf-8")
        entrypoint = (REPO_ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("network_mode: host", compose)
        self.assertIn('HPP_ALLOW_OFFLINE_START: "1"', compose)
        self.assertIn('HPP_START_AVAHI: "0"', compose)
        self.assertIn("/run/dbus/system_bus_socket:/run/dbus/system_bus_socket:ro", compose)
        self.assertIn("retry_upstream_queue", entrypoint)
        self.assertIn("rm -f /run/avahi-daemon/pid", entrypoint)
        self.assertIn("require_positive_integer HPP_UPSTREAM_RETRY_SECONDS", entrypoint)
        self.assertIn("require_positive_integer HPP_UPSTREAM_SETUP_TIMEOUT", entrypoint)

    def test_publish_workflow_builds_intel_and_arm_images(self) -> None:
        workflow = (REPO_ROOT / ".github" / "workflows" / "publish-container.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("packages: write", workflow)
        self.assertIn("linux/amd64,linux/arm64", workflow)
        self.assertIn("docker/build-push-action@v7", workflow)

    def test_synology_docs_warn_against_public_exposure(self) -> None:
        docs = (REPO_ROOT / "docs" / "SYNOLOGY.md").read_text(encoding="utf-8")

        self.assertIn("Do not publish container port 631 to the internet", docs)
        self.assertIn("HPP_FORWARD_DRY_RUN=1", docs)
        self.assertIn("/data/jobs.jsonl", docs)


if __name__ == "__main__":
    unittest.main()
