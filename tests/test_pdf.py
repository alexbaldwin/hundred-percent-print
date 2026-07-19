from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hundred_percent_print.pdf import page_size_points, write_calibration_pdf


class PdfTests(unittest.TestCase):
    def test_letter_size_points(self) -> None:
        self.assertEqual(page_size_points("Letter"), (612.0, 792.0))

    def test_fullbleed_media_alias_uses_parent_page_size(self) -> None:
        self.assertEqual(page_size_points("Letter.Fullbleed"), (612.0, 792.0))

    def test_calibration_pdf_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = write_calibration_pdf(Path(temp_dir) / "calibration.pdf", media="Letter")
            data = path.read_bytes()

        self.assertTrue(data.startswith(b"%PDF-1.4"))
        self.assertIn(b"1 inch square", data)
        self.assertIn(b"100 mm square", data)


if __name__ == "__main__":
    unittest.main()
