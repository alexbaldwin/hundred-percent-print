from __future__ import annotations

import unittest

from hundred_percent_print.options import PrintSettings, job_options, normalize_extra_options


class OptionTests(unittest.TestCase):
    def test_job_options_force_exact_scale(self) -> None:
        options = job_options(PrintSettings(upstream_queue="Canon_TR150_series", media="Letter"))

        self.assertIn("print-scaling=none", options)
        self.assertIn("fit-to-page=false", options)
        self.assertIn("scaling=100", options)
        self.assertIn("natural-scaling=100", options)
        self.assertIn("number-up=1", options)
        self.assertIn("sides=one-sided", options)
        self.assertIn("media=Letter", options)
        self.assertIn("PageSize=Letter", options)

    def test_extra_options_require_name_value(self) -> None:
        with self.assertRaises(ValueError):
            normalize_extra_options(["not-an-option"])

    def test_extra_options_are_normalized(self) -> None:
        self.assertEqual(normalize_extra_options([" InputSlot = Rear "]), ("InputSlot=Rear",))


if __name__ == "__main__":
    unittest.main()
