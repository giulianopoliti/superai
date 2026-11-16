"""Prepare connector-compatible SQL using the same backend validation as the CLI.

Only inserts pending suggestions and system events. The live database must still
match the exported products, families and interpretation evidence when applying.
"""
# ruff: noqa: E402

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, create_engine, event, select
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import (
    Base,
    BusinessModel,
    CatalogInterpretationModel,
    ProductFamilyModel,
    ProductModel,
)
from app.db.models import (
    CatalogFamilyChangeSuggestionModel as Change,
)
from app.db.models import (
    CatalogFamilySuggestionEventModel as Event,
)
from app.modules.procurement.catalog_family_backfill import materialize_report


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def restore(model, data):
    values = {}
    for column in model.__table__.columns:
        if column.name not in data:
            continue
        value = data[column.name]
        if value is not None and column.type.python_type is datetime:
            value = datetime.fromisoformat(value)
        values[column.name] = value
    return model(**values)


def prepare(snapshot, report):
    business_id = snapshot["business_id"]
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def connect(connection, _):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "begin", lambda connection: connection.exec_driver_sql("BEGIN"))
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(BusinessModel(id=business_id, name="Exported business"))
        session.flush()
        for model, key in (
            (ProductFamilyModel, "families"),
            (ProductModel, "products"),
            (CatalogInterpretationModel, "interpretations"),
        ):
            if any(row["business_id"] != business_id for row in snapshot[key]):
                raise ValueError("Snapshot mixes businesses")
            session.add_all(restore(model, row) for row in snapshot[key])
            session.flush()
        result = materialize_report(session, business_id, report)
        if result["counts"].get("conflicts_or_stale"):
            raise ValueError(result)
        # All identifiers below come from fixed models, never from exported data.
        b = quote(business_id)
        guards = [
            f"PERFORM id FROM businesses WHERE id={b} FOR UPDATE;",
            "IF NOT FOUND THEN RAISE EXCEPTION 'Business missing'; END IF;",
            f"PERFORM id FROM products WHERE business_id={b} FOR SHARE;",
            f"PERFORM id FROM product_families WHERE business_id={b} FOR SHARE;",
            f"IF (SELECT md5(string_agg(row_to_json(p)::text,E'\\n' order by id)) "
            f"FROM products p WHERE business_id={b}) IS DISTINCT FROM "
            f"{quote(snapshot['fingerprint'])} THEN "
            "RAISE EXCEPTION 'Catalog changed since export'; END IF;",
        ]
        for family in snapshot["families"]:
            fields = ("name", "normalized_name", "external_family_id", "active")
            expected = {k: family[k] for k in fields}
            expression = ",".join(f"{quote(k)},{k}" for k in fields)
            guards.append(
                f"IF NOT EXISTS (SELECT 1 FROM product_families WHERE business_id={b} "
                f"AND id={quote(family['id'])} AND jsonb_build_object({expression})="
                f"{quote(json.dumps(expected))}::jsonb) THEN "
                "RAISE EXCEPTION 'Families changed since export'; END IF;"
            )
        for row in snapshot["interpretations"]:
            notice = row["response"].get("original_family_validation", {})
            guards.append(
                f"PERFORM id FROM catalog_interpretations WHERE business_id={b} "
                f"AND id={quote(row['id'])} AND product_id={quote(row['product_id'])} "
                f"AND source={quote(json.dumps(row['source']))}::jsonb "
                f"AND response->'original_family_validation'={quote(json.dumps(notice))}::jsonb "
                "FOR SHARE; IF NOT FOUND THEN RAISE EXCEPTION "
                "'Interpretation changed since export'; END IF;"
            )
        statements = ["DO $preflight$ BEGIN\n" + "\n".join(guards) + "\nEND $preflight$;"]
        for model in (Change, Event):
            for row in session.scalars(select(model)).all():
                if model is Change and row.status != "pending":
                    raise ValueError("Only pending suggestions can be exported")
                columns, values = [], []
                for column in model.__table__.columns:
                    columns.append('"' + column.name + '"')
                    value = getattr(row, column.name)
                    if value is None:
                        values.append("NULL")
                    elif isinstance(column.type, JSON):
                        values.append(quote(json.dumps(value, ensure_ascii=False)) + "::jsonb")
                    elif isinstance(value, datetime):
                        values.append(quote(value.isoformat()) + "::timestamptz")
                    else:
                        values.append(quote(value))
                statements.append(
                    f'INSERT INTO public."{model.__tablename__}" ({",".join(columns)}) '
                    f"VALUES ({','.join(values)}) ON CONFLICT (id) DO NOTHING;"
                )
        return "\n\n".join(statements), result


def main():
    parser = argparse.ArgumentParser(description="Preparar backfill validado para conector SQL")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    report = json.loads(args.report.read_text(encoding="utf-8"))
    sql, result = prepare(snapshot, report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(sql, encoding="utf-8")
    args.output.with_suffix(".json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result["counts"]))


if __name__ == "__main__":
    main()
