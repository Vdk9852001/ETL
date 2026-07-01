"""
Unit Tests — core/pipeline.py (end-to-end integration)
=========================================================
Tests the full Extract → Transform → Value Map → Export flow
using in-memory data — no Flask server needed.

Run:  python -m pytest tests/test_pipeline.py -v
"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
import pandas as pd
from core.pipeline import (
    PipelineSession, run_extract, run_transform,
    run_value_map, run_export,
)


def _write_csv(df, folder, name):
    path = os.path.join(folder, name)
    df.to_csv(path, index=False)
    return path


def _write_valuemap(data, folder):
    """Write ValueMapping.xlsx with SAP47_Value / S4_Value per sheet."""
    path = os.path.join(folder, "ValueMapping.xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for sheet, vm in data.items():
            rows = [{"SAP47_Value": k, "S4_Value": v} for k, v in vm.items()]
            pd.DataFrame(rows).to_excel(w, sheet_name=sheet, index=False)
    return path


def _write_template(data, folder):
    """
    Write Product_Template.xlsx with columns:
    S4_Table | S4_Field | S47_Table | S47_Field
    """
    path = os.path.join(folder, "Product_Template.xlsx")
    rows = []
    for s4_table, fields in data.items():
        for s4_field, (src_table, src_field) in fields.items():
            rows.append({
                "S4_Table":  s4_table,
                "S4_Field":  s4_field,
                "S47_Table": src_table,
                "S47_Field": src_field,
            })
    pd.DataFrame(rows).to_excel(path, index=False)
    return path


class TestPipelineSession(unittest.TestCase):

    def test_initial_state(self):
        s = PipelineSession()
        self.assertEqual(s.sap_object, "")
        self.assertFalse(any(s.step_done.values()))
        self.assertEqual(s.log, [])
        self.assertEqual(s.errors, [])

    def test_log_msg_appended(self):
        s = PipelineSession()
        s.log_msg("Hello")
        self.assertEqual(len(s.log), 1)
        self.assertIn("Hello", s.log[0])

    def test_to_status_dict_keys(self):
        s = PipelineSession()
        d = s.to_status_dict()
        for key in ["sap_object","step_done","field_map_tables",
                    "transformed_tables","mapped_tables","log"]:
            self.assertIn(key, d)


class TestFullPipeline(unittest.TestCase):
    """
    Full end-to-end test:
    CSV sources → field template → value map → LTMC XML output.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

        # --- Source CSVs (legacy SAP 4.7 extract) ---
        mara = pd.DataFrame({
            "MATNR": ["1234",  "5678"],
            "MTART": ["VERP",  "ROH"],
            "MATKL": ["00101", "00102"],
        })
        makt = pd.DataFrame({
            "MATNR": ["1234",       "5678"],
            "SPRAS": ["E",          "E"],
            "MAKTX": ["Carton Box", "Raw Steel"],
        })
        self.mara_path = _write_csv(mara, self.tmp, "MARA.csv")
        self.makt_path = _write_csv(makt, self.tmp, "MAKT.csv")

        # --- Field mapping template ---
        self.tmpl_path = _write_template({
            "S_MARA": {
                "MATNR": ("MARA", "MATNR"),
                "MTART": ("MARA", "MTART"),
                "MAKTX": ("MAKT", "MAKTX"),
            }
        }, self.tmp)

        # --- Value mapping workbook ---
        self.vmap_path = _write_valuemap({
            "MTART": {"VERP": "Z001", "ROH": "Z002"},
        }, self.tmp)

    def _make_session(self):
        s = PipelineSession()
        s.sap_object      = "PRODUCT"
        s.preferred_lang  = "E"
        s.template_path   = self.tmpl_path
        s.value_map_path  = self.vmap_path
        s.source_paths    = {"MARA": self.mara_path, "MAKT": self.makt_path}
        return s

    # ── Extract ─────────────────────────────────────────────────────────

    def test_extract_step_passes(self):
        s  = self._make_session()
        ok = run_extract(s)
        self.assertTrue(ok)
        self.assertTrue(s.step_done["extract"])

    def test_extract_field_map_populated(self):
        s = self._make_session()
        run_extract(s)
        self.assertIn("S_MARA", s.field_map)

    def test_extract_legacy_tables_loaded(self):
        s = self._make_session()
        run_extract(s)
        self.assertIn("MARA", s.legacy_tables)
        self.assertIn("MAKT", s.legacy_tables)

    def test_extract_mara_matnr_padded(self):
        """MATNR '1234' must be padded to 18 chars after extract."""
        s = self._make_session()
        run_extract(s)
        matnr_vals = s.legacy_tables["MARA"]["MATNR"].tolist()
        self.assertIn("000000000000001234", matnr_vals)

    def test_extract_makt_english_only(self):
        """MAKT filtered to English rows only."""
        s = self._make_session()
        run_extract(s)
        self.assertTrue((s.legacy_tables["MAKT"]["SPRAS"] == "E").all())

    def test_extract_fails_without_template(self):
        s = PipelineSession()
        s.source_paths = {"MARA": self.mara_path}
        # No template_path set
        ok = run_extract(s)
        self.assertFalse(ok)

    # ── Transform ────────────────────────────────────────────────────────

    def test_transform_step_passes(self):
        s = self._make_session()
        run_extract(s)
        ok = run_transform(s)
        self.assertTrue(ok)
        self.assertTrue(s.step_done["transform"])

    def test_transform_produces_s_mara(self):
        s = self._make_session()
        run_extract(s)
        run_transform(s)
        self.assertIn("S_MARA", s.transformed)

    def test_transform_s_mara_has_data_rows(self):
        s = self._make_session()
        run_extract(s)
        run_transform(s)
        self.assertGreater(len(s.transformed["S_MARA"]), 0)

    def test_transform_maktx_filled_from_join(self):
        """MAKTX pulled from MAKT via join — must contain description text."""
        s = self._make_session()
        run_extract(s)
        run_transform(s)
        df    = s.transformed["S_MARA"]
        maktx_col = next((c for c in df.columns if "MAKTX" in c), None)
        self.assertIsNotNone(maktx_col)
        self.assertIn("Carton Box", df[maktx_col].tolist())

    # ── Value Map ────────────────────────────────────────────────────────

    def test_value_map_step_passes(self):
        s = self._make_session()
        run_extract(s)
        run_transform(s)
        ok = run_value_map(s)
        self.assertTrue(ok)
        self.assertTrue(s.step_done["value_map"])

    def test_value_map_mtart_transformed(self):
        """VERP → Z001, ROH → Z002 applied to MTART column."""
        s = self._make_session()
        run_extract(s)
        run_transform(s)
        run_value_map(s)
        df       = s.mapped["S_MARA"]
        mtart_col = next((c for c in df.columns if "MTART" in c), None)
        self.assertIsNotNone(mtart_col)
        vals = df[mtart_col].tolist()
        self.assertIn("Z001", vals)
        self.assertIn("Z002", vals)

    def test_value_map_no_unmapped_in_clean_run(self):
        """All MTART values have mapping — unmapped_summary must be empty."""
        s = self._make_session()
        run_extract(s)
        run_transform(s)
        run_value_map(s)
        self.assertEqual(len(s.unmapped_summary), 0)

    # ── Export ───────────────────────────────────────────────────────────

    def test_export_csv_creates_files(self):
        s = self._make_session()
        run_extract(s)
        run_transform(s)
        run_value_map(s)
        out_dir   = os.path.join(self.tmp, "output")
        out_files = run_export(s, out_dir, fmt="csv")
        self.assertTrue(s.step_done["export"])
        self.assertGreater(len(out_files), 0)
        for f in out_files:
            if f.endswith(".csv"):
                self.assertTrue(os.path.exists(f))

    def test_export_xlsx_creates_files(self):
        s = self._make_session()
        run_extract(s)
        run_transform(s)
        run_value_map(s)
        out_dir   = os.path.join(self.tmp, "output_xlsx")
        out_files = run_export(s, out_dir, fmt="xlsx")
        xlsx_files = [f for f in out_files if f.endswith(".xlsx")]
        self.assertGreater(len(xlsx_files), 0)
        for f in xlsx_files:
            self.assertTrue(os.path.exists(f))

    def test_export_csv_readable_with_correct_data(self):
        """CSV output must be readable and contain transformed data."""
        s = self._make_session()
        run_extract(s)
        run_transform(s)
        run_value_map(s)
        out_dir   = os.path.join(self.tmp, "output_verify")
        out_files = run_export(s, out_dir, fmt="csv")
        csv_files = [f for f in out_files if f.endswith(".csv") and "unmapped" not in f]
        self.assertGreater(len(csv_files), 0)
        df = pd.read_csv(csv_files[0], dtype=str)
        # Must have rows and columns
        self.assertGreater(len(df), 0)
        self.assertGreater(len(df.columns), 0)

    def test_unmapped_summary_csv_created_when_unmapped_exist(self):
        """If unmapped values exist, unmapped_summary.csv must be written."""
        # Add an unmapped MTART value
        mara = pd.DataFrame({
            "MATNR": ["1234"], "MTART": ["ZZZZ"], "MATKL": ["00101"]
        })
        mara_path = _write_csv(mara, self.tmp, "MARA_un.csv")

        s = self._make_session()
        s.source_paths["MARA"] = mara_path
        run_extract(s)
        run_transform(s)
        run_value_map(s)
        out_dir   = os.path.join(self.tmp, "output_un")
        out_files = run_export(s, out_dir, fmt="csv")
        summary_files = [f for f in out_files if "unmapped" in f]
        self.assertGreater(len(summary_files), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
