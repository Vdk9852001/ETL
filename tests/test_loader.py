"""
Unit Tests — core/loader.py
=============================
Tests that write_ltmc_xml produces valid SAP SpreadsheetML structure.
Run:  python -m pytest tests/test_loader.py -v
"""
import sys, os, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
import xml.etree.ElementTree as ET
import pandas as pd
from core.loader import write_ltmc_xml, tables_to_xml_bytes

SS_NS = "urn:schemas-microsoft-com:office:spreadsheet"
def tag(local): return f"{{{SS_NS}}}{local}"

def _parse_sheet(xml_path, sheet_name):
    """Parse a written XML and return all rows from the named worksheet."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for ws in root.findall(f".//{tag('Worksheet')}"):
        if ws.get(tag("Name")) == sheet_name:
            table = ws.find(tag("Table"))
            rows  = table.findall(tag("Row"))
            return rows
    return []

def _row_values(row_el):
    vals = []
    for cell in row_el.findall(tag("Cell")):
        data = cell.find(tag("Data"))
        vals.append(data.text or "" if data is not None else "")
    return vals


class TestWriteLtmcXml(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "output.xml")
        self.df  = pd.DataFrame({
            "MATNR": ["000000000000001234", "000000000000005678"],
            "MTART": ["VERP",               "ROH"],
            "MAKTX": ["Carton Box",          "Raw Steel"],
        })

    def test_file_created(self):
        write_ltmc_xml({"S_MARA": self.df}, self.out, sap_object="Product")
        self.assertTrue(os.path.exists(self.out))

    def test_worksheet_name_correct(self):
        """Worksheet Name attribute must match the table name key."""
        write_ltmc_xml({"S_MARA": self.df}, self.out, sap_object="Product")
        tree = ET.parse(self.out)
        root = tree.getroot()
        ws_names = [ws.get(tag("Name"))
                    for ws in root.findall(f".//{tag('Worksheet')}")]
        self.assertIn("S_MARA", ws_names)

    def test_row1_contains_object_name(self):
        """Row 1 must contain 'Source Data for Migration Object: Product'."""
        write_ltmc_xml({"S_MARA": self.df}, self.out, sap_object="Product")
        rows = _parse_sheet(self.out, "S_MARA")
        self.assertGreater(len(rows), 0)
        vals = _row_values(rows[0])
        self.assertTrue(any("Product" in v for v in vals))

    def test_row4_contains_table_name(self):
        """Row 4 must contain the SAP table name e.g. S_MARA."""
        write_ltmc_xml({"S_MARA": self.df}, self.out, sap_object="Product")
        rows = _parse_sheet(self.out, "S_MARA")
        vals = _row_values(rows[3])   # 0-indexed → row 4
        self.assertIn("S_MARA", vals)

    def test_row5_contains_field_names(self):
        """Row 5 must contain the DataFrame column names as field headers."""
        write_ltmc_xml({"S_MARA": self.df}, self.out, sap_object="Product")
        rows = _parse_sheet(self.out, "S_MARA")
        vals = _row_values(rows[4])   # row 5
        self.assertIn("MATNR", vals)
        self.assertIn("MTART", vals)
        self.assertIn("MAKTX", vals)

    def test_row6_contains_type_specs(self):
        """Row 6 must contain one type spec per column."""
        spec = "ETE;80;0;C;80;0"
        write_ltmc_xml({"S_MARA": self.df}, self.out,
                       sap_object="Product", type_spec=spec)
        rows = _parse_sheet(self.out, "S_MARA")
        vals = _row_values(rows[5])   # row 6
        self.assertEqual(len(vals), len(self.df.columns))
        self.assertTrue(all(v == spec for v in vals))

    def test_data_rows_start_at_row7(self):
        """Data rows begin at position 6 (0-indexed), i.e. row 7."""
        write_ltmc_xml({"S_MARA": self.df}, self.out, sap_object="Product")
        rows = _parse_sheet(self.out, "S_MARA")
        # rows[0..5] = header rows, rows[6+] = data
        data_rows = rows[6:]
        self.assertEqual(len(data_rows), len(self.df))
        # First data row
        vals = _row_values(data_rows[0])
        self.assertIn("000000000000001234", vals)
        self.assertIn("VERP", vals)

    def test_total_row_count(self):
        """Total rows = 6 header + N data rows."""
        write_ltmc_xml({"S_MARA": self.df}, self.out, sap_object="Product")
        rows = _parse_sheet(self.out, "S_MARA")
        self.assertEqual(len(rows), 6 + len(self.df))

    def test_multiple_sheets_in_one_file(self):
        """Multiple tables written as separate worksheets in one XML file."""
        mara = self.df.copy()
        makt = pd.DataFrame({"MATNR": ["000000000000001234"], "MAKTX": ["Carton Box"]})
        write_ltmc_xml({"S_MARA": mara, "S_MAKT": makt}, self.out, sap_object="Product")
        tree = ET.parse(self.out)
        root = tree.getroot()
        ws_names = [ws.get(tag("Name"))
                    for ws in root.findall(f".//{tag('Worksheet')}")]
        self.assertIn("S_MARA", ws_names)
        self.assertIn("S_MAKT", ws_names)

    def test_leading_zeros_preserved_in_xml(self):
        """
        MATNR with leading zeros must appear as string in XML —
        not converted to integer 1234.
        """
        write_ltmc_xml({"S_MARA": self.df}, self.out, sap_object="Product")
        content = Path(self.out).read_text(encoding="utf-8")
        self.assertIn("000000000000001234", content)
        # Must NOT appear as plain integer
        self.assertNotIn(">1234<", content)

    def test_output_is_valid_xml(self):
        """Written file must parse as valid XML without error."""
        write_ltmc_xml({"S_MARA": self.df}, self.out, sap_object="Product")
        try:
            ET.parse(self.out)
            valid = True
        except ET.ParseError:
            valid = False
        self.assertTrue(valid)

    def test_empty_dataframe_writes_headers_only(self):
        """Empty DataFrame → header rows written, zero data rows."""
        empty = pd.DataFrame(columns=["MATNR", "MTART"])
        write_ltmc_xml({"S_MARA": empty}, self.out, sap_object="Product")
        rows = _parse_sheet(self.out, "S_MARA")
        self.assertEqual(len(rows), 6)   # 6 header rows, 0 data


class TestTablesToXmlBytes(unittest.TestCase):

    def test_returns_bytes(self):
        df  = pd.DataFrame({"MATNR": ["1"], "MTART": ["VERP"]})
        out = tables_to_xml_bytes({"S_MARA": df}, sap_object="Product")
        self.assertIsInstance(out, bytes)

    def test_bytes_contain_xml_declaration(self):
        df  = pd.DataFrame({"MATNR": ["1"]})
        out = tables_to_xml_bytes({"S_MARA": df})
        self.assertIn(b"<?xml", out)

    def test_bytes_contain_data(self):
        df  = pd.DataFrame({"MATNR": ["000000000000001234"]})
        out = tables_to_xml_bytes({"S_MARA": df})
        self.assertIn(b"000000000000001234", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
