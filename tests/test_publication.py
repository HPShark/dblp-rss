import unittest

from publication import natural_number, publication_date, publication_key


class PublicationTests(unittest.TestCase):
    def test_natural_numeric_order_ranges_supplements_and_missing_values(self):
        values = ["", "S2", "S10", "2", "3", "3-4", "4", "9", "10", "100"]
        self.assertEqual(sorted(reversed(values), key=natural_number), values)
        self.assertEqual(natural_number("03"), natural_number("3"))
        self.assertEqual(natural_number("3/4"), natural_number("3-4"))
        self.assertLess(natural_number("Supplement 2"), natural_number("Supplement 10"))

    def test_dates_keep_available_precision_and_parse_month_spellings_ranges_and_days(self):
        cases = [
            ("", "2026", "year"), ("3", "2026-03", "month"),
            ("March", "2026-03", "month"), ("Mar.", "2026-03", "month"),
            ("March-April", "2026-03", "month"), ("3-4", "2026-03", "month"),
            ("March 12", "2026-03-12", "day"), ("12 March", "2026-03-12", "day"),
            ("September 2026", "2026-09", "month"),
            ("2026-03-12", "2026-03-12", "day"), ("2026-03", "2026-03", "month"),
        ]
        for month, expected, precision in cases:
            with self.subTest(month=month):
                actual = publication_date("2026", month)
                self.assertEqual(actual.value, expected)
                self.assertEqual(actual.precision, precision)
                self.assertEqual(actual.lower_bound.isoformat(), (expected + "-01-01")[:10])

    def test_bad_dates_and_conflicting_years_do_not_override_publication_year(self):
        for month in ["13", "0", "February 30", "March 0", "2026-03-00", "2025-03-12",
                      "March 2025", "Summer", "n/a"]:
            with self.subTest(month=month):
                actual = publication_date("2026", month)
                self.assertEqual((actual.value, actual.precision), ("2026", "year"))
        for year in ["", "0000", "unknown", "20260", "None"]:
            with self.subTest(year=year):
                actual = publication_date(year, "March")
                self.assertEqual(actual.precision, "unknown")
                self.assertIsNone(actual.lower_bound)

    def test_journal_issue_precedes_update_and_conferences_ignore_volumes_and_issues(self):
        journal = lambda issue, modified: publication_key(
            "journals/tdsc", "2026", "23", issue, "", modified, "key")
        self.assertGreater(journal("4", "2020-01-01"), journal("2", "2026-11-09"))
        conference = lambda volume, issue: publication_key(
            "conf/cvpr", "2026", volume, issue, "June", "2020-01-01", "key")
        self.assertEqual(conference("1", "1"), conference("9999", "9999"))

    def test_publication_month_precedes_update_when_volume_and_issue_are_tied(self):
        key = lambda month, modified: publication_key(
            "journals/tifs", "2026", "21", "", month, modified, "key")
        self.assertGreater(key("October", "2020-01-01"), key("March", "2026-11-09"))
        key_conf = lambda month, modified: publication_key(
            "conf/example", "2026", "999", "4", month, modified, "key")
        self.assertGreater(key_conf("December", "2020-01-01"), key_conf("January", "2026-11-09"))


if __name__ == "__main__":
    unittest.main()
