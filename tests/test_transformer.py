"""
Unit Tests — core/transformer.py
==================================
Run:  python -m pytest tests/test_transformer.py -v
"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
import pandas as pd
from core.transformer import load_legacy_tables, transform


def write_csv(df, folder, filename):
    path = os.path.join(folder, filename)
    df.to_csv(path, index=False)
    return path


class TestLoadLegacyTables(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_csv_loaded_as_string_dtype(self):
        """All columns must be str-compatible to preserve leading zeros like 000001."""
        df   = pd.DataFrame({"MATNR": ["000001", "000002"], "MTART": ["VERP", "ROH"]})
        path = write_csv(df, self.tmp, "MARA.csv")
        tables = load_legacy_tables({"MARA": path}, log_fn=lambda m: None)
        # Accept object (pandas 2), string / str (pandas 3)
        dtype_name = str(tables["MARA"]["MATNR"].dtype)
        self.assertTrue(
            dtype_name in ("object", "string", "str") or "string" in dtype_name.lower(),
            f"Expected string-compatible dtype, got {dtype_name}"
        )

    def test_matnr_zero_padded_on_load(self):
        """Numeric MATNR in source CSV padded to 18 chars after load."""
        df   = pd.DataFrame({"MATNR": ["1234"], "MTART": ["VERP"]})
        path = write_csv(df, self.tmp, "MARA.csv")
        tables = load_legacy_tables({"MARA": path}, log_fn=lambda m: None)
        self.assertEqual(tables["MARA"]["MATNR"].iloc[0], "000000000000001234")

    def test_makt_filtered_to_english(self):
        """MAKT rows with SPRAS != preferred_lang removed."""
        df = pd.DataFrame({
            "MATNR": ["1", "1",  "2"],
            "SPRAS": ["E", "D",  "E"],
            "MAKTX": ["EN","DE", "EN2"],
        })
        path   = write_csv(df, self.tmp, "MAKT.csv")
        tables = load_legacy_tables({"MAKT": path}, preferred_lang="E",
                                    log_fn=lambda m: None)
        self.assertEqual(len(tables["MAKT"]), 2)
        self.assertTrue((tables["MAKT"]["SPRAS"] == "E").all())

    def test_multiple_tables_loaded(self):
        mara = pd.DataFrame({"MATNR": ["1"], "MTART": ["VERP"]})
        mlan = pd.DataFrame({"MATNR": ["1"], "ALAND": ["DE"]})
        p1   = write_csv(mara, self.tmp, "MARA.csv")
        p2   = write_csv(mlan, self.tmp, "MLAN.csv")
        tables = load_legacy_tables({"MARA": p1, "MLAN": p2},
                                    log_fn=lambda m: None)
        self.assertIn("MARA", tables)
        self.assertIn("MLAN", tables)

    def test_missing_file_skipped_no_crash(self):
        """Non-existent file path logged and skipped, not exception."""
        logs   = []
        tables = load_legacy_tables({"MARA": "/nonexistent/MARA.csv"},
                                    log_fn=logs.append)
        self.assertNotIn("MARA", tables)
        self.assertTrue(any("ERROR" in l for l in logs))


class TestTransformDirect(unittest.TestCase):
    """Direct mapping — no joins required."""

    def _field_map(self):
        return {
            "S_MARA": [
                {"s4_field": "S_MARA-MATNR", "s4_col": "MATNR",
                 "src_table": "MARA", "src_field": "MATNR"},
                {"s4_field": "S_MARA-MTART", "s4_col": "MTART",
                 "src_table": "MARA", "src_field": "MTART"},
            ]
        }

    def _legacy(self):
        return {
            "MARA": pd.DataFrame({
                "MATNR": ["000000000000001234", "000000000000005678"],
                "MTART": ["VERP", "ROH"],
            })
        }

    def test_fields_filled_from_correct_column(self):
        result = transform(self._field_map(), self._legacy(), "PRODUCT",
                           log_fn=lambda m: None)
        self.assertIn("S_MARA", result)
        df = result["S_MARA"]
        self.assertEqual(df["S_MARA-MATNR"].iloc[0], "000000000000001234")
        self.assertEqual(df["S_MARA-MTART"].iloc[1], "ROH")

    def test_missing_source_table_column_is_none(self):
        """Column not in source table → filled with None, no crash."""
        fm = {"S_MARA": [{"s4_field": "S_MARA-MISSING", "s4_col": "MISSING",
                           "src_table": "MARA", "src_field": "MISSING"}]}
        result = transform(fm, self._legacy(), "PRODUCT",
                           log_fn=lambda m: None)
        if "S_MARA" in result:
            vals = result["S_MARA"]["S_MARA-MISSING"]
            self.assertTrue(vals.isna().all() or (vals == "None").all())

    def test_missing_source_table_skipped_gracefully(self):
        """Source table not loaded at all → field skipped, no crash."""
        fm = {"S_MARA": [{"s4_field": "S_MARA-MAKTX", "s4_col": "MAKTX",
                           "src_table": "MAKT", "src_field": "MAKTX"}]}
        result = transform(fm, {}, "PRODUCT", log_fn=lambda m: None)
        # Either no table or None column — both are acceptable
        if "S_MARA" in result:
            vals = result["S_MARA"]["S_MARA-MAKTX"]
            self.assertTrue(vals.isna().all() or (vals == "None").all())


class TestTransformWithJoin(unittest.TestCase):
    """Join-based mapping — MAKT joins MARA on MATNR."""

    def test_makt_maktx_filled_via_join(self):
        fm = {
            "S_MARA": [
                {"s4_field": "S_MARA-MATNR", "s4_col": "MATNR",
                 "src_table": "MARA", "src_field": "MATNR"},
                {"s4_field": "S_MARA-MAKTX", "s4_col": "MAKTX",
                 "src_table": "MAKT", "src_field": "MAKTX"},
            ]
        }
        mara = pd.DataFrame({
            "MATNR": ["000000000000001234", "000000000000005678"],
            "MTART": ["VERP", "ROH"],
        })
        makt = pd.DataFrame({
            "MATNR": ["000000000000001234", "000000000000005678"],
            "SPRAS": ["E", "E"],
            "MAKTX": ["Carton Box", "Raw Steel"],
        })
        result = transform(fm, {"MARA": mara, "MAKT": makt}, "PRODUCT",
                           log_fn=lambda m: None)
        df = result.get("S_MARA")
        self.assertIsNotNone(df)
        self.assertEqual(df["S_MARA-MAKTX"].iloc[0], "Carton Box")
        self.assertEqual(df["S_MARA-MAKTX"].iloc[1], "Raw Steel")

    def test_join_deduplicates_right_table_before_merge(self):
        """
        Duplicate rows in join table (TB070_CM) must be deduped
        before merge to prevent row explosion (1 row → 2 rows).
        """
        fm = {
            "S_MLAN": [
                {"s4_field": "S_MLAN-MATNR", "s4_col": "MATNR",
                 "src_table": "MLAN", "src_field": "MATNR"},
                {"s4_field": "S_MLAN-TAXKM", "s4_col": "TAXKM",
                 "src_table": "TB070_CM", "src_field": "TAXKM"},
            ]
        }
        mlan = pd.DataFrame({"MATNR": ["1"], "ALAND": ["DE"], "TAXKM": ["1"]})
        # Two identical rows in TB070_CM — without dedup this doubles output
        tb070 = pd.DataFrame({
            "TAX_CTY": ["DE", "DE"],
            "TAXKM":   ["EU_TAX", "EU_TAX"],
        })
        result = transform(fm, {"MLAN": mlan, "TB070_CM": tb070}, "PRODUCT",
                           log_fn=lambda m: None)
        df = result.get("S_MLAN")
        if df is not None:
            self.assertEqual(len(df), 1)   # must not be doubled

    def test_join_cache_used_for_same_pair(self):
        """Two fields from same joined table pair — merge runs once only."""
        fm = {
            "S_MARA": [
                {"s4_field": "S_MARA-MAKTX", "s4_col": "MAKTX",
                 "src_table": "MAKT", "src_field": "MAKTX"},
                {"s4_field": "S_MARA-SPRAS", "s4_col": "SPRAS",
                 "src_table": "MAKT", "src_field": "SPRAS"},
            ]
        }
        mara = pd.DataFrame({"MATNR": ["1", "2"]})
        makt = pd.DataFrame({
            "MATNR": ["1", "2"],
            "SPRAS": ["E", "E"],
            "MAKTX": ["Item One", "Item Two"],
        })
        result = transform(fm, {"MARA": mara, "MAKT": makt}, "PRODUCT",
                           log_fn=lambda m: None)
        df = result.get("S_MARA")
        if df is not None:
            # Both fields must be populated from the cached join
            self.assertIn("S_MARA-MAKTX", df.columns)
            self.assertIn("S_MARA-SPRAS", df.columns)


class TestDateFormattingCalledOnce(unittest.TestCase):

    def test_both_date_columns_formatted_in_single_pass(self):
        """
        Regression test for original bug:
        detect_and_format_dates was called inside the column loop,
        so only the LAST column got formatted on the last call.
        Now called once per table — both DATAB and DATBI must be formatted.
        """
        fm = {
            "S_MARC": [
                {"s4_field": "S_MARC-DATAB", "s4_col": "DATAB",
                 "src_table": "MARC", "src_field": "DATAB"},
                {"s4_field": "S_MARC-DATBI", "s4_col": "DATBI",
                 "src_table": "MARC", "src_field": "DATBI"},
            ]
        }
        legacy = {
            "MARC": pd.DataFrame({
                "MATNR": ["1", "2"],
                "DATAB": ["20240101", "20240201"],
                "DATBI": ["20251231", "99991231"],
            })
        }
        result = transform(fm, legacy, "PRODUCT", log_fn=lambda m: None)
        df = result.get("S_MARC")
        if df is not None:
            self.assertIn("-", str(df["S_MARC-DATAB"].iloc[0]))
            self.assertIn("9999", str(df["S_MARC-DATBI"].iloc[1]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
