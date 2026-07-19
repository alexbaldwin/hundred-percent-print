#!/bin/sh
set -eu

log() {
    printf '%s\n' "hundred-percent-print: $*" >&2
}

die() {
    log "ERROR: $*"
    exit 2
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

wait_for_cups() {
    attempts=0
    while [ "$attempts" -lt 50 ]; do
        if lpstat -r >/dev/null 2>&1; then
            return 0
        fi
        attempts=$((attempts + 1))
        sleep 0.2
    done
    return 1
}

link_persistent_runtime_dirs() {
    mkdir -p /data/spool /data/cups-logs /run/cups /var/spool/cups /var/cache/cups
    if [ -d /var/log/cups ] && [ ! -L /var/log/cups ]; then
        rm -rf /var/log/cups
    fi
    ln -sfn /data/cups-logs /var/log/cups
}

start_dbus() {
    mkdir -p /run/dbus
    if [ ! -S /run/dbus/system_bus_socket ]; then
        dbus-daemon --system --fork || die "could not start dbus-daemon"
    fi
}

start_avahi() {
    if [ "${HPP_START_AVAHI:-1}" = "0" ]; then
        log "Skipping avahi-daemon because HPP_START_AVAHI=0."
        return 0
    fi

    mkdir -p /run/avahi-daemon
    avahi-daemon --daemonize --no-chroot || die "could not start avahi-daemon; use macvlan networking or set HPP_START_AVAHI=0 if another mDNS publisher is providing AirPrint"
}

start_cups() {
    /usr/sbin/cupsd || die "could not start cupsd"
    wait_for_cups || die "cupsd did not become ready"
    cupsctl --share-printers --remote-any >/dev/null || die "could not enable CUPS printer sharing"
}

configure_upstream_queue() {
    if [ -n "${HPP_UPSTREAM_DEVICE_URI:-}" ] && [ "${HPP_SKIP_UPSTREAM_SETUP:-0}" != "1" ]; then
        log "Configuring upstream CUPS queue ${HPP_UPSTREAM_QUEUE} at ${HPP_UPSTREAM_DEVICE_URI}."
        lpadmin \
            -p "$HPP_UPSTREAM_QUEUE" \
            -E \
            -v "$HPP_UPSTREAM_DEVICE_URI" \
            -m "${HPP_UPSTREAM_MODEL:-everywhere}" \
            -D "${HPP_UPSTREAM_DESCRIPTION:-Exact-scale upstream printer}" \
            -L "${HPP_UPSTREAM_LOCATION:-LAN}" \
            -o printer-is-shared=false \
            -o printer-error-policy=abort-job \
            || die "could not configure upstream queue ${HPP_UPSTREAM_QUEUE}"

        lpoptions \
            -p "$HPP_UPSTREAM_QUEUE" \
            -o print-scaling=none \
            -o fit-to-page=false \
            -o scaling=100 \
            -o natural-scaling=100 \
            -o number-up=1 \
            -o sides=one-sided \
            -o Duplex=None \
            -o "media=$HPP_MEDIA" \
            -o "PageSize=$HPP_PAGE_SIZE" \
            -o "ColorModel=$HPP_COLOR_MODEL" \
            -o "cupsPrintQuality=$HPP_PRINT_QUALITY" \
            -o "MediaType=$HPP_MEDIA_TYPE" \
            -o print-quality=5 \
            || die "could not set exact-scale defaults on upstream queue ${HPP_UPSTREAM_QUEUE}"

        cupsenable "$HPP_UPSTREAM_QUEUE" || die "could not enable upstream queue ${HPP_UPSTREAM_QUEUE}"
        cupsaccept "$HPP_UPSTREAM_QUEUE" || die "upstream queue ${HPP_UPSTREAM_QUEUE} is not accepting jobs"
    fi

    if ! lpstat -v "$HPP_UPSTREAM_QUEUE" >/dev/null 2>&1; then
        die "upstream queue ${HPP_UPSTREAM_QUEUE} does not exist. Set HPP_UPSTREAM_DEVICE_URI, or set HPP_SKIP_UPSTREAM_SETUP=1 only after creating the queue yourself."
    fi
}

run_server() {
    set -- hundred-percent-print serve \
        --upstream "$HPP_UPSTREAM_QUEUE" \
        --media "$HPP_MEDIA" \
        --page-size "$HPP_PAGE_SIZE" \
        --color-model "$HPP_COLOR_MODEL" \
        --quality "$HPP_PRINT_QUALITY" \
        --media-type "$HPP_MEDIA_TYPE" \
        --mode "$HPP_MODE" \
        --cups-frontend-queue "$HPP_CUPS_FRONTEND_QUEUE" \
        --airprint-name "$HPP_AIRPRINT_NAME" \
        --backend-name "$HPP_BACKEND_NAME" \
        --port "$HPP_PORT" \
        --spool "$HPP_SPOOL" \
        --job-log "$HPP_JOB_LOG"

    if [ -n "${HPP_RESOLUTION:-}" ]; then
        set -- "$@" --resolution "$HPP_RESOLUTION"
    fi
    if [ "${HPP_KEEP_SPOOL:-1}" = "1" ]; then
        set -- "$@" --keep-spool
    fi
    if [ "${HPP_FORWARD_DRY_RUN:-0}" = "1" ]; then
        set -- "$@" --forward-dry-run
    fi
    if [ "${HPP_NO_AIRPRINT_ADVERTISE:-0}" = "1" ]; then
        set -- "$@" --no-airprint-advertise
    fi

    log "Starting exact-scale AirPrint proxy."
    exec "$@"
}

case "${1:-}" in
    hundred-percent-print|hpp-forward-job|python|python3|sh|bash)
        exec "$@"
        ;;
    discover|serve|self-test|status|calibration|print|configure-cups|launch-agent)
        exec hundred-percent-print "$@"
        ;;
esac

HPP_UPSTREAM_QUEUE="${HPP_UPSTREAM_QUEUE:-Canon_TR150_series}"
HPP_MEDIA="${HPP_MEDIA:-Letter}"
HPP_PAGE_SIZE="${HPP_PAGE_SIZE:-$HPP_MEDIA}"
HPP_COLOR_MODEL="${HPP_COLOR_MODEL:-RGB}"
HPP_PRINT_QUALITY="${HPP_PRINT_QUALITY:-High}"
HPP_MEDIA_TYPE="${HPP_MEDIA_TYPE:-auto}"
HPP_MODE="${HPP_MODE:-cups}"
HPP_CUPS_FRONTEND_QUEUE="${HPP_CUPS_FRONTEND_QUEUE:-Hundred_Percent_Patterns}"
HPP_AIRPRINT_NAME="${HPP_AIRPRINT_NAME:-100 Percent Pattern Print SAFE}"
HPP_BACKEND_NAME="${HPP_BACKEND_NAME:-Hundred Percent Print Private Backend}"
HPP_PORT="${HPP_PORT:-8799}"
HPP_SPOOL="${HPP_SPOOL:-/data/spool}"
HPP_JOB_LOG="${HPP_JOB_LOG:-/data/jobs.jsonl}"

for required in dbus-daemon avahi-daemon cupsd cupsctl lpadmin lpoptions cupsenable cupsaccept lpstat ippeveprinter ipptool pdfinfo qpdf; do
    command_exists "$required" || die "required command is missing from the container image: ${required}"
done

link_persistent_runtime_dirs
start_dbus
start_avahi
start_cups
configure_upstream_queue
run_server
