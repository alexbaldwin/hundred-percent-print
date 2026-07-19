FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/alexbaldwin/hundred-percent-print" \
      org.opencontainers.image.description="Exact-scale AirPrint proxy for dimension-critical PDF printing" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HPP_SPOOL=/data/spool \
    HPP_JOB_LOG=/data/jobs.jsonl

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        avahi-daemon \
        avahi-utils \
        ca-certificates \
        cups \
        cups-client \
        cups-filters \
        cups-ipp-utils \
        dbus \
        iproute2 \
        poppler-utils \
        qpdf \
        tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --upgrade pip \
    && python -m pip install .

COPY docker/entrypoint.sh /usr/local/bin/hundred-percent-print-entrypoint
RUN chmod +x /usr/local/bin/hundred-percent-print-entrypoint \
    && mkdir -p /data/spool /data/cups-logs

VOLUME ["/data"]
EXPOSE 631/tcp 5353/udp 8799/tcp

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/hundred-percent-print-entrypoint"]
