from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


DEFAULT_MEDIA = "Letter"
DEFAULT_QUEUE = "Canon_TR150_series"
DEFAULT_SERVICE_NAME = "100 Percent Pattern Print"


@dataclass(frozen=True)
class PrintSettings:
    upstream_queue: str = DEFAULT_QUEUE
    media: str = DEFAULT_MEDIA
    page_size: str | None = None
    color_model: str = "RGB"
    print_quality: str = "High"
    media_type: str = "auto"
    resolution: str | None = None
    extra_options: tuple[str, ...] = field(default_factory=tuple)

    @property
    def normalized_page_size(self) -> str:
        return self.page_size or self.media


SCALE_LOCK_OPTIONS: tuple[tuple[str, str], ...] = (
    ("print-scaling", "none"),
    ("fit-to-page", "false"),
    ("scaling", "100"),
    ("natural-scaling", "100"),
    ("number-up", "1"),
    ("sides", "one-sided"),
    ("Duplex", "None"),
)


def job_options(settings: PrintSettings) -> list[str]:
    options: list[str] = [f"{name}={value}" for name, value in SCALE_LOCK_OPTIONS]

    options.extend(
        [
            f"media={settings.media}",
            f"PageSize={settings.normalized_page_size}",
            f"ColorModel={settings.color_model}",
            f"cupsPrintQuality={settings.print_quality}",
            f"MediaType={settings.media_type}",
            "print-quality=5",
        ]
    )

    if settings.resolution:
        options.append(f"printer-resolution={settings.resolution}")

    options.extend(settings.extra_options)
    return options


def lp_option_args(settings: PrintSettings) -> list[str]:
    args: list[str] = []
    for option in job_options(settings):
        args.extend(["-o", option])
    return args


def lpadmin_default_options(settings: PrintSettings) -> list[str]:
    # Use both IPP default attributes and legacy PPD option names. AirPrint,
    # CUPS, and vendor firmware do not all honor the same spelling.
    return [
        f"media-default={settings.media}",
        "print-scaling-default=none",
        "sides-default=one-sided",
        "print-quality-default=5",
        "copies-default=1",
        f"PageSize={settings.normalized_page_size}",
        f"ColorModel={settings.color_model}",
        f"cupsPrintQuality={settings.print_quality}",
        f"MediaType={settings.media_type}",
    ]


def lpadmin_option_args(settings: PrintSettings) -> list[str]:
    args: list[str] = []
    for option in lpadmin_default_options(settings):
        args.extend(["-o", option])
    return args


def normalize_extra_options(values: Iterable[str] | None) -> tuple[str, ...]:
    if not values:
        return ()

    normalized: list[str] = []
    for value in values:
        if "=" not in value:
            raise ValueError(f"Print option must use name=value form: {value}")
        name, option_value = value.split("=", 1)
        name = name.strip()
        option_value = option_value.strip()
        if not name or not option_value:
            raise ValueError(f"Print option must use name=value form: {value}")
        normalized.append(f"{name}={option_value}")
    return tuple(normalized)


def parse_extra_options_env(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return normalize_extra_options(line for line in value.splitlines() if line.strip())
