"""
LTMC XML Loader
================
Writes the final transformed DataFrame into a SAP Migration Cockpit
SpreadsheetML XML file — the exact format SAP LTMC expects for upload.

This is the REVERSE of ltmc_parser.py (which reads the XML).

Output structure per worksheet:
  Row 1  visible  : "Source Data for Migration Object: <object>"
  Row 2  visible  : "Version SAP S/4HANA Cloud"
  Row 3  visible  : blank spacer
  Row 4  visible  : SAP table name  e.g. "S_MARA"
  Row 5  visible  : SAP field names  PRODUCT, MTART, MAKTL...   ← HEADER
  Row 6  visible  : field type specs  ETE;80;0;C;80;0
  Row 7  visible  : data row 1
  Row 8  visible  : data row 2
  ...
"""
from __future__ import annotations
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from typing import Dict, List
import pandas as pd


SS_NS   = "urn:schemas-microsoft-com:office:spreadsheet"
O_NS    = "urn:schemas-microsoft-com:office:office"
X_NS    = "urn:schemas-microsoft-com:office:excel"
ET.register_namespace("ss", SS_NS)
ET.register_namespace("o",  O_NS)
ET.register_namespace("x",  X_NS)

def _tag(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"

def _cell(value: str, data_type: str = "String") -> ET.Element:
    cell = ET.Element("ss:Cell")
    data = ET.SubElement(cell, "ss:Data")
    data.set("ss:Type", data_type)
    data.text = str(value) if value is not None else ""
    return cell

def _row(values: List[str], data_type: str = "String") -> ET.Element:
    row = ET.Element("ss:Row")
    for v in values:
        row.append(_cell(v, data_type))
    return row


def write_ltmc_xml(
    tables:        Dict[str, pd.DataFrame],
    output_path:   str,
    sap_object:    str = "Product",
    version:       str = "SAP S/4HANA Cloud",
    type_spec:     str = "ETE;80;0;C;80;0",
) -> str:
    """
    Write one or more S4 tables into a single LTMC SpreadsheetML XML file.

    Parameters
    ----------
    tables      : {s4_table_name: DataFrame}  e.g. {"S_MARA": df_mara}
    output_path : where to write the .xml file
    sap_object  : displayed in row 1 header
    version     : displayed in row 2 header
    type_spec   : default field type spec written to row 6

    Returns
    -------
    str  path to the written file
    """
    # Root workbook — set only xmlns once; register_namespace handles ss:/o:/x: prefixes
    wb = ET.Element("ss:Workbook")
    wb.set("xmlns:ss", SS_NS)
    wb.set("xmlns:o",  O_NS)
    wb.set("xmlns:x",  X_NS)

    for table_name, df in tables.items():
        ws = ET.SubElement(wb, "ss:Worksheet")
        ws.set("ss:Name", table_name)

        table_el = ET.SubElement(ws, "ss:Table")

        # Row 1 — migration object header
        table_el.append(_row([
            f"Source Data for Migration Object: {sap_object}"
        ]))

        # Row 2 — version
        table_el.append(_row([f"Version {version}"]))

        # Row 3 — blank spacer
        table_el.append(_row([""]))

        # Row 4 — SAP table name
        table_el.append(_row([table_name]))

        # Row 5 — field names (column headers)
        columns = list(df.columns)
        table_el.append(_row(columns))

        # Row 6 — type specs (one per column, using default spec)
        table_el.append(_row([type_spec] * len(columns)))

        # Rows 7+ — data
        df_clean = df.fillna("").astype(str)
        for _, data_row in df_clean.iterrows():
            table_el.append(_row(list(data_row.values)))

    # Write to file
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    tree = ET.ElementTree(wb)
    ET.indent(tree, space="  ")

    with open(str(path), "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(f, encoding="unicode", xml_declaration=False)

    return str(path)


def tables_to_xml_bytes(
    tables:     Dict[str, pd.DataFrame],
    sap_object: str = "Product",
) -> bytes:
    """Return the XML as bytes (for Flask download response)."""
    import io, tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp_path = tmp.name
    write_ltmc_xml(tables, tmp_path, sap_object)
    content = open(tmp_path, "rb").read()
    os.unlink(tmp_path)
    return content
