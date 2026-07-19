from __future__ import annotations

from pathlib import Path


POINTS_PER_INCH = 72.0
MM_PER_INCH = 25.4
POINTS_PER_MM = POINTS_PER_INCH / MM_PER_INCH

PAGE_SIZES: dict[str, tuple[float, float]] = {
    "Letter": (8.5 * POINTS_PER_INCH, 11 * POINTS_PER_INCH),
    "Legal": (8.5 * POINTS_PER_INCH, 14 * POINTS_PER_INCH),
    "A4": (210 * POINTS_PER_MM, 297 * POINTS_PER_MM),
    "A5": (148 * POINTS_PER_MM, 210 * POINTS_PER_MM),
}

PAGE_SIZE_ALIASES: dict[str, str] = {
    "Letter.Fullbleed": "Letter",
    "Legal.Fullbleed": "Legal",
    "A4.Fullbleed": "A4",
    "A5.Fullbleed": "A5",
    "na_letter_8.5x11in": "Letter",
    "na_legal_8.5x14in": "Legal",
    "iso_a4_210x297mm": "A4",
    "iso_a5_148x210mm": "A5",
}


def page_size_points(media: str) -> tuple[float, float]:
    canonical_media = PAGE_SIZE_ALIASES.get(media, media)
    if canonical_media in PAGE_SIZES:
        return PAGE_SIZES[canonical_media]
    raise ValueError(
        f"Unsupported media {media!r}. Supported: {', '.join(sorted(PAGE_SIZES))}"
    )


def write_calibration_pdf(path: str | Path, media: str = "Letter") -> Path:
    output = Path(path)
    width, height = page_size_points(media)
    content = _calibration_content(width, height, media)
    pdf = _make_pdf(width, height, content)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(pdf)
    return output


def _calibration_content(width: float, height: float, media: str) -> str:
    one_in = POINTS_PER_INCH
    hundred_mm = 100 * POINTS_PER_MM
    margin = 0.5 * POINTS_PER_INCH
    ruler_y = height - 1.75 * POINTS_PER_INCH
    inch_square_y = height - 3.35 * POINTS_PER_INCH
    metric_square_y = 1.25 * POINTS_PER_INCH
    metric_square_x = margin
    right_note_x = min(width - 2.85 * POINTS_PER_INCH, margin + hundred_mm + 0.35 * POINTS_PER_INCH)

    commands: list[str] = [
        "0 0 0 RG",
        "0 0 0 rg",
        "0.75 w",
        _text(margin, height - 0.75 * POINTS_PER_INCH, 14, "Hundred Percent Print calibration"),
        _text(margin, height - 0.98 * POINTS_PER_INCH, 9, f"Media: {media}. Print through the proxy at 100 percent / no scaling."),
        _text(margin, height - 1.18 * POINTS_PER_INCH, 9, "Measure the boxes after printing. Do not use patterns unless these marks match."),
        f"{margin:.4f} {ruler_y:.4f} {6 * one_in:.4f} 0 m",
        f"{margin + 6 * one_in:.4f} {ruler_y:.4f} l S",
    ]

    for tick in range(7):
        x = margin + tick * one_in
        tick_height = 0.22 * POINTS_PER_INCH if tick in (0, 6) else 0.14 * POINTS_PER_INCH
        commands.append(f"{x:.4f} {ruler_y - tick_height / 2:.4f} m {x:.4f} {ruler_y + tick_height / 2:.4f} l S")
        commands.append(_text(x - 3, ruler_y - 0.35 * POINTS_PER_INCH, 8, str(tick)))
    commands.append(_text(margin, ruler_y + 0.25 * POINTS_PER_INCH, 9, "6 inch ruler"))

    commands.extend(
        [
            f"{margin:.4f} {inch_square_y:.4f} {one_in:.4f} {one_in:.4f} re S",
            _text(margin + one_in + 12, inch_square_y + 0.42 * one_in, 10, "1 inch square"),
            f"{metric_square_x:.4f} {metric_square_y:.4f} {hundred_mm:.4f} {hundred_mm:.4f} re S",
            _text(metric_square_x, metric_square_y + hundred_mm + 12, 10, "100 mm square"),
            _text(right_note_x, metric_square_y + hundred_mm - 8, 8, "Expected measurements:"),
            _text(right_note_x, metric_square_y + hundred_mm - 24, 8, "1 inch box = 1.000 in"),
            _text(right_note_x, metric_square_y + hundred_mm - 40, 8, "100 mm box = 100.0 mm"),
            _text(right_note_x, metric_square_y + hundred_mm - 56, 8, "6 inch ruler = 6.000 in"),
        ]
    )
    return "\n".join(commands) + "\n"


def _text(x: float, y: float, size: int, text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"BT /F1 {size} Tf {x:.4f} {y:.4f} Td ({escaped}) Tj ET"


def _make_pdf(width: float, height: float, content: str) -> bytes:
    stream = content.encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width:.4f} {height:.4f}] "
            "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ).encode("ascii"),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
    ]

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")

    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(output)
