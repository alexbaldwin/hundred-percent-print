# Hundred Percent Print

Local exact-scale AirPrint/CUPS forwarding for fabric pattern printing.

See [docs/SPEC.md](docs/SPEC.md) for the full product and technical spec.

The critical path is `hundred-percent-print serve`: it creates a CUPS-shared AirPrint front-end queue, publishes an explicit AirPrint `_universal` DNS-SD service for iOS, points that queue at a private local IPP backend, then forwards every received job to the real Canon CUPS destination with scale-locking options:

- `print-scaling=none`
- `fit-to-page=false`
- `scaling=100`
- `natural-scaling=100`
- `number-up=1`
- explicit media/page size

This is stricter than only changing printer defaults because the final forwarder resubmits every AirPrint job with the exact-scale options.

The Canon TR150 direct AirPrint endpoint on this network advertises `print-scaling-default=auto`. It also reports `Unsupported print-scaling` when `print-scaling=none` is sent directly to the printer. The Mac CUPS queue accepts `print-scaling=none`, so the reliable path is iOS -> local proxy -> Mac CUPS queue -> Canon printer.

The direct `ippeveprinter` path is not used as the default iOS-facing printer. A live iOS test reached the backend, successfully validated the PDF, then failed on `Create-Job` with `client-error-bad-request (Unexpected document data following request.)` before any document was spooled. The CUPS front end handles that iOS submission path correctly and then forwards to the private backend where scale-locking and logging happen.

The forwarder also validates incoming PDF page size before forwarding. For Letter media, the Canon-facing PDF must be 8.5 x 11 inches. If iOS has converted the job to a smaller printable-area PDF, the server pads it back onto a Letter page without scaling the content, then forwards the normalized PDF. If the job cannot be padded safely, the server rejects it with `status=rejected-page-size`.

The forwarder fails closed. Empty files, malformed PDFs, unsupported content types, and non-PDF documents are rejected before they reach the Canon queue. Real print jobs also preflight the upstream CUPS destination before running `lp`; if the Canon queue is missing, disabled, or not accepting requests, the job log records `status=upstream-unavailable` and no Canon job is queued.

## Quick Start

Run from this repo without installing into system Python:

```sh
./scripts/hundred-percent-print discover
```

