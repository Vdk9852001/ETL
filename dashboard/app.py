"""
SAP ETL Tool — Flask Application
==================================
Runs on http://localhost:5002
"""
import sys, io, zipfile
from pathlib import Path
from datetime import datetime

from flask import (Flask, render_template, jsonify, request,
                   Response, send_file)
from werkzeug.utils import secure_filename
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from core.pipeline import (PipelineSession, run_extract,
                            run_transform, run_value_map, run_export)

app = Flask(__name__)

BASE_DIR    = Path(__file__).parent.parent
SOURCE_DIR  = BASE_DIR / "data" / "source"
TMPL_DIR    = BASE_DIR / "data" / "templates"
VMAP_DIR    = BASE_DIR / "data" / "value_maps"
OUTPUT_DIR  = BASE_DIR / "output"
for d in [SOURCE_DIR, TMPL_DIR, VMAP_DIR, OUTPUT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

ALLOWED = {".csv", ".xlsx", ".xls"}

# ── One session per server run (single-user tool) ─────────────────────────────
session = PipelineSession()


# ── UI ────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("etl.html")


# ── Status ────────────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    return jsonify(session.to_status_dict())


# ── File uploads ──────────────────────────────────────────────────────────────
@app.route("/api/upload/template", methods=["POST"])
def upload_template():
    f    = request.files.get("file")
    if not f: return jsonify({"error": "No file"}), 400
    safe = secure_filename(f.filename)
    if Path(safe).suffix.lower() not in ALLOWED:
        return jsonify({"error": "Upload .xlsx or .csv"}), 400
    path = TMPL_DIR / safe
    f.save(str(path))
    session.template_path = str(path)
    session.log_msg(f"Template uploaded: {safe}")
    return jsonify({"ok": True, "file": safe})


@app.route("/api/upload/valuemap", methods=["POST"])
def upload_valuemap():
    f    = request.files.get("file")
    if not f: return jsonify({"error": "No file"}), 400
    safe = secure_filename(f.filename)
    path = VMAP_DIR / safe
    f.save(str(path))
    session.value_map_path = str(path)
    session.log_msg(f"Value map uploaded: {safe}")
    # Preview sheets
    try:
        xl     = pd.ExcelFile(str(path))
        sheets = xl.sheet_names
        session.log_msg(f"Value map sheets: {', '.join(sheets)}")
        return jsonify({"ok": True, "file": safe, "sheets": sheets})
    except Exception as e:
        return jsonify({"ok": True, "file": safe, "error": str(e)})


@app.route("/api/upload/source", methods=["POST"])
def upload_source():
    files   = request.files.getlist("files")
    if not files: return jsonify({"error": "No files"}), 400
    saved   = []
    for f in files:
        safe = secure_filename(f.filename)
        if Path(safe).suffix.lower() not in ALLOWED:
            continue
        path = SOURCE_DIR / safe
        f.save(str(path))
        name = Path(safe).stem.upper()
        session.source_paths[name] = str(path)
        saved.append({"name": name, "file": safe})
        session.log_msg(f"Source uploaded: {safe} → table {name}")
    return jsonify({"ok": True, "saved": saved,
                    "all_tables": list(session.source_paths.keys())})


@app.route("/api/source/remove/<name>", methods=["DELETE"])
def remove_source(name):
    session.source_paths.pop(name.upper(), None)
    return jsonify({"ok": True, "remaining": list(session.source_paths.keys())})


# ── Config ────────────────────────────────────────────────────────────────────
@app.route("/api/config", methods=["POST"])
def set_config():
    data = request.get_json(force=True)
    if "sap_object"     in data: session.sap_object     = data["sap_object"].strip().upper()
    if "preferred_lang" in data: session.preferred_lang = data["preferred_lang"].strip().upper()
    return jsonify({"ok": True, "sap_object": session.sap_object,
                    "preferred_lang": session.preferred_lang})


# ── Pipeline steps ────────────────────────────────────────────────────────────
@app.route("/api/run/extract", methods=["POST"])
def api_extract():
    if not session.template_path:
        return jsonify({"error": "Upload a field mapping template first"}), 400
    if not session.source_paths:
        return jsonify({"error": "Upload at least one source table"}), 400
    ok = run_extract(session)
    if not ok:
        return jsonify({"error": session.errors[-1]}), 500

    # Return preview of field map
    preview = {}
    for table, fields in session.field_map.items():
        preview[table] = [
            {"s4_field": f["s4_field"],
             "src":      f"{f['src_table']}.{f['src_field']}"}
            for f in fields[:5]
        ]
    return jsonify({
        "ok":     True,
        "tables": list(session.field_map.keys()),
        "field_count": sum(len(v) for v in session.field_map.values()),
        "preview": preview,
        "loaded_tables": [
            {"name": k, "rows": len(v), "cols": len(v.columns)}
            for k, v in session.legacy_tables.items()
        ],
    })


@app.route("/api/run/transform", methods=["POST"])
def api_transform():
    if not session.step_done.get("extract"):
        return jsonify({"error": "Run Extract step first"}), 400
    ok = run_transform(session)
    if not ok:
        return jsonify({"error": session.errors[-1]}), 500

    tables_info = [
        {"name": k, "rows": len(v), "cols": len(v.columns),
         "columns": list(v.columns[:8])}
        for k, v in session.transformed.items()
    ]
    return jsonify({"ok": True, "tables": tables_info})


@app.route("/api/run/valuemap", methods=["POST"])
def api_value_map():
    if not session.step_done.get("transform"):
        return jsonify({"error": "Run Transform step first"}), 400
    if not session.value_map_path:
        return jsonify({"error": "Upload a value mapping workbook first"}), 400
    ok = run_value_map(session)
    if not ok:
        return jsonify({"error": session.errors[-1]}), 500

    return jsonify({
        "ok":              True,
        "tables":          list(session.mapped.keys()),
        "unmapped_count":  len(session.unmapped_summary),
        "unmapped_summary": session.unmapped_summary[:30],
    })


@app.route("/api/run/export", methods=["POST"])
def api_export():
    data = request.get_json(force=True) or {}
    fmt  = data.get("format", "csv")
    out_files = run_export(session, str(OUTPUT_DIR), fmt=fmt)
    return jsonify({"ok": True, "files": [Path(f).name for f in out_files]})


# ── Preview endpoints ──────────────────────────────────────────────────────────
@app.route("/api/preview/<stage>/<table>")
def api_preview(stage, table):
    """Return first 20 rows of a table at a given pipeline stage."""
    stores = {
        "raw":       session.legacy_tables,
        "transform": session.transformed,
        "mapped":    session.mapped,
    }
    store = stores.get(stage, {})
    df    = store.get(table.upper())
    if df is None:
        return jsonify({"error": f"Table {table} not found at stage {stage}"}), 404
    return jsonify({
        "table":   table,
        "stage":   stage,
        "rows":    len(df),
        "columns": list(df.columns),
        "data":    df.head(20).fillna("").to_dict("records"),
    })


@app.route("/api/preview/field-map")
def api_preview_fieldmap():
    """Return the full parsed field map."""
    result = {}
    for table, fields in session.field_map.items():
        result[table] = fields
    return jsonify(result)


@app.route("/api/unmapped")
def api_unmapped():
    return jsonify({
        "summary":  session.unmapped_summary,
        "count":    len(session.unmapped_summary),
        "by_table": _group_unmapped(session.unmapped_summary),
    })


def _group_unmapped(summary):
    grouped = {}
    for item in summary:
        tbl = item["table"]
        grouped.setdefault(tbl, []).append(item)
    return grouped


# ── Download ───────────────────────────────────────────────────────────────────
@app.route("/api/download/<filename>")
def download_file(filename):
    path = OUTPUT_DIR / secure_filename(filename)
    if not path.exists():
        return jsonify({"error": "File not found"}), 404
    return send_file(str(path), as_attachment=True, download_name=filename)


@app.route("/api/download/all")
def download_all():
    """Download all output files as a zip."""
    files = list(OUTPUT_DIR.glob("*.csv")) + list(OUTPUT_DIR.glob("*.xlsx"))
    if not files:
        return jsonify({"error": "No output files yet"}), 404
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(str(f), f.name)
    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(buf, as_attachment=True,
                     download_name=f"ETL_Output_{ts}.zip",
                     mimetype="application/zip")


@app.route("/api/download/log")
def download_log():
    content = "\n".join(session.log)
    return Response(content.encode("utf-8"), mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=etl_run.log"})


# ── Reset ──────────────────────────────────────────────────────────────────────
@app.route("/api/reset", methods=["POST"])
def api_reset():
    global session
    session = PipelineSession()
    return jsonify({"ok": True})


@app.route("/api/objects")
def api_objects():
    """List available SAP objects from config."""
    import json
    jf = BASE_DIR / "config" / "join_rules.json"
    if jf.exists():
        objs = list(json.loads(jf.read_text()).keys())
    else:
        objs = ["PRODUCT","CUSTOMER","VENDOR","MATERIAL"]
    return jsonify(objs)


if __name__ == "__main__":
    print("SAP ETL Tool starting at http://localhost:5002")
    app.run(debug=True, host="0.0.0.0", port=5002)
