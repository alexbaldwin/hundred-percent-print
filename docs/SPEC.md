# Hundred Percent Print Specification

## Problem

Fabric pattern PDFs depend on physical dimensions. Printing through iOS AirPrint directly to the Canon TR150 can silently apply automatic scaling, which makes printed pattern pieces unsafe to use even when the print preview looks correct.

Local investigation confirmed the Canon TR150 direct AirPrint endpoint advertises `print-scaling-default=auto`. It also advertises `print-scaling-supported` with `none`, but a direct IPP `Validate-Job` request containing `print-scaling=none` returned `successful-ok-ignored-or-substituted-attributes` with the message `Unsupported print-scaling`. The macOS CUPS queue for the same printer accepts `print-scaling=none`.

## Goal

Provide a local AirPrint-compatible server that iOS can print to, then forward every job through a controlled CUPS queue with exact-scale options applied at submission time.

The user-facing printer should be named `100 Percent Pattern Print` by default. Users must choose this proxy printer instead of the Canon printer's built-in AirPrint entry.

The supported iOS-facing architecture is a CUPS-shared front-end queue published through an explicit AirPrint `_universal` DNS-SD advertisement and pointed at a private local `ippeveprinter` backend. This is required because a live iOS AirPrint test against the direct backend validated the PDF successfully, then failed on `Create-Job` with `client-error-bad-request (Unexpected document data following request.)` before any document was spooled or forwarded.

For Synology/NAS deployment, the supported package is a Docker container that runs Linux CUPS, Avahi/mDNS, and the exact-scale forwarding server together. The container creates its own upstream CUPS queue from a configured Canon IPP device URI instead of relying on the Mac's local CUPS queue.

## Requirements

1. The server exposes a CUPS-shared local AirPrint printer using macOS CUPS tooling.
2. The server enables CUPS printer sharing and remote CUPS access so iOS can reach the CUPS queue.
3. The server publishes an explicit `_ipp._tcp,_universal` and `_ipps._tcp,_universal` AirPrint DNS-SD service named `100 Percent Pattern Print SAFE`.
4. The CUPS front-end queue points at a private local backend that does not advertise itself via Bonjour in the default mode.
5. Every forwarded job is submitted to the upstream CUPS destination with scale-locking options:
   - `print-scaling=none`
   - `fit-to-page=false`
   - `scaling=100`
   - `natural-scaling=100`
   - `number-up=1`
   - `sides=one-sided`
   - explicit media and page size
6. The forwarding path supports dry-run mode so validation can exercise the server without printing paper.
7. The forwarding path writes structured job logs when configured.
8. The forwarding path can preserve received spool documents for failure recovery.
9. The forwarding path validates PDF page size before forwarding.
10. If an incoming PDF is smaller than the selected media but fits inside it, the forwarding path pads the PDF page boxes to the selected media without scaling, rasterizing, or changing content geometry.
11. If an incoming PDF is larger than the selected media or cannot be safely padded, the forwarding path rejects it before forwarding.
12. The forwarding path rejects empty documents, unsupported content types, malformed PDFs, and non-PDF documents unless explicitly overridden for diagnostics.
13. The forwarding path checks that the upstream CUPS destination exists, is enabled, and is accepting requests before real Canon submission.
14. The forwarding path records source and submitted document hashes so preserved spool files can be matched back to job-log entries.
15. Helper commands used by the forwarder have bounded timeouts so one bad helper call cannot hang a job indefinitely.
16. The CLI can generate a calibration PDF with inch and metric measurement marks.
17. The CLI can print local files through the same exact-scale option set.
18. The CLI can run an end-to-end self-test by starting the local server, submitting a calibration job, verifying the forwarder observed it, and shutting the server down.
19. The CLI can run a no-paper CUPS front-end self-test that exercises the iOS-compatible queue architecture.
20. The CLI can generate a macOS LaunchAgent plist for long-running use.
21. The README documents the normal workflow, calibration workflow, and what not to rely on.
22. The project provides a Docker image for Linux/Synology deployment with all required runtime helpers.
23. The Docker entrypoint starts D-Bus, Avahi, CUPS, configures the upstream CUPS queue from `HPP_UPSTREAM_DEVICE_URI`, and fails before advertising if the upstream queue cannot be configured.
24. The Docker deployment persists structured job logs, preserved spool files, and CUPS logs under `/data`.
25. The Synology deployment guide documents macvlan networking as the preferred AirPrint/mDNS path and host networking as a fallback.

## Non-Goals

