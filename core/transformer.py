"""
Transformer
============
Loads legacy source tables and fills the S4 target structure using
the field mapping produced by extractor.py.

Was: Python_Transformation.py
Changes:
  - No hardcoded paths, no hardcoded join_rules dict
  - Join rules loaded from config/join_rules.json
  - detect_and_format_dates called ONCE per table (bug fix from original)
  - MAKT language filter configurable, not hardcoded to 'E'
  - Returns DataFrames per S4_Table, nothing written to disk here
"""
from __future__ import annotations
import json
import pandas as pd
from pathlib import Path
from typing import Dict, List, Callable

from core.rules.base_rules import (
    normalize_matnr, preprocess_makt, preprocess_generic,
    detect_and_format_dates,
)

_JOIN_RULES_FILE = Path(__file__).parent.parent / "config" / "join_rules.json"


def load_join_rules(sap_object: str) -> dict:
    """Load join rules for a specific SAP object from config/join_rules.json."""
    if not _JOIN_RULES_FILE.exists():
        return {}
    try:
        all_rules = json.loads(_JOIN_RULES_FILE.read_text(encoding="utf-8"))
        return all_rules.get(sap_object.upper(), {})
    except Exception:
        return {}


def load_legacy_tables(
    source_paths: Dict[str, str],
    preferred_lang: str = "E",
    log_fn: Callable[[str], None] = print,
) -> Dict[str, pd.DataFrame]:
    """
    Load all legacy source tables from uploaded file paths.

    source_paths = {"MARA": "/path/MARA.csv", "MAKT": "/path/MAKT.csv", ...}

    Returns {table_name: DataFrame} with:
      - whitespace stripped from all values
      - MATNR zero-padded where purely numeric
      - MAKT filtered to preferred language
    """
    tables: Dict[str, pd.DataFrame] = {}

    for name, path in source_paths.items():
        name_u = name.upper()
        try:
            p = str(path)
            if p.lower().endswith(".csv"):
                df = pd.read_csv(p, dtype=str, encoding="utf-8-sig",
                                 on_bad_lines="skip", na_filter=False)
            else:
                df = pd.read_excel(p, dtype=str, na_values=[],
                                   keep_default_na=False)

            df = preprocess_generic(df)

            if name_u == "MAKT":
                df = preprocess_makt(df, preferred_lang)
            elif "MATNR" in df.columns:
                df = normalize_matnr(df, "MATNR")

            tables[name_u] = df
            log_fn(f"Loaded {name_u}: {len(df)} rows, {len(df.columns)} cols")
        except Exception as e:
            log_fn(f"ERROR loading {name}: {e}")

    return tables


def transform(
    field_map: Dict[str, List[dict]],
    legacy_tables: Dict[str, pd.DataFrame],
    sap_object: str,
    log_fn: Callable[[str], None] = print,
) -> Dict[str, pd.DataFrame]:
    """
    Fill each S4 target table using the field map and legacy source tables.

    field_map = {
      "S_MARA": [
        {"s4_field":"S_MARA-MATNR","src_table":"MARA","src_field":"MATNR"},
        ...
      ]
    }

    Returns {s4_table: filled_DataFrame}
    """
    join_rules    = load_join_rules(sap_object)
    results: Dict[str, pd.DataFrame] = {}

    for s4_table, field_list in field_map.items():
        log_fn(f"\nFilling {s4_table} ({len(field_list)} fields)...")
        filled = _fill_table(
            s4_table, field_list, legacy_tables, join_rules, log_fn
        )
        if filled is not None and not filled.empty:
            # BUG FIX: date formatting called ONCE per table, not per column
            filled         = detect_and_format_dates(filled)
            results[s4_table] = filled
            log_fn(f"  → {len(filled)} rows filled")

    return results


def _fill_table(
    s4_table: str,
    field_list: List[dict],
    legacy_tables: Dict[str, pd.DataFrame],
    join_rules: dict,
    log_fn: Callable,
) -> pd.DataFrame | None:
    """Fill one S4 table from legacy sources."""
    filled      = pd.DataFrame()
    merge_cache = {}   # avoid re-merging same table pair

    for fdef in field_list:
        s4_field  = fdef["s4_field"]
        src_table = fdef["src_table"].upper()
        src_field = fdef["src_field"]

        # ── Case 1: source table needs a JOIN ─────────────────────────────────
        if src_table in join_rules:
            rule      = join_rules[src_table]
            base_name = rule["base"].upper()
            cache_key = f"{base_name}__{src_table}"

            if cache_key not in merge_cache:
                base_df = legacy_tables.get(base_name)
                join_df = legacy_tables.get(src_table)

                if base_df is None or join_df is None:
                    log_fn(f"  SKIP {s4_field}: missing {base_name} or {src_table}")
                    merge_cache[cache_key] = None
                else:
                    lk = rule["left_key"]
                    rk = rule["right_key"]
                    jdf = join_df.copy()

                    # Deduplicate join table on right key before merge
                    if rk in jdf.columns:
                        jdf = jdf.drop_duplicates(subset=[rk])
                        jdf[rk] = jdf[rk].str.strip().str.upper()
                    if lk in base_df.columns:
                        base_df = base_df.copy()
                        base_df[lk] = base_df[lk].str.strip().str.upper()

                    merged = pd.merge(base_df, jdf,
                                      left_on=lk, right_on=rk, how="left")
                    merge_cache[cache_key] = merged
                    log_fn(f"  Joined {base_name} ↔ {src_table} on {lk}={rk}")

            merged_df = merge_cache[cache_key]
            if merged_df is not None and src_field in merged_df.columns:
                filled[s4_field] = merged_df[src_field].astype(str).str.strip()
            else:
                filled[s4_field] = None

        # ── Case 2: direct lookup from source table ───────────────────────────
        else:
            src_df = legacy_tables.get(src_table)
            if src_df is not None and src_field in src_df.columns:
                if filled.empty or len(filled) == len(src_df):
                    filled[s4_field] = src_df[src_field].astype(str).str.strip()
                else:
                    # Length mismatch — try to align on MATNR if available
                    log_fn(f"  WARN: row count mismatch for {s4_field} "
                           f"(filled={len(filled)}, src={len(src_df)})")
                    filled[s4_field] = src_df[src_field].reset_index(drop=True).astype(str).str.strip()
            else:
                if src_df is None:
                    log_fn(f"  SKIP {s4_field}: table {src_table} not loaded")
                else:
                    log_fn(f"  SKIP {s4_field}: col {src_field} not in {src_table}")
                filled[s4_field] = None

    return filled if not filled.empty else None
