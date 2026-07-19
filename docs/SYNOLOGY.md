# Synology Docker Deployment

This is the recommended always-on setup for a NAS: one Docker container runs Linux CUPS, Avahi/mDNS, and the exact-scale forwarding server. iOS sees one AirPrint printer named `100 Percent Pattern Print SAFE`; the container forwards accepted jobs to the real Canon printer with scale-locking options and PDF page-size validation.

## Requirements

- Synology DSM with Container Manager.
- A Canon printer with a stable LAN IP address.
- A stable LAN IP address reserved for the `hundred-percent-print` container.
- The printer and iOS devices must be on a network where multicast DNS is allowed.

Synology Container Manager supports Docker containers and Compose projects. AirPrint discovery depends on mDNS/DNS-SD, and Docker bridge networking usually hides that traffic from the LAN. Use the macvlan Compose template unless you know host networking will not conflict with anything on the NAS.

## 1. Build Or Publish The Image

From this repository:

```sh
docker build -t hundred-percent-print:local .
```

The repository publishes a multi-architecture image for Intel and ARM Synology models whenever `main` is updated or a `v*` release tag is pushed:

```sh
docker pull ghcr.io/alexbaldwin/hundred-percent-print:latest
```

The Compose templates already reference `ghcr.io/alexbaldwin/hundred-percent-print:latest`. GitHub Container Registry packages are private on first publication; the package must be changed to public before an unauthenticated Synology can pull it.

## 2. Choose The Canon Device URI

Start with this form, replacing the IP address:

```text
ipp://192.168.1.50/ipp/print
```

If that does not work for your Canon model, try:

```text
ipps://192.168.1.50/ipp/print
```

Prefer IPP over raw socket printing because the container configures a CUPS IPP Everywhere queue. Keep the Canon printer on a DHCP reservation or static IP; do not point this service at a hostname that can drift.

## 3. Configure Macvlan Compose

Copy `deploy/compose.synology-macvlan.yml` and edit these values:

```yaml
HPP_UPSTREAM_DEVICE_URI: "ipp://192.168.1.50/ipp/print"
ipv4_address: 192.168.1.245
parent: eth0
subnet: 192.168.1.0/24
gateway: 192.168.1.1
ip_range: 192.168.1.240/29
```

The `parent` interface is commonly `eth0`; on Synology systems using Open vSwitch it may be `ovs_eth0`. Check with:

```sh
ip link
```

The container IP must be unused, outside your DHCP pool if possible, and reachable from iPhone/iPad devices.

## 4. Deploy In Container Manager

Use Container Manager's Project flow and provide the edited Compose file. If you deploy over SSH instead:

```sh
docker compose -f deploy/compose.synology-macvlan.yml pull
docker compose -f deploy/compose.synology-macvlan.yml up -d
```

If macvlan is not possible, use `deploy/compose.synology-host.yml`. The host template mounts DSM's system D-Bus socket and sets `HPP_START_AVAHI=0`, allowing the container's advertisement commands to publish through DSM's existing Avahi daemon instead of starting a conflicting second mDNS responder. CUPS port `631` must still be free on the NAS.

## 5. Verify Before Printing Patterns

Watch startup logs:

```sh
docker logs -f hundred-percent-print
```

The container should report that it configured `Canon_TR150_series`, started the exact-scale AirPrint proxy, and is writing job logs to `/data/jobs.jsonl`.

Run a no-paper self-test inside the running container:

```sh
docker exec hundred-percent-print \
  hundred-percent-print self-test \
  --mode cups \
  --upstream Canon_TR150_series \
  --media Letter
```

Then print a calibration page from iOS to `100 Percent Pattern Print SAFE` and measure it. Do not print fabric patterns until:

- the job log has `status=submitted`,
- `upstream_check=pass`,
- `pdf_size_check=pass` or `pdf_size_check=normalized-to-media`,
- the printed 1 inch, 100 mm, and 6 inch calibration marks measure exactly.

## Logs And Recovery

The Compose templates persist `/data`:

- `/data/jobs.jsonl`: one structured event per received job.
- `/data/spool`: received and normalized documents when `HPP_KEEP_SPOOL=1`.
- `/data/cups-logs`: CUPS logs from inside the container.

If a job fails, inspect `jobs.jsonl` first. A missing or bad Canon URI should produce an early startup failure or `status=upstream-unavailable`. Invalid files should produce `status=rejected-input`. A wrong or unsafe page size should produce `status=rejected-page-size` and should not reach the Canon queue.

## Runtime Environment

Useful settings:

```text
HPP_UPSTREAM_DEVICE_URI=ipp://192.168.1.50/ipp/print
HPP_UPSTREAM_QUEUE=Canon_TR150_series
HPP_MEDIA=Letter
HPP_PAGE_SIZE=Letter
HPP_AIRPRINT_NAME=100 Percent Pattern Print SAFE
HPP_KEEP_SPOOL=1
HPP_ALLOW_OFFLINE_START=1
HPP_UPSTREAM_RETRY_SECONDS=30
HPP_START_AVAHI=0
HPP_FORWARD_DRY_RUN=1
HPP_EXTRA_OPTIONS=InputSlot=Rear
```

Use `HPP_FORWARD_DRY_RUN=1` when validating discovery and job capture without paper. Remove it for production printing.

With `HPP_ALLOW_OFFLINE_START=1`, the AirPrint service can start while the physical printer is powered off. Real jobs still fail closed while the upstream queue is unavailable, and the container retries driverless queue setup every `HPP_UPSTREAM_RETRY_SECONDS` seconds until the printer comes online.

## Security

This service is intended for a trusted home LAN only. Do not publish container port 631 to the internet and do not put this container behind a public reverse proxy. The service accepts print documents from LAN clients, so keep the image updated and keep the NAS firewall scoped to your local network.

The host-network template mounts `/run/dbus/system_bus_socket` so it can register AirPrint with DSM's Avahi daemon. Treat the container image as trusted host software when using this mode. Macvlan mode does not require this host socket mount.

## References

- [Synology Container Manager package](https://www.synology.com/en-ca/dsm/packages/ContainerManager)
- [Synology Container Manager Project documentation](https://kb.synology.com/en-global/DSM/help/ContainerManager/docker_project?version=7)
- [Synology Docker Compose support](https://www.synology.com/en-us/dsm/feature/docker)
- [Docker host network driver](https://docs.docker.com/engine/network/drivers/host/)
- [Docker Compose networking](https://docs.docker.com/compose/how-tos/networking/)
- [avahi-publish service registration](https://man.archlinux.org/man/avahi-publish.1.en)
