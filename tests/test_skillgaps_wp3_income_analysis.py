"""WP-3 regression: the deployed income-analysis unit contract."""

from pathlib import Path
import sys
import unittest

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "skills" / "knuaf-doc" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import gg_finance_tables as ft  # noqa: E402


class IncomeAnalysisUnitTests(unittest.TestCase):
    def test_yield_rate_row_is_ratio_not_percent_value(self):
        table_map = ft.load_table_map()
        table = next(
            t for section in table_map["sections"] for t in section["tables"]
            if t["id"] == "income_analysis")
        units = table["extract"]["row_units"]
        self.assertEqual(units["영농잉여율"], "ratio")
        fmt = ft._FORMATS[units["영농잉여율"]]
        self.assertEqual(fmt(-1.9847731), "-198.5")
        self.assertEqual(fmt(0.2453938), "24.5")


if __name__ == "__main__":
    unittest.main()
