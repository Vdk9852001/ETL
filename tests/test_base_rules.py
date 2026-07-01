"""
Unit Tests — core/rules/base_rules.py
=======================================
Run:  python -m pytest tests/test_base_rules.py -v
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
import pandas as pd
from core.rules.base_rules import (
    normalize_matnr, preprocess_makt, preprocess_generic,
    detect_and_format_dates, apply_value_lookup,
)


class TestNormalizeMatnr(unittest.TestCase):

    def test_numeric_padded_to_18(self):
        df = pd.DataFrame({"MATNR": ["1234", "99", "1"]})
        out = normalize_matnr(df)
        self.assertEqual(out["MATNR"].iloc[0], "000000000000001234")
        self.assertEqual(out["MATNR"].iloc[1], "000000000000000099")

    def test_alphanumeric_not_padded(self):
        df = pd.DataFrame({"MATNR": ["TRADING-001", "CC000112361"]})
        out = normalize_matnr(df)
        self.assertEqual(out["MATNR"].iloc[0], "TRADING-001")
        self.assertEqual(out["MATNR"].iloc[1], "CC000112361")

    def test_missing_column_returns_df_unchanged(self):
        df = pd.DataFrame({"KUNNR": ["C001"]})
        out = normalize_matnr(df)
        self.assertListEqual(list(out.columns), ["KUNNR"])

    def test_custom_column_and_length(self):
        df = pd.DataFrame({"PRODUCT": ["42", "7"]})
        out = normalize_matnr(df, col="PRODUCT", length=10)
        self.assertEqual(out["PRODUCT"].iloc[0], "0000000042")

    def test_mixed_in_same_column(self):
        df = pd.DataFrame({"MATNR": ["1234", "ABC-99", "5678"]})
        out = normalize_matnr(df)
        self.assertEqual(out["MATNR"].iloc[0], "000000000000001234")
        self.assertEqual(out["MATNR"].iloc[1], "ABC-99")


class TestPreprocessMakt(unittest.TestCase):

    def _makt(self):
        return pd.DataFrame({
            "MATNR": ["1234", "1234", "5678"],
            "SPRAS": ["E",    "D",    "E"],
            "MAKTX": ["Product EN", "Product DE", "Steel EN"],
        })

    def test_keeps_only_english(self):
        out = preprocess_makt(self._makt(), preferred_lang="E")
        self.assertEqual(len(out), 2)
        self.assertTrue((out["SPRAS"] == "E").all())

    def test_case_insensitive_language(self):
        df = pd.DataFrame({"MATNR": ["1"], "SPRAS": ["e"], "MAKTX": ["test"]})
        out = preprocess_makt(df, preferred_lang="E")
        self.assertEqual(len(out), 1)

    def test_matnr_padded_after_filter(self):
        df = pd.DataFrame({
            "MATNR": ["42", "42"], "SPRAS": ["E", "D"], "MAKTX": ["EN", "DE"]
        })
        out = preprocess_makt(df, preferred_lang="E")
        self.assertEqual(out["MATNR"].iloc[0], "000000000000000042")

    def test_no_spras_returns_all_rows(self):
        df = pd.DataFrame({"MATNR": ["1", "2"], "MAKTX": ["A", "B"]})
        out = preprocess_makt(df)
        self.assertEqual(len(out), 2)


class TestPreprocessGeneric(unittest.TestCase):

    def test_column_names_stripped(self):
        df = pd.DataFrame({"  MATNR  ": ["1"], "  NAME1  ": ["a"]})
        out = preprocess_generic(df)
        self.assertIn("MATNR", out.columns)
        self.assertIn("NAME1", out.columns)

    def test_cell_values_stripped(self):
        df = pd.DataFrame({"MATNR": ["  1234  "]})
        out = preprocess_generic(df)
        self.assertEqual(out["MATNR"].iloc[0], "1234")


class TestDetectAndFormatDates(unittest.TestCase):

    def test_yyyymmdd_reformatted(self):
        df = pd.DataFrame({"DATAB": ["20240101","20240201","20240301","20240401","20240501"]})
        out = detect_and_format_dates(df, date_format="%Y-%m-%d")
        self.assertEqual(out["DATAB"].iloc[0], "2024-01-01")

    def test_9999_becomes_placeholder(self):
        df = pd.DataFrame({"DATBI": ["99991231", "20240101"]})
        out = detect_and_format_dates(df, date_format="%Y-%m-%d", placeholder_format="9999/12/31")
        self.assertEqual(out["DATBI"].iloc[0], "9999/12/31")
        self.assertEqual(out["DATBI"].iloc[1], "2024-01-01")

    def test_non_date_column_unchanged(self):
        df = pd.DataFrame({"MATKL": ["00101", "00102", "00103"]})
        out = detect_and_format_dates(df)
        self.assertEqual(out["MATKL"].iloc[0], "00101")

    def test_idempotent_calling_twice(self):
        """Regression: original code called this per column causing double-format."""
        df = pd.DataFrame({"DATAB": ["20240101", "20240201"], "DATBI": ["20241231", "20251231"]})
        once  = detect_and_format_dates(df.copy())
        twice = detect_and_format_dates(detect_and_format_dates(df.copy()))
        pd.testing.assert_frame_equal(once, twice)

    def test_excluded_field_not_touched(self):
        df = pd.DataFrame({"S_MARA-MATKL": ["20240101", "20240201"]})
        out = detect_and_format_dates(df)
        self.assertEqual(out["S_MARA-MATKL"].iloc[0], "20240101")

    def test_two_date_columns_both_reformatted(self):
        """Both columns formatted in one call — verifies the loop-bug fix."""
        df = pd.DataFrame({
            "DATAB": ["20240101", "20240201"],
            "DATBI": ["20241231", "20251231"],
        })
        out = detect_and_format_dates(df, date_format="%Y-%m-%d")
        self.assertIn("-", out["DATAB"].iloc[0])
        self.assertIn("-", out["DATBI"].iloc[0])


class TestApplyValueLookup(unittest.TestCase):

    def test_known_values_mapped(self):
        s  = pd.Series(["0001", "0004", "0002"])
        vm = {"0001": "BP01", "0002": "CP01", "0004": "SP01"}
        out, unmapped = apply_value_lookup(s, vm)
        self.assertEqual(out.iloc[0], "BP01")
        self.assertEqual(out.iloc[1], "SP01")
        self.assertEqual(unmapped, [])

    def test_unknown_values_passed_through(self):
        """Values not in mapping must come back unchanged, not blank."""
        s  = pd.Series(["0001", "9999"])
        vm = {"0001": "BP01"}
        out, unmapped = apply_value_lookup(s, vm)
        self.assertEqual(out.iloc[1], "9999")
        self.assertIn("9999", unmapped)

    def test_trailing_dot_zero_stripped(self):
        """Excel float artefact '0004.0' must resolve to '0004' before lookup."""
        s  = pd.Series(["0004.0", "0001.0"])
        vm = {"0004": "SP01", "0001": "BP01"}
        out, unmapped = apply_value_lookup(s, vm)
        self.assertEqual(out.iloc[0], "SP01")
        self.assertEqual(unmapped, [])

    def test_empty_series(self):
        s  = pd.Series([], dtype=str)
        vm = {"A": "B"}
        out, unmapped = apply_value_lookup(s, vm)
        self.assertEqual(len(out), 0)

    def test_unmapped_distinct_values_only(self):
        s  = pd.Series(["0001", "9999", "9999", "8888"])
        vm = {"0001": "BP01"}
        _, unmapped = apply_value_lookup(s, vm)
        self.assertEqual(len(unmapped), 2)
        self.assertIn("9999", unmapped)
        self.assertIn("8888", unmapped)


if __name__ == "__main__":
    unittest.main(verbosity=2)
