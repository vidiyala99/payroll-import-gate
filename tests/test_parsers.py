"""Parser tests on odd formats seen in the MOCK/SAMPLE exports."""

import unittest
from datetime import date, time
from decimal import Decimal

from gate.parsers import (ParseError, parse_amount, parse_break_minutes, parse_date, parse_hours,
                          parse_time, read_csv)


class TestMockDates(unittest.TestCase):
    def test_mock_date_formats(self):
        self.assertEqual(parse_date("11/02/2026"), date(2026, 11, 2))
        self.assertEqual(parse_date("2026-11-03"), date(2026, 11, 3))
        self.assertEqual(parse_date("05-Nov-2026"), date(2026, 11, 5))
        self.assertEqual(parse_date("02/11/2026", "DMY"), date(2026, 11, 2))

    def test_mock_ambiguous_or_bad_dates_are_errors_not_guesses(self):
        for bad in ["13/11/2026", "", "Nov 5", "31-Foo-2026", "02/30/2026"]:
            with self.assertRaises(ParseError, msg=bad):
                parse_date(bad)


class TestMockTimesAndHours(unittest.TestCase):
    def test_mock_time_formats(self):
        self.assertEqual(parse_time("7:00 AM"), time(7, 0))
        self.assertEqual(parse_time("4:00 PM"), time(16, 0))
        self.assertEqual(parse_time("12:00 AM"), time(0, 0))
        self.assertEqual(parse_time("0700"), time(7, 0))
        self.assertEqual(parse_time("16:30"), time(16, 30))

    def test_mock_bad_times(self):
        for bad in ["7", "TBD", "25:00", "13:00 PM", ""]:
            with self.assertRaises(ParseError, msg=bad):
                parse_time(bad)

    def test_mock_hours_formats(self):
        self.assertEqual(parse_hours("8.5"), Decimal("8.50"))
        self.assertEqual(parse_hours("8:30"), Decimal("8.50"))
        self.assertEqual(parse_hours("8h30m"), Decimal("8.50"))
        self.assertEqual(parse_hours("8h"), Decimal("8.00"))
        self.assertEqual(parse_hours("45m"), Decimal("0.75"))
        with self.assertRaises(ParseError):
            parse_hours("eight")

    def test_mock_break_formats(self):
        self.assertEqual(parse_break_minutes(""), 0)
        self.assertEqual(parse_break_minutes("30"), 30)
        self.assertEqual(parse_break_minutes("0:30"), 30)
        with self.assertRaises(ParseError):
            parse_break_minutes("half hour")


class TestMockAmounts(unittest.TestCase):
    def test_mock_western_and_indian_grouping(self):
        self.assertEqual(parse_amount("$104,000"), (Decimal("104000"), "USD"))
        self.assertEqual(parse_amount("₹18,00,000"), (Decimal("1800000"), "INR"))
        self.assertEqual(parse_amount("INR 9,60,000"), (Decimal("960000"), "INR"))
        self.assertEqual(parse_amount("1,50,000.00"), (Decimal("150000.00"), None))
        self.assertEqual(parse_amount("104000 USD"), (Decimal("104000"), "USD"))
        self.assertEqual(parse_amount("(12.00)"), (Decimal("-12.00"), None))

    def test_mock_bad_amounts(self):
        for bad in ["", "1,2,3", "12.3.4", "abc", "$1,0000"]:
            with self.assertRaises(ParseError, msg=bad):
                parse_amount(bad)

    def test_mock_conflicting_currency_markers(self):
        with self.assertRaises(ParseError):
            parse_amount("INR $500")


class TestMockCsv(unittest.TestCase):
    def test_mock_row_numbers_count_header_as_row_1_and_skip_blank_lines(self):
        headers, rows = read_csv("a,b\n1,2\n\n3\n")
        self.assertEqual(headers, ["a", "b"])
        self.assertEqual([n for n, _ in rows], [2, 4])
        self.assertEqual(rows[1][1], {"a": "3", "b": ""})


if __name__ == "__main__":
    unittest.main()
