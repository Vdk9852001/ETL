"""
Base transformation rules shared across all SAP objects.
normalize_matnr, detect_and_format_dates, preprocess_makt — 
lifted directly from Python_Transformation.py with the loop bug fixed.
"""
from __future__ import annotations
import pandas as pd


# Date fields that should never be auto-formatted even if they look like dates
DATE_EXCLUDE_FIELDS: set = {"S_MARA-MATKL"}


def normalize_matnr(df: pd.DataFrame, col: str = "MATNR", length: int = 18) -> pd.DataFrame:
    """
    Zero-pad MATNR to 18 chars — only when the value is purely numeric.
    Non-numeric values (e.g. TRADING-001) are left unchanged.
    Taken from Python_Transformation.py — logic is correct.
    """
    if col not in df.columns:
        return df

    def _pad(val):
        v = str(val).strip()
        return v.zfill(length) if v.isdigit() else v

    df = df.copy()
    df[col] = df[col].apply(_pad)
    return df


def preprocess_makt(df: pd.DataFrame, preferred_lang: str = "E") -> pd.DataFrame:
    """
    Filter MAKT (material descriptions) to keep only the preferred language.
    Then zero-pad MATNR.
    """
    df = normalize_matnr(df, "MATNR")
    if "SPRAS" in df.columns:
        df["SPRAS"] = df["SPRAS"].astype(str).str.strip().str.upper()
        df = df[df["SPRAS"] == preferred_lang.upper()]
    return df


def preprocess_generic(df: pd.DataFrame) -> pd.DataFrame:
    """Strip whitespace from all column names and string values."""
    df = df.copy()
    df.columns = df.columns.str.strip()
    for col in df.select_dtypes(include="object").columns:
        df[col] = df[col].astype(str).str.strip()
    return df


def detect_and_format_dates(
    df: pd.DataFrame,
    date_format: str = "%Y-%m-%d",
    placeholder_format: str = "9999/12/31",
) -> pd.DataFrame:
    """
    Auto-detect columns that look like YYYYMMDD dates (>80% match)
    and reformat them.

    BUG FIX from Python_Transformation.py:
    Original called this function inside the column loop — once per field.
    Now called ONCE per table after all columns are populated.
    """
    df = df.copy()
    for col in df.columns:
        if col in DATE_EXCLUDE_FIELDS:
            continue
        col_data = df[col].astype(str).str.strip()
        if col_data.str.match(r"^\d{8}$").mean() > 0.8:
            def _fmt(x):
                if x.startswith("9999"):
                    return placeholder_format
                try:
                    return pd.to_datetime(x, format="%Y%m%d", errors="raise").strftime(date_format)
                except Exception:
                    return x
            df[col] = col_data.apply(_fmt)
    return df


def apply_value_lookup(
    series: pd.Series,
    mapping_dict: dict,
    field_name: str = "",
) -> tuple[pd.Series, list]:
    """
    Apply a value lookup dict to a pandas Series.
    Returns (mapped_series, list_of_unmapped_values).

    Strips trailing .0 from numeric-string values before lookup
    (e.g. "0004.0" → "0004") — from Python_Valuemapping.py.
    """
    series_str = series.astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    mapped     = series_str.map(mapping_dict)

    # Collect values not in the mapping dict
    unmapped = sorted(
        series_str[~series_str.isin(mapping_dict.keys())].dropna().unique().tolist()
    )
    # Keep original values where no mapping exists (pass-through)
    mapped = mapped.where(mapped.notna(), series_str)

    return mapped, unmapped
