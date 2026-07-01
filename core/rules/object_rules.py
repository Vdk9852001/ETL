"""
Object-specific post-processing rules.
These were previously hardcoded in Python_Valuemapping.py.
Now driven by config/object_rules.json so new objects don't require code changes.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Callable
import pandas as pd

_RULES_FILE = Path(__file__).parent.parent.parent / "config" / "object_rules.json"


def load_object_rules(sap_object: str) -> dict:
    """Return the rules block for a given SAP object (e.g. 'PRODUCT')."""
    if not _RULES_FILE.exists():
        return {}
    try:
        all_rules = json.loads(_RULES_FILE.read_text(encoding="utf-8"))
        return all_rules.get(sap_object.upper(), {})
    except Exception:
        return {}


def apply_object_rules(
    df: pd.DataFrame,
    table_name: str,
    sap_object: str,
    log_fn: Callable[[str], None] = print,
) -> pd.DataFrame:
    """
    Apply all config-driven object-specific rules to a DataFrame.

    Replaces the hardcoded if/elif blocks in Python_Valuemapping.py:
        if file.startswith(("S_MBEW",...)):
            df = update_waers(df, file)
            df = update_mlast(df, file)
            df = update_curtp(df, file)
        if file.upper().startswith(("S_MATLWH",...)):
            df = update_entitled(df, file)
    """
    rules   = load_object_rules(sap_object)
    df      = df.copy()
    tname_u = table_name.upper()

    # ── WAERS (currency from plant) ─────────────────────────────────────────
    waers_rule = rules.get("waers")
    if waers_rule:
        triggers = [t.upper() for t in waers_rule.get("trigger_files", [])]
        if any(tname_u.startswith(t) for t in triggers):
            df = _apply_lookup_rule(
                df, table_name,
                key_col=waers_rule["key_col"],
                target_col=waers_rule["target_col"],
                mapping=waers_rule["mapping"],
                log_fn=log_fn,
            )

    # ── Constant fields (MLAST=3, CURTP=10, etc.) ───────────────────────────
    const_rule = rules.get("constants")
    if const_rule:
        triggers = [t.upper() for t in const_rule.get("trigger_files", [])]
        if any(tname_u.startswith(t) for t in triggers):
            for field, value in const_rule.get("fields", {}).items():
                col = _find_col_ending(df, field)
                if col:
                    df[col] = value
                    log_fn(f"  Constant: {col} = {value} in {table_name}")

    # ── ENTITLED (from LGNUM) ────────────────────────────────────────────────
    entitled_rule = rules.get("entitled")
    if entitled_rule:
        triggers = [t.upper() for t in entitled_rule.get("trigger_files", [])]
        if any(tname_u.startswith(t) for t in triggers):
            df = _apply_lookup_rule(
                df, table_name,
                key_col=entitled_rule["key_col"],
                target_col=entitled_rule["target_col"],
                mapping=entitled_rule["mapping"],
                log_fn=log_fn,
            )

    return df


def _find_col_ending(df: pd.DataFrame, suffix: str) -> str | None:
    """Find a column whose name ends with a given suffix (case-insensitive)."""
    for col in df.columns:
        if col.upper().endswith(suffix.upper()):
            return col
    return None


def _apply_lookup_rule(
    df: pd.DataFrame,
    table_name: str,
    key_col: str,
    target_col: str,
    mapping: dict,
    log_fn: Callable,
) -> pd.DataFrame:
    kc = _find_col_ending(df, key_col)
    tc = _find_col_ending(df, target_col)
    if not kc:
        log_fn(f"  Rule skipped: {key_col} column not found in {table_name}")
        return df
    if not tc:
        log_fn(f"  Rule skipped: {target_col} column not found in {table_name}")
        return df

    key_series = df[kc].astype(str).str.strip().str.upper()
    df[tc]     = key_series.map(mapping)
    unmapped   = key_series[df[tc].isna()].unique().tolist()
    if unmapped:
        log_fn(f"  Unmapped {key_col}→{target_col} in {table_name}: {unmapped}")
    else:
        log_fn(f"  {key_col}→{target_col} applied in {table_name}")
    return df
