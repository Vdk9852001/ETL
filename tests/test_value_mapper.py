"""
Unit Tests — core/value_mapper.py
====================================
Run:  python -m pytest tests/test_value_mapper.py -v
"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
import pandas as pd
from core.value_mapper import load_value_mappings, apply_value_mappings


def write_valuemap_xlsx(data: dict, path: str):
    """
    data = {"KTOKD": {"0001":"BP01","0004":"SP01"}, "LAND1": {"DE":"Germany"}}
    Writes one sheet per key with SAP47_Value / S4_Value columns.
    """
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for sheet_name, vm in data.items():
            rows = [{"SAP47_Value": k, "S4_Value": v} for k, v in vm.items()]
            pd.DataFrame(rows).to_excel(w, sheet_name=sheet_name, index=False)


class TestLoadValueMappings(unittest.TestCase):

    def setUp(self):
        self.tmp  = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "ValueMapping.xlsx")

    def test_basic_load(self):
        """One sheet loaded — keys and values parsed correctly."""
        write_valuemap_xlsx({"KTOKD": {"0001": "BP01", "0004": "SP01"}}, self.path)
        vms = load_value_mappings(self.path)
        self.assertIn("KTOKD", vms)
        self.assertEqual(vms["KTOKD"]["0001"], "BP01")
        self.assertEqual(vms["KTOKD"]["0004"], "SP01")

    def test_multiple_sheets_loaded(self):
        """All sheets loaded into separate dict entries."""
        write_valuemap_xlsx({
            "KTOKD": {"0001": "BP01"},
            "LAND1": {"DE": "Germany", "US": "USA"},
        }, self.path)
        vms = load_value_mappings(self.path)
        self.assertIn("KTOKD", vms)
        self.assertIn("LAND1", vms)
        self.assertEqual(vms["LAND1"]["DE"], "Germany")

    def test_sheet_keys_uppercased(self):
        """Sheet names normalised to UPPER so 'ktokd' == 'KTOKD'."""
        write_valuemap_xlsx({"ktokd": {"0001": "BP01"}}, self.path)
        vms = load_value_mappings(self.path)
        self.assertIn("KTOKD", vms)


class TestApplyValueMappings(unittest.TestCase):

    def setUp(self):
        self.tmp  = tempfile.mkdtemp()
        self.vmap_path = os.path.join(self.tmp, "ValueMapping.xlsx")
        write_valuemap_xlsx({
            "KTOKD": {"0001": "BP01", "0004": "SP01"},
            "LAND1": {"DE": "Germany", "US": "USA"},
        }, self.vmap_path)
        self.value_maps = load_value_mappings(self.vmap_path)

    def _transformed(self):
        """Simulated output from transformer step."""
        return {
            "S_KNA1": pd.DataFrame({
                "S_KNA1-KUNNR": ["C001", "C002", "C003"],
                "S_KNA1-KTOKD": ["0001", "0004", "0001"],
                "S_KNA1-LAND1": ["DE",   "US",   "DE"],
            })
        }

    def test_ktokd_mapped_correctly(self):
        """0001 → BP01, 0004 → SP01 applied to S_KNA1-KTOKD."""
        mapped, _ = apply_value_mappings(
            self._transformed(), self.value_maps, "CUSTOMER",
            log_fn=lambda m: None
        )
        df = mapped["S_KNA1"]
        self.assertEqual(df["S_KNA1-KTOKD"].iloc[0], "BP01")
        self.assertEqual(df["S_KNA1-KTOKD"].iloc[1], "SP01")

    def test_land1_mapped_correctly(self):
        """DE → Germany, US → USA applied to S_KNA1-LAND1."""
        mapped, _ = apply_value_mappings(
            self._transformed(), self.value_maps, "CUSTOMER",
            log_fn=lambda m: None
        )
        df = mapped["S_KNA1"]
        self.assertEqual(df["S_KNA1-LAND1"].iloc[0], "Germany")
        self.assertEqual(df["S_KNA1-LAND1"].iloc[1], "USA")

    def test_unmapped_value_reported_in_summary(self):
        """Value not in mapping dict → reported in unmapped_summary list."""
        transformed = {
            "S_KNA1": pd.DataFrame({
                "S_KNA1-KTOKD": ["0001", "9999"],   # 9999 has no mapping
            })
        }
        _, summary = apply_value_mappings(
            transformed, self.value_maps, "CUSTOMER",
            log_fn=lambda m: None
        )
        self.assertEqual(len(summary), 1)
        self.assertIn("9999", summary[0]["values"])

    def test_unmapped_value_passed_through_unchanged(self):
        """Value not in mapping dict must remain in output, not blanked."""
        transformed = {
            "S_KNA1": pd.DataFrame({
                "S_KNA1-KTOKD": ["0001", "9999"],
            })
        }
        mapped, _ = apply_value_mappings(
            transformed, self.value_maps, "CUSTOMER",
            log_fn=lambda m: None
        )
        df = mapped["S_KNA1"]
        self.assertEqual(df["S_KNA1-KTOKD"].iloc[1], "9999")

    def test_column_with_no_mapping_sheet_untouched(self):
        """Column whose field has no sheet in ValueMapping.xlsx → unchanged."""
        transformed = {
            "S_KNA1": pd.DataFrame({
                "S_KNA1-KUNNR": ["C001", "C002"],   # no mapping sheet for KUNNR
            })
        }
        mapped, summary = apply_value_mappings(
            transformed, self.value_maps, "CUSTOMER",
            log_fn=lambda m: None
        )
        df = mapped["S_KNA1"]
        self.assertEqual(df["S_KNA1-KUNNR"].iloc[0], "C001")
        self.assertEqual(len(summary), 0)

    def test_column_key_extracted_from_suffix(self):
        """
        'S_KNA1-KTOKD' → lookup key 'KTOKD' derived by splitting on '-'
        and taking last part. Mapping must still apply.
        """
        transformed = {
            "S_KNA1": pd.DataFrame({"S_KNA1-KTOKD": ["0004"]})
        }
        mapped, _ = apply_value_mappings(
            transformed, self.value_maps, "CUSTOMER",
            log_fn=lambda m: None
        )
        self.assertEqual(mapped["S_KNA1"]["S_KNA1-KTOKD"].iloc[0], "SP01")

    def test_empty_transformed_tables_returns_empty(self):
        mapped, summary = apply_value_mappings(
            {}, self.value_maps, "CUSTOMER", log_fn=lambda m: None
        )
        self.assertEqual(len(mapped), 0)
        self.assertEqual(len(summary), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