1. The project does not change Canon firmware defaults.
2. The project does not make direct iOS-to-Canon AirPrint safe.
3. The project does not remove the need for paper calibration before using pattern output.
4. The project does not guarantee borderless/full-bleed output is dimensionally safe.
5. The project does not expose a public internet print service. It is intended for trusted LAN deployment only.

## Default Workflow

1. Start the proxy:

   ```sh
   ./scripts/hundred-percent-print serve --upstream Canon_TR150_series --media Letter --keep-spool
   ```

2. On iPhone or iPad, print to `100 Percent Pattern Print SAFE`.
3. First print the calibration PDF.
4. Measure the marks:
   - 1 inch square must measure 1.000 inch.
   - 100 mm square must measure 100.0 mm.
   - 6 inch ruler must measure 6.000 inches.
5. Print pattern PDFs only after calibration passes.

## Acceptance Criteria

1. `./scripts/hundred-percent-print self-test --upstream Canon_TR150_series` submits a job to the local proxy in dry-run mode and exits successfully without sending paper to the Canon printer.
2. Unit tests verify exact-scale options, forwarder behavior, calibration PDF generation, server command construction, and LaunchAgent generation.
3. The server dry-run output shows all environment variables that influence forwarding.
4. The job log contains one JSON object per forwarded job with upstream queue, media, page size, content type, source file, input size, dry-run state, command, and result.
5. If the upstream CUPS destination does not exist, `serve` and `self-test` fail before starting the AirPrint server.
6. `./scripts/hundred-percent-print self-test --mode cups --upstream Canon_TR150_series` creates a temporary CUPS front-end queue, submits a generated calibration PDF through that queue, records a dry-run backend job with `print-scaling=none`, removes the temporary queue, and exits without sending paper to the Canon printer.
7. A dry-run live-stack verification can submit a calibration PDF through `Hundred_Percent_Patterns`, preserve the backend PDF, write a dry-run job-log entry containing `print-scaling=none`, and leave the Canon queue with no active job.
8. Bonjour browsing for `_ipp._tcp,_universal` lists `100 Percent Pattern Print SAFE`, and resolving it points to port `631` with `rp=printers/Hundred_Percent_Patterns`.
9. A PDF received as 7.5 x 10 inches while the selected media is Letter is normalized to an 8.5 x 11 inch PDF before forwarding, with `pdf_size_check=normalized-to-media` and `pdf_normalization.scale=1.0` in the job log.
10. A PDF that cannot fit inside the selected media is rejected before forwarding, with `status=rejected-page-size` in the job log.
11. Empty documents, missing PDF headers, corrupt PDFs, and non-PDF inputs are rejected before forwarding, with `status=rejected-input` in the job log.
12. If the upstream CUPS destination is missing, disabled, or not accepting requests, the job is not submitted to `lp` and the job log records `status=upstream-unavailable`.
13. If `lp` itself rejects a real job, the job log records `status=failed`, `returncode`, `stdout`, and `stderr`.
14. `sh -n docker/entrypoint.sh` passes.
15. The unit test suite verifies the Docker/Synology packaging files expected for NAS deployment.

## Operational Notes

The most important operational signal is the job log. A healthy job log entry has `returncode=0`, `dry_run=false` for real printing, `upstream_check=pass`, and a command containing `-o print-scaling=none`.

The second critical signal is `pdf_size_check`. For a Letter pattern job, the forwarder must log either `pdf_size_check=pass` with `pdf_page_size_inches=[8.5, 11.0]`, or `pdf_size_check=normalized-to-media` with `pdf_normalization.scale=1.0` and `normalized_page_size_inches=[8.5, 11.0]`. If iOS or another AirPrint client sends a smaller printable-area PDF, the forwarder pads it onto the selected media without scaling. If the PDF cannot fit inside the selected media, the forwarder logs `status=rejected-page-size` and does not submit the job to the Canon queue.

Failure statuses are intentionally specific:

- `rejected-input`: empty input, unsupported content type, non-PDF document, missing PDF header, or corrupt PDF.
- `rejected-page-size`: valid PDF, but its page boxes cannot be safely padded to the selected media.
- `upstream-unavailable`: CUPS destination does not exist, is disabled, or is not accepting requests.
- `configuration-error`: required local helper tools are missing or a helper command times out.
- `failed`: the final `lp` command ran but returned a nonzero status.

For validation without paper, use `--forward-dry-run`. For production printing, omit it.

Failures before a document reaches the private backend will not have a job-log entry. Inspect the raw server stderr log for IPP request/response details and CUPS errors for front-end queue failures. For diagnostic runs, use `--keep-spool` so any document that reaches the backend remains available for inspection or manual recovery.
