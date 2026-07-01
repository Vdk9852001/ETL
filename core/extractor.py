"""
Extractor
==========
Reads the field mapping template Excel file (Product_Template.xlsx)
and builds the field map used by the transformer.

Was: Python_Fieldmapping.py
Changes:
  - No hardcoded paths
  - Returns data instead of writing CSVs
  - Groups by S4_Table and returns structured dict
"""
from __future__ import annotations
import pandas as pd
from pathlib import Path
from typing import Dict, List


# Values that mean "no mapping exists for this field"
INVALID_VALUES = {
    "no", "n/a", "tbd", "", "none", "nan",
    "no direct mapping from 4.7",
    "no direct mapping",
    "not applicable",
}


def parse_field_template(path: str) -> Dict[str, List[dict]]:
    """
    Read Product_Template.xlsx and return a dict:
    {
      "S_MARA": [
        {"s4_field": "S_MARA-MATNR", "src_table": "MARA", "src_field": "MATNR"},
        ...
      ],
      "S_MAKT": [...],
    }

    Replaces the grouped CSV output of Python_Fieldmapping.py.
    Data stays in memory — no intermediate files written.
    """
    df = pd.read_excel(str(path), dtype=str)
    df.columns = df.columns.str.strip()

    # Support both column naming conventions
    col_map = {
        "S4_Table":   _find_col(df, ["S4_Table",  "S4 Table",  "Target Table"]),
        "S4_Field":   _find_col(df, ["S4_Field",  "S4 Field",  "Target Field"]),
        "S47_Table":  _find_col(df, ["S47_Table", "SAP47 Table","Source Table","Legacy Table"]),
        "S47_Field":  _find_col(df, ["S47_Field", "SAP47 Field","Source Field","Legacy Field"]),
    }

    missing = [k for k, v in col_map.items() if v is None]
    if missing:
        raise ValueError(
            f"Could not find columns {missing} in template. "
            f"Available: {list(df.columns)}"
        )

    # Rename to standard names
    df = df.rename(columns={v: k for k, v in col_map.items() if v})
    df = df[["S4_Table","S4_Field","S47_Table","S47_Field"]]

    # Filter invalid mappings
    for col in ["S47_Table", "S47_Field"]:
        df = df[~df[col].str.strip().str.lower().isin(INVALID_VALUES)]
        df = df[df[col].notna()]

    df = df.dropna(subset=["S4_Table","S4_Field"])
    df = df.apply(lambda c: c.str.strip())

    # Build result dict grouped by S4_Table
    result: Dict[str, List[dict]] = {}
    for _, row in df.iterrows():
        table = row["S4_Table"].strip()
        result.setdefault(table, []).append({
            "s4_field":  f"{row['S4_Table']}-{row['S4_Field']}",
            "s4_col":    row["S4_Field"],
            "src_table": row["S47_Table"].strip().upper(),
            "src_field": row["S47_Field"].strip(),
        })

    return result


def list_source_files(folder: str) -> List[str]:
    """List all CSV files in the source legacy folder."""
    return [
        f.name for f in Path(folder).iterdir()
        if f.suffix.lower() in {".csv", ".xlsx", ".xls"}
    ]


def _find_col(df: pd.DataFrame, candidates: List[str]) -> str | None:
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None
