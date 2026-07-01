"""
Value Mapper
=============
Applies value lookup tables from ValueMapping.xlsx to the transformed DataFrames.
Then applies object-specific post-processing rules (WAERS, MLAST, ENTITLED etc.)

Was: Python_Valuemapping.py
Changes:
  - No hardcoded paths
  - Object-specific rules driven by config/object_rules.json
  - Returns results + unmapped summary, nothing written to disk
  - Column key extraction made explicit (split on '-', take last part)
"""
from __future__ import annotations
import pandas as pd
from pathlib import Path
from typing import Dict, List, Callable

from core.rules.base_rules   import apply_value_lookup
from core.rules.object_rules import apply_object_rules


def load_value_mappings(path: str) -> Dict[str, dict]:
    """
    Load ValueMapping.xlsx.
    Each sheet = one SAP field name.
    Columns must contain 'SAP47_Value' and 'S4_Value'.

    Returns {FIELD_NAME: {old_value: new_value}}
    """
    mappings: Dict[str, dict] = {}
    xl = pd.ExcelFile(str(path))

    for sheet in xl.sheet_names:
        df = xl.parse(sheet, dtype=str)
        df.columns = [str(c).strip() for c in df.columns]

        # Find source and target columns (flexible naming)
        src_col = _find_col(df, ["SAP47_Value","Source_Value","Old_Value","From","Legacy"])
        tgt_col = _find_col(df, ["S4_Value","Target_Value","New_Value","To","S4"])

        if src_col and tgt_col:
            key = sheet.strip().upper()
            mappings[key] = dict(zip(
                df[src_col].astype(str).str.strip(),
                df[tgt_col].astype(str).str.strip(),
            ))

    return mappings


def apply_value_mappings(
    transformed_tables: Dict[str, pd.DataFrame],
    value_mappings: Dict[str, dict],
    sap_object: str,
    log_fn: Callable[[str], None] = print,
) -> tuple[Dict[str, pd.DataFrame], List[dict]]:
    """
    Apply all value lookups and object rules to every transformed table.

    Returns:
      mapped_tables    — {s4_table: fully_mapped_DataFrame}
      unmapped_summary — list of {table, column, values, count}
    """
    mapped_tables:    Dict[str, pd.DataFrame] = {}
    unmapped_summary: List[dict]              = []

    for table_name, df in transformed_tables.items():
        df       = df.copy()
        log_fn(f"\nValue mapping: {table_name}")
        mapped_cols   = 0
        unmapped_cols = 0

        # ── Apply value lookups per column ────────────────────────────────────
        for col in df.columns:
            # Derive lookup key:
            # "S_MARA-MATKL" → "MATKL"
            # "S_MBEW-BWKEY"  → "BWKEY"
            raw_key = str(col).strip().split("-")[-1].upper()

            # Special normalisation from original code
            # (WERK in various forms → WERK)
            lookup_key = "WERK" if "WERK" in raw_key else raw_key

            if lookup_key in value_mappings:
                mapped_series, unmapped = apply_value_lookup(
                    df[col], value_mappings[lookup_key], col
                )
                df[col] = mapped_series
                mapped_cols += 1

                if unmapped:
                    log_fn(f"  Unmapped in {col}: {unmapped}")
                    unmapped_summary.append({
                        "table":   table_name,
                        "column":  col,
                        "field":   lookup_key,
                        "values":  ", ".join(str(v) for v in unmapped),
                        "count":   len(unmapped),
                    })
            else:
                unmapped_cols += 1

        # ── Apply object-specific rules (WAERS, MLAST, ENTITLED etc.) ─────────
        df = apply_object_rules(df, table_name, sap_object, log_fn)

        log_fn(f"  Mapped cols: {mapped_cols}, No-mapping cols: {unmapped_cols}")
        mapped_tables[table_name] = df

    return mapped_tables, unmapped_summary


def _find_col(df: pd.DataFrame, candidates: List[str]) -> str | None:
    cols_lower = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in cols_lower:
            return cols_lower[cand.lower()]
    return None
