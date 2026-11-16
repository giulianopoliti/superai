"""Project only the evidence needed to derive an analysis summary in SQL.

Reader responses, offers, crops, and raw document text stay on the detail path.
The small ledger projection preserves reporting for historical checkpoints.
All SQL identifiers and JSON paths below are fixed application constants.
"""

from sqlalchemy import JSON, literal_column


def summary_metadata_column(dialect: str):
    root = "supplier_offer_documents.metadata"
    fields = {
        "pages": ("page_number", "initial_done"),
        "calls": ("phase", "start_page", "end_page"),
        "decision_audit": ("reason_codes", "call_ids"),
    }
    if dialect == "postgresql":
        ledger = f"({root}->'analysis_ledger')"
        parts = []
        for array, keys in fields.items():
            source = f"({ledger}->'{array}')"
            def value(key, audit=array == "decision_audit"):
                field = f"entry->'{key}'"
                return f"coalesce({field}, '[]'::jsonb)" if audit else field

            pairs = ", ".join(f"'{key}', {value(key)}" for key in keys)
            fallback = "'[]'::jsonb" if array == "decision_audit" else "NULL"
            parts.append(
                f"'{array}', CASE WHEN jsonb_typeof({source})='array' THEN "
                f"(SELECT coalesce(jsonb_agg(CASE WHEN jsonb_typeof(entry)='object' "
                f"THEN jsonb_build_object({pairs}) ELSE 'null'::jsonb END), '[]'::jsonb) "
                f"FROM jsonb_array_elements({source}) AS entry) ELSE {fallback} END"
            )
        sql = (
            f"jsonb_build_object('analysis', {root}->'analysis', 'analysis_ledger', "
            f"jsonb_build_object('run_id', {ledger}->'run_id', {', '.join(parts)}))"
        )
    elif dialect == "sqlite":
        parts = []
        for array, keys in fields.items():
            path = f"$.analysis_ledger.{array}"
            def value(key, audit=array == "decision_audit"):
                field = f"json_extract(entry.value, '$.{key}')"
                return (
                    f"json(CASE WHEN json_type(entry.value, '$.{key}') IS NULL "
                    f"THEN '[]' ELSE {field} END)"
                    if audit else field
                )

            pairs = ", ".join(f"'{key}', {value(key)}" for key in keys)
            fallback = "'[]'" if array == "decision_audit" else "NULL"
            parts.append(
                f"'{array}', json(CASE WHEN json_type({root}, '{path}')='array' THEN "
                f"(SELECT json_group_array(json(CASE WHEN entry.type='object' "
                f"THEN json_object({pairs}) ELSE 'null' END)) "
                f"FROM json_each({root}, '{path}') AS entry) ELSE {fallback} END)"
            )
        sql = (
            f"json_object('analysis', json_extract({root}, '$.analysis'), 'analysis_ledger', "
            f"json_object('run_id', json_extract({root}, '$.analysis_ledger.run_id'), "
            f"{', '.join(parts)}))"
        )
    else:
        raise ValueError("Document summary projection supports PostgreSQL and SQLite.")
    return literal_column(sql, type_=JSON).label("summary_metadata")
