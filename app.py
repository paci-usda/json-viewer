#!/usr/bin/env python3
"""JSON Log Viewer – a simple local web app to view and filter JSON logs.

Usage:
    pip install flask
    python app.py
    # then open http://localhost:5000 in your browser
"""

import csv
import hashlib
import io
import json
import re

from flask import (
    Flask,
    flash,
    get_flashed_messages,
    make_response,
    redirect,
    render_template,
    request,
)

app = Flask(__name__)
app.secret_key = "json-viewer-local"  # only needed for flash messages

# ---------------------------------------------------------------------------
# Global in-memory state (suitable for a single-user local tool)
# ---------------------------------------------------------------------------

_records: list = []           # all loaded records, in load order
_all_fields: list = []        # field names in first-seen discovery order
_included_fields: list = []   # ordered subset of fields to display
_display_mode: str = "columns"  # "columns" | "lists" | "raw"
_sort_keys: list = []         # [[field, "asc"|"desc"], …] – newest-first
_filters: dict = {}           # field → {"pattern": str, "mode": "include"|"exclude"}
_truncate_limits: dict = {}   # field → max displayed chars
_column_widths: dict = {}     # field → max display width in chars
_uploaded_files: dict = {}    # sha256 → set(filename)
_default_truncate_limit: int = 50
_default_column_width: int = 100
_display_mode_options: list[tuple[str, str]] = [
    ("columns", "Table / Columns"),
    ("lists", "Individual Cards"),
    ("raw", "Raw JSON"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _refresh_fields(new_records: list) -> None:
    """Add any newly-seen fields to _all_fields / _included_fields."""
    seen = set(_all_fields)
    for rec in new_records:
        for key in rec:
            if key not in seen:
                seen.add(key)
                _all_fields.append(key)
                _included_fields.append(key)


def _compute_display(records: list) -> tuple[list, dict]:
    """Return (display_records, filter_errors) after applying filters and sorts.

    filter_errors maps field names to regexp compilation/execution error strings,
    e.g. {"level": "nothing to repeat at position 0"}.
    """
    errors: dict = {}

    # 1. Apply filters
    out: list = []
    for rec in records:
        keep = True
        for field, filt in _filters.items():
            pat = filt.get("pattern", "").strip()
            if not pat:
                continue
            mode = filt.get("mode", "include")
            val = str(rec.get(field, ""))
            try:
                matched = bool(re.search(pat, val))
            except re.error as exc:
                errors[field] = str(exc)
                matched = False
            if mode == "include" and not matched:
                keep = False
                break
            if mode == "exclude" and matched:
                keep = False
                break
        if keep:
            out.append(rec)

    # 2. Apply sort keys from *oldest* to *newest* using Python's stable sort,
    #    so the most-recently requested key becomes the primary sort key while
    #    older keys act as tie-breakers.
    for field, order in reversed(_sort_keys):
        rev = order == "desc"
        out.sort(
            key=lambda r, f=field: (r.get(f) is None, str(r.get(f, ""))),
            reverse=rev,
        )

    return out, errors


def _stringify_value(value) -> str:
    """Return a readable string for a record value."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    if value is None:
        return ""
    return str(value)


def _display_value(value, limit: int | None = None) -> str:
    """Return a display string, optionally truncated to ``limit`` characters."""
    text = _stringify_value(value)
    if limit and limit > 0 and len(text) > limit:
        return f"{text[:limit]}…"
    return text


def _measure_text_width(text: str) -> int:
    """Return the widest line length in ``text``."""
    return max((len(line) for line in text.splitlines()), default=0)


def _resolve_column_widths(
    records: list,
    fields: list,
    truncate_limits: dict,
    column_widths: dict,
) -> dict:
    """Return effective character widths for each field."""
    widths: dict = {}
    for field in fields:
        explicit_width = column_widths.get(field)
        if explicit_width:
            widths[field] = explicit_width
            continue

        width = _measure_text_width(field)
        for rec in records:
            width = max(
                width,
                _measure_text_width(
                    _display_value(rec.get(field, ""), truncate_limits.get(field))
                ),
            )
        widths[field] = max(1, min(_default_column_width, width))
    return widths


def _compute_column_statistics(records: list, field: str, truncate_limit: int | None = None) -> dict:
    """Return summary statistics for a field across the displayed records."""
    values = [_stringify_value(rec.get(field, "")) for rec in records]
    displayed_values = [_display_value(rec.get(field, ""), truncate_limit) for rec in records]

    if not values:
        return {
            "unique_count": 0,
            "displayed_unique_count": 0,
            "min_value": "",
            "max_value": "",
            "min_length": 0,
            "max_length": 0,
        }

    lengths = [len(value) for value in values]
    return {
        "unique_count": len(set(values)),
        "displayed_unique_count": len(set(displayed_values)),
        "min_value": min(values),
        "max_value": max(values),
        "min_length": min(lengths),
        "max_length": max(lengths),
    }


def _parse_positive_int(raw_value: str) -> int | None:
    """Return a positive integer from form input, or None if blank."""
    text = raw_value.strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError("must be numeric") from exc
    if value < 1:
        raise ValueError("must be positive")
    return value


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def index():
    global _records, _all_fields, _included_fields
    global _display_mode, _sort_keys, _filters, _truncate_limits, _column_widths

    if request.method == "POST":
        action = request.form.get("action", "")

        # ── Load data ────────────────────────────────────────────────────────
        if action == "load":
            load_mode = request.form.get("load_mode", "replace")
            raw_sources: list[tuple[str, str]] = []
            duplicate_uploads: list[str] = []
            uploads = [f for f in request.files.getlist("files") if f and f.filename]
            if not uploads:
                uploaded = request.files.get("file")
                if uploaded and uploaded.filename:
                    uploads = [uploaded]

            for uploaded in uploads:
                data = uploaded.read()
                digest = hashlib.sha256(data).hexdigest()
                previous_names = _uploaded_files.get(digest)
                if previous_names:
                    duplicate_uploads.append(uploaded.filename)
                    previous_names.add(uploaded.filename)
                else:
                    _uploaded_files[digest] = {uploaded.filename}
                raw_sources.append(
                    (uploaded.filename or "Uploaded file", data.decode("utf-8", errors="replace"))
                )

            if not raw_sources:
                raw_sources.append(("Pasted text", request.form.get("json_text", "")))

            new_recs: list = []
            parse_errors: list = []
            for source_name, raw in raw_sources:
                for i, line in enumerate(raw.splitlines(), 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        new_recs.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        parse_errors.append(f"{source_name} line {i}: {exc}")

            if parse_errors:
                # Report all parse errors; still load any records that did parse
                summary = "; ".join(parse_errors[:5])
                if len(parse_errors) > 5:
                    summary += f" … and {len(parse_errors) - 5} more error(s)"
                flash(f"Parse error(s) – {summary}", "error")

            if duplicate_uploads:
                summary = ", ".join(duplicate_uploads[:5])
                if len(duplicate_uploads) > 5:
                    summary += f" … and {len(duplicate_uploads) - 5} more"
                flash(f"Already uploaded before: {summary}", "warning")

            if new_recs:
                if load_mode == "replace":
                    _records = []
                    _all_fields = []
                    _included_fields = []
                    _sort_keys = []
                    _filters = {}
                    _truncate_limits = {}
                    _column_widths = {}
                _records.extend(new_recs)
                _refresh_fields(new_recs)
                flash(
                    f"Loaded {len(new_recs)} record(s). {len(_records)} total.",
                    "info",
                )

        # ── Field settings ───────────────────────────────────────────────────
        elif action == "fields":
            checked = set(request.form.getlist("field_check"))
            raw_order = request.form.get("field_order", "")
            if raw_order:
                ordered = [f for f in raw_order.split(",") if f in _all_fields]
                # Append any fields missing from the order list
                ordered += [f for f in _all_fields if f not in ordered]
            else:
                ordered = list(_all_fields)
            _included_fields = [f for f in ordered if f in checked]
            next_truncate_limits: dict = {}
            next_column_widths: dict = {}
            invalid_truncates: list[str] = []
            invalid_widths: list[str] = []
            for field in _all_fields:
                try:
                    truncate_value = _parse_positive_int(
                        request.form.get(f"field_truncate_{field}", "")
                    )
                except ValueError:
                    invalid_truncates.append(field)
                else:
                    if truncate_value is not None:
                        next_truncate_limits[field] = truncate_value

                try:
                    width_value = _parse_positive_int(
                        request.form.get(f"field_width_{field}", "")
                    )
                except ValueError:
                    invalid_widths.append(field)
                else:
                    if width_value is not None:
                        next_column_widths[field] = width_value

            _truncate_limits = next_truncate_limits
            _column_widths = next_column_widths
            if invalid_truncates:
                flash(
                    "Truncate Display values must be positive whole numbers: "
                    + ", ".join(invalid_truncates[:5]),
                    "error",
                )
            if invalid_widths:
                flash(
                    "Width values must be positive whole numbers: "
                    + ", ".join(invalid_widths[:5]),
                    "error",
                )

        # ── Display mode ─────────────────────────────────────────────────────
        elif action == "display":
            _display_mode = request.form.get("display_mode", "columns")

        # ── Sort ─────────────────────────────────────────────────────────────
        elif action == "sort":
            field = request.form.get("sort_field", "").strip()
            order = request.form.get("sort_order", "asc")
            if field:
                # Remove existing entry for this field, then prepend so it
                # becomes the newest (primary) sort key.
                _sort_keys = [[f, o] for f, o in _sort_keys if f != field]
                _sort_keys.insert(0, [field, order])

        # ── Clear sort ───────────────────────────────────────────────────────
        elif action == "clear_sort":
            _sort_keys = []

        # ── Column truncation ────────────────────────────────────────────────
        elif action == "truncate":
            field = request.form.get("truncate_field", "").strip()
            clear = request.form.get("truncate_clear") == "1"
            if field:
                if clear:
                    _truncate_limits.pop(field, None)
                else:
                    raw_limit = request.form.get("truncate_limit", "").strip()
                    try:
                        limit = _parse_positive_int(raw_limit)
                        if limit is None:
                            raise ValueError
                    except ValueError:
                        flash("Truncate limit must be a positive whole number.", "error")
                    else:
                        _truncate_limits[field] = limit

        # ── Apply filters ────────────────────────────────────────────────────
        elif action == "filter":
            _filters = {}
            for field in _all_fields:
                pat = request.form.get(f"fpat_{field}", "").strip()
                mode = request.form.get(f"fmode_{field}", "include")
                if pat:
                    _filters[field] = {"pattern": pat, "mode": mode}

        # ── Clear filters ────────────────────────────────────────────────────
        elif action == "clear_filters":
            _filters = {}

        # ── Download displayed data ──────────────────────────────────────────
        elif action == "download":
            export_format = request.form.get("export_format", "json")
            display_records, _ = _compute_display(_records)
            export_fields = _included_fields or _all_fields
            if export_format == "json":
                payload = json.dumps(display_records, indent=2)
                mimetype = "application/json"
                extension = "json"
            elif export_format in {"csv", "tsv"}:
                delimiter = "," if export_format == "csv" else "\t"
                payload_buffer = io.StringIO()
                writer = csv.writer(payload_buffer, delimiter=delimiter)
                writer.writerow(export_fields)
                for rec in display_records:
                    writer.writerow(
                        [_stringify_value(rec.get(field, "")) for field in export_fields]
                    )
                payload = payload_buffer.getvalue()
                mimetype = (
                    "text/csv"
                    if export_format == "csv"
                    else "text/tab-separated-values"
                )
                extension = export_format
            else:
                flash("Unknown download format requested.", "error")
                return redirect("/")

            response = make_response(payload)
            response.headers["Content-Type"] = f"{mimetype}; charset=utf-8"
            response.headers["Content-Disposition"] = (
                f'attachment; filename="json-viewer-export.{extension}"'
            )
            return response

        return redirect("/")

    # GET – render
    display_records, filter_errors = _compute_display(_records)
    flashed = get_flashed_messages(with_categories=True)
    primary_sort_field = _sort_keys[0][0] if _sort_keys else ""
    primary_sort_order = _sort_keys[0][1] if _sort_keys else "asc"
    resolved_column_widths = _resolve_column_widths(
        display_records,
        _all_fields,
        _truncate_limits,
        _column_widths,
    )
    column_statistics = {
        field: _compute_column_statistics(display_records, field, _truncate_limits.get(field))
        for field in _included_fields
    }

    return render_template(
        "index.html",
        records=display_records,
        total=len(_records),
        all_fields=_all_fields,
        included_fields=_included_fields,
        display_mode=_display_mode,
        sort_keys=_sort_keys,
        primary_sort_field=primary_sort_field,
        primary_sort_order=primary_sort_order,
        filters=_filters,
        filter_errors=filter_errors,
        truncate_limits=_truncate_limits,
        column_widths=_column_widths,
        resolved_column_widths=resolved_column_widths,
        column_statistics=column_statistics,
        default_truncate_limit=_default_truncate_limit,
        default_column_width=_default_column_width,
        display_mode_options=_display_mode_options,
        display_value=_display_value,
        flashed=flashed,
    )


if __name__ == "__main__":
    import os
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(debug=debug, port=5000)
