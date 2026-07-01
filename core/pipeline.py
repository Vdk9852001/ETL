"""
Pipeline Orchestrator
======================
Connects Extract → Transform → Value Map → Export into one in-memory flow.
The Flask app calls this module — no intermediate files written to disk.

State is held in a PipelineSession object passed between steps.
"""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import pandas as pd

from core.extractor    import parse_field_template
from core.transformer  import load_legacy_tables, transform
from core.value_mapper import load_value_mappings, apply_value_mappings


@dataclass
class PipelineSession:
    """All state for one ETL run. Lives in Flask app memory."""
    sap_object:        str = ""
    preferred_lang:    str = "E"

    # Uploaded file paths
    template_path:     str = ""
    value_map_path:    str = ""
    source_paths:      Dict[str, str] = field(default_factory=dict)  # {table: path}

    # Pipeline stages
    field_map:         dict = field(default_factory=dict)   # from extractor
    legacy_tables:     dict = field(default_factory=dict)   # raw loaded tables
    transformed:       dict = field(default_factory=dict)   # after field mapping
    mapped:            dict = field(default_factory=dict)   # after value mapping
    value_mappings:    dict = field(default_factory=dict)   # loaded from xlsx

    # Status
    log:               List[str] = field(default_factory=list)
    unmapped_summary:  List[dict] = field(default_factory=list)
    errors:            List[str] = field(default_factory=list)
    step_done:         Dict[str, bool] = field(default_factory=lambda: {
        "extract": False, "transform": False,
        "value_map": False, "export": False,
    })

    def log_msg(self, msg: str, level: str = "info"):
        ts  = datetime.now().strftime("%H:%M:%S")
        tag = {"info":"","warn":"⚠ ","error":"✗ "}.get(level,"")
        entry = f"[{ts}] {tag}{msg}"
        self.log.append(entry)
        print(entry)

    def to_status_dict(self) -> dict:
        return {
            "sap_object":      self.sap_object,
            "template_path":   Path(self.template_path).name if self.template_path else "",
            "value_map_path":  Path(self.value_map_path).name if self.value_map_path else "",
            "source_tables":   list(self.source_paths.keys()),
            "step_done":       self.step_done,
            "field_map_tables": list(self.field_map.keys()),
            "transformed_tables": list(self.transformed.keys()),
            "mapped_tables":   list(self.mapped.keys()),
            "unmapped_count":  len(self.unmapped_summary),
            "error_count":     len(self.errors),
            "log":             list(reversed(self.log[-50:])),
        }


# ── Step functions (called by Flask routes) ────────────────────────────────────

def run_extract(session: PipelineSession) -> bool:
    """Parse the field template and load legacy tables into session."""
    try:
        session.log_msg(f"EXTRACT — parsing field template...")
        session.field_map = parse_field_template(session.template_path)
        tables = list(session.field_map.keys())
        session.log_msg(f"Field map: {len(tables)} S4 tables, "
                        f"{sum(len(v) for v in session.field_map.values())} total fields")

        session.log_msg("Loading legacy source tables...")
        session.legacy_tables = load_legacy_tables(
            session.source_paths,
            preferred_lang=session.preferred_lang,
            log_fn=session.log_msg,
        )
        session.step_done["extract"] = True
        return True
    except Exception as e:
        session.errors.append(str(e))
        session.log_msg(f"EXTRACT failed: {e}", "error")
        return False


def run_transform(session: PipelineSession) -> bool:
    """Apply field mapping to produce filled S4 tables."""
    try:
        session.log_msg("TRANSFORM — applying field mappings and joins...")
        session.transformed = transform(
            field_map=session.field_map,
            legacy_tables=session.legacy_tables,
            sap_object=session.sap_object,
            log_fn=session.log_msg,
        )
        total_rows = sum(len(v) for v in session.transformed.values())
        session.log_msg(f"Transform complete: {len(session.transformed)} tables, "
                        f"{total_rows} total rows")
        session.step_done["transform"] = True
        return True
    except Exception as e:
        session.errors.append(str(e))
        session.log_msg(f"TRANSFORM failed: {e}", "error")
        return False


def run_value_map(session: PipelineSession) -> bool:
    """Apply value lookup tables and object-specific rules."""
    try:
        session.log_msg("VALUE MAP — loading mapping workbook...")
        session.value_mappings = load_value_mappings(session.value_map_path)
        session.log_msg(f"Value map loaded: {len(session.value_mappings)} field tables")

        session.log_msg("Applying value lookups and object rules...")
        session.mapped, session.unmapped_summary = apply_value_mappings(
            transformed_tables=session.transformed,
            value_mappings=session.value_mappings,
            sap_object=session.sap_object,
            log_fn=session.log_msg,
        )
        session.log_msg(f"Value map complete: {len(session.unmapped_summary)} unmapped groups")
        session.step_done["value_map"] = True
        return True
    except Exception as e:
        session.errors.append(str(e))
        session.log_msg(f"VALUE MAP failed: {e}", "error")
        return False


def run_export(
    session: PipelineSession,
    output_folder: str,
    fmt: str = "csv",
) -> List[str]:
    """Write final mapped tables to output files. Returns list of output paths."""
    out_dir   = Path(output_folder)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_files = []
    source    = session.mapped if session.mapped else session.transformed
    ts        = datetime.now().strftime("%Y%m%d_%H%M%S")

    for table_name, df in source.items():
        if fmt == "xlsx":
            fname = f"{table_name}_{ts}.xlsx"
            fpath = out_dir / fname
            df.to_excel(str(fpath), index=False)
        else:
            fname = f"{table_name}_{ts}.csv"
            fpath = out_dir / fname
            df.to_csv(str(fpath), index=False)
        out_files.append(str(fpath))
        session.log_msg(f"Exported: {fname} ({len(df)} rows)")

    # Export unmapped summary
    if session.unmapped_summary:
        summ_path = out_dir / f"unmapped_summary_{ts}.csv"
        pd.DataFrame(session.unmapped_summary).to_csv(str(summ_path), index=False)
        out_files.append(str(summ_path))
        session.log_msg(f"Unmapped summary: unmapped_summary_{ts}.csv")

    session.step_done["export"] = True
    return out_files