If you want an installed command, use a virtual environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -e .
```

On this Mac, the detected Canon destination is likely:

```text
Canon_TR150_series
```

Run the no-paper self-test:

```sh
./scripts/hundred-percent-print self-test --upstream Canon_TR150_series --media Letter
```

This starts the private backend on a temporary port, submits a generated calibration PDF, verifies that the forwarder saw the job, and exits without sending paper to the Canon printer.

Run the full CUPS front-end self-test when you want to validate the iOS-compatible architecture without paper:

```sh
./scripts/hundred-percent-print self-test --mode cups --upstream Canon_TR150_series --media Letter
```

Run the local AirPrint proxy:

```sh
./scripts/hundred-percent-print serve --upstream Canon_TR150_series --media Letter
```

Keep that process running. From the iPhone/iPad/Mac AirPrint dialog, choose `100 Percent Pattern Print SAFE`, not the printer's built-in Canon AirPrint entry and not the private backend.

The default `serve` mode creates or updates the shared CUPS queue named:

```text
Hundred_Percent_Patterns
```

It advertises this AirPrint service:

```text
100 Percent Pattern Print SAFE
```

The private backend uses port `8799` by default. This intentionally avoids the earlier cached direct-backend endpoint on port `8631`.

Forwarded jobs are logged by default to:

```text
~/Library/Logs/hundred-percent-print/jobs.jsonl
```

For a safe live-server test that accepts AirPrint jobs but does not print:

```sh
./scripts/hundred-percent-print serve --upstream Canon_TR150_series --media Letter --forward-dry-run --keep-spool
```

During a failure, inspect both logs:

- `jobs.jsonl`: structured entries for documents that reached the private backend.
- `server.err.log`: raw IPP request/response logging, including failures before a document is spooled.

With `--keep-spool`, received documents are preserved in the configured spool directory for recovery and measurement. Job-log entries include `source_sha256` and `submitted_sha256` so the original received document and the Canon-facing document can be matched exactly.

If a job prints too small, inspect the preserved spool PDF first. A Letter job whose PDF is 7.5 x 10 inches has been converted to printable-area page boxes before it reached the Canon queue. The server should log `pdf_size_check=normalized-to-media` and forward a `.normalized-Letter.pdf` file whose page size is 8.5 x 11 inches while preserving `scale=1.0`.

## Docker / Synology NAS

For always-on use, run the Docker image on Synology Container Manager instead of keeping a Mac awake. The container includes Linux CUPS, Avahi/mDNS, `ippeveprinter`, `pdfinfo`, `qpdf`, and this package.

Start with the Synology guide:

```text
docs/SYNOLOGY.md
```

The NAS deployment does not depend on the Mac's CUPS queue. It creates its own CUPS queue pointed at the Canon printer's stable IPP endpoint, for example:

```text
ipp://192.168.1.50/ipp/print
```

Use the macvlan Compose template when possible:

```text
deploy/compose.synology-macvlan.yml
```

The fallback host-network template is available at:

```text
deploy/compose.synology-host.yml
```

Persist `/data` on the NAS. The container writes structured job events to `/data/jobs.jsonl`, keeps received spool files under `/data/spool` when `HPP_KEEP_SPOOL=1`, and writes CUPS logs under `/data/cups-logs`.

## Defensive Behavior

Expected job-log statuses:

- `submitted`: the Canon-facing `lp` command accepted the job.
- `dry-run`: validation and command construction succeeded without paper.
- `rejected-input`: empty input, unsupported content type, non-PDF input, missing PDF header, or corrupt PDF.
- `rejected-page-size`: the PDF is valid but cannot be safely padded to the selected media.
- `upstream-unavailable`: the Canon CUPS queue is missing, disabled, or not accepting requests.
- `configuration-error`: required helper tooling such as `pdfinfo` or `qpdf` is missing, misconfigured, or timed out.
- `failed`: `lp` ran and returned an error.

By default, only PDFs are accepted for dimension-critical printing because image and raster formats do not carry the same page-box geometry needed for exact-scale validation. `HPP_ALLOW_NON_PDF=1` exists only for diagnostics.

## Calibration

Generate a calibration page:

```sh
./scripts/hundred-percent-print calibration --media Letter --output calibration-letter.pdf
```

Or print it through the same exact-scale path:

```sh
./scripts/hundred-percent-print calibration --media Letter --print --queue Canon_TR150_series
```

Measure all of these on paper before printing pattern pieces:

- 1 inch square must measure exactly 1.000 inch.
- 100 mm square must measure exactly 100.0 mm.
- 6 inch ruler must measure exactly 6.000 inches.

Do not use a printed pattern if the calibration marks are wrong. Check that the pattern PDF page size matches the selected media (`Letter`, `A4`, etc.). Avoid borderless/full-bleed media for dimension-critical patterns unless you have calibrated that mode; many printers expand borderless output.

## Direct Exact-Scale Print

For a PDF already on this Mac:

```sh
./scripts/hundred-percent-print print pattern.pdf --queue Canon_TR150_series --media Letter
```

Dry-run to inspect the exact `lp` command:

```sh
./scripts/hundred-percent-print print pattern.pdf --queue Canon_TR150_series --media Letter --dry-run
```

## Keep the Server Running

Create a LaunchAgent plist:

```sh
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/hundred-percent-print"
./scripts/hundred-percent-print launch-agent --upstream Canon_TR150_series --media Letter > "$HOME/Library/LaunchAgents/com.hundred-percent-print.server.plist"
launchctl bootstrap "gui/$UID" "$HOME/Library/LaunchAgents/com.hundred-percent-print.server.plist"
launchctl enable "gui/$UID/com.hundred-percent-print.server"
```

The LaunchAgent writes standard output and error logs to:

```text
~/Library/Logs/hundred-percent-print/server.out.log
~/Library/Logs/hundred-percent-print/server.err.log
```

Forwarded job events are written to:

```text
~/Library/Logs/hundred-percent-print/jobs.jsonl
```

Stop it:

```sh
launchctl bootout "gui/$UID/com.hundred-percent-print.server"
```

## Queue Modes

The default `serve` mode uses a CUPS-shared queue plus an explicit AirPrint `_universal` DNS-SD advertisement in front of the private backend. This is the supported mode for iOS AirPrint because CUPS handles the iOS `Create-Job` submission flow before the backend applies exact-scale forwarding.

For diagnostics only, expose the backend directly:

```sh
./scripts/hundred-percent-print serve --mode direct --upstream Canon_TR150_series --media Letter
```

You can also create a plain CUPS queue with exact-scale defaults and no forwarding backend:

```sh
./scripts/hundred-percent-print configure-cups --from-destination Canon_TR150_series --queue Hundred_Percent_Patterns --media Letter
```

The forwarding server mode is safer for pattern printing because it forces the final submitted Canon job options for every document. A plain shared queue is only a fallback for desktop clients that respect queue defaults.

## Canon Options

The Canon TR150 queue on this Mac reports these useful options:

- `PageSize`: `Letter`, `Letter.Fullbleed`, `A4`, `A4.Fullbleed`, and others.
- `ColorModel`: `RGB` or `Gray`.
- `cupsPrintQuality`: `Draft`, `Normal`, or `High`.
- `MediaType`: `auto`, `stationery`, `photographic`, and Canon-specific media values.

For scale-critical fabric patterns, start with:

```sh
./scripts/hundred-percent-print serve --upstream Canon_TR150_series --media Letter --quality High --media-type auto
```

If the printer feeds from a specific tray or paper type, add the exact CUPS option after inspecting `lpoptions -p Canon_TR150_series -l`:

```sh
./scripts/hundred-percent-print serve --upstream Canon_TR150_series --media Letter --option InputSlot=Rear
```

## Tests

Run the default safe test suite:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Run the real no-paper AirPrint integration test:

```sh
HPP_RUN_INTEGRATION=1 PYTHONPATH=src python3 -m unittest tests.test_self_test_integration -v
```

Run the optional no-paper CUPS front-end integration test:

```sh
HPP_RUN_CUPS_FRONTEND_INTEGRATION=1 PYTHONPATH=src python3 -m unittest tests.test_self_test_integration -v
```

Validate the Docker entrypoint syntax without building the image:

```sh
sh -n docker/entrypoint.sh
```
