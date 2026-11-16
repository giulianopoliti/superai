"""Isolated preview composition: real JWT validation, SQLite fixtures, no paid providers.

Never import this module from the production app. It refuses non-SQLite databases,
local dotenv files and any configuration without the explicit fixture opt-in.
"""

import argparse
import os
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4


def configure():
    if os.environ.get("STOCK_AI_PREVIEW_FIXTURES") != "1":
        raise RuntimeError("Explicit isolated preview opt-in required")
    root = Path(os.environ["PREVIEW_DATA_DIR"]).resolve()
    if not root.is_absolute() or root == Path(root.anchor):
        raise RuntimeError("Dedicated preview directory required")
    if any(Path(name).exists() for name in (".env", ".env.local")):
        raise RuntimeError("Preview must not read a production dotenv file")
    expected = "sqlite+pysqlite:///" + (root / "fixtures.db").as_posix()
    if os.environ.get("DATABASE_URL") != expected:
        raise RuntimeError("Preview database must be the dedicated SQLite fixture file")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "JEV_API_KEY", "TYPESAFE_AI_API_KEY",
                 "KAPSO_API_KEY", "GOOGLE_VISION_API_KEY", "GOOGLE_CLOUD_VISION_API_KEY"):
        os.environ[name] = ""
    os.environ.update(APP_ENV="preview", WEB_AUTH_ENABLED="true", JOBS_ENABLED="true",
                      WEB_CORS_ORIGINS="[]", SCHEDULER_ENABLED="false", LLM_ENABLED="false",
                      DOCUMENT_ANALYSIS_V2_ENABLED="false", JOB_FILES_DIR=str(root / "jobs"))
    return root


def compose():
    configure()
    from app import main as backend
    from app.modules.procurement.analysis_contracts import (
        AnalysisMethodCapability,
        AnalysisMethodsResponse,
        AnalysisSummary,
    )
    from app.modules.procurement.analysis_pipeline_schemas import PreparedAnalysis
    from app.modules.procurement.schemas import ExtractedSupplierOfferItem, SupplierOfferExtraction

    def capabilities():
        return AnalysisMethodsResponse(methods=[
            AnalysisMethodCapability(id="openai_structured", available=True),
            AnalysisMethodCapability(
                id="openai_blocks_jev", available=False,
                unavailable_reason="Solo el método A simulado está habilitado en esta prueba.",
            ),
        ])

    original_factory = backend.build_supplier_offer_analysis_pipeline

    def fixture_pipeline():
        pipeline = original_factory()

        def prepare(upload, parameters):
            delay = float(os.environ.get("PREVIEW_ANALYSIS_DELAY_SECONDS", "2"))
            if not 0 <= delay <= 180:
                raise ValueError("Invalid fixture delay")
            time.sleep(delay)
            run_id = str(uuid4())
            summary = AnalysisSummary(
                run_id=run_id, method=parameters.analysis_method, status="completed", page_count=1,
                observed_block_count=1, interpreted_offer_count=1, pending_block_count=0,
                provider_call_count=0, duration_seconds=delay,
            )
            return PreparedAnalysis(
                business_id=parameters.business_id,
                supplier_name=parameters.supplier_name or "Proveedor de prueba",
                request_id=run_id, elapsed_seconds=delay,
                extraction=SupplierOfferExtraction(
                    source_filename=upload.filename,
                    warnings=["ENTORNO DE PRUEBA: resultado simulado, sin leer el documento."],
                    document_metadata={"analysis": summary.model_dump(mode="json"),
                                       "preview_fixture": True},
                    items=[ExtractedSupplierOfferItem(
                        raw_name="Azúcar de prueba 1kg", offer_price="1000", currency="ARS",
                        price_type="unit", tax_included=True,
                    )],
                ),
            )

        pipeline.prepare = prepare
        pipeline.supports_page_checkpoints = False
        return pipeline

    backend.analysis_methods_capability = capabilities
    backend.build_supplier_offer_analysis_pipeline = fixture_pipeline
    return backend, backend.create_app()


def seed():
    configure()
    from app.db.models import Base, BusinessModel, ProductModel, UserModel, WebUserAccessModel
    from app.db.session import SessionLocal

    subject = str(UUID(os.environ["PREVIEW_AUTH_SUBJECT"]))
    Base.metadata.create_all(SessionLocal.kw["bind"])
    with SessionLocal() as session:
        if session.get(BusinessModel, "demo-business") is None:
            session.add(BusinessModel(
                id="demo-business", name="Comercio de prueba — datos ficticios"
            ))
            session.flush()
        if session.get(UserModel, "preview-operator") is None:
            session.add(UserModel(id="preview-operator", business_id="demo-business",
                                  display_name="Super Salvador — prueba", role="operator"))
            session.flush()
        if session.get(WebUserAccessModel, (subject, "demo-business")) is None:
            session.add(WebUserAccessModel(auth_subject=subject, business_id="demo-business",
                                           user_id="preview-operator", is_active=True))
        if session.get(ProductModel, "preview-sugar") is None:
            session.add(ProductModel(
                id="preview-sugar", business_id="demo-business", name="Azúcar de prueba 1kg",
                normalized_name="azucar de prueba 1kg", external_product_id="PREVIEW-001",
                sku="PREVIEW-001", current_cost="1200", sale_price="1500", stock_quantity="10",
                unit_size="1", unit="kg",
            ))
        session.commit()
    print("Isolated preview fixtures ready; no production database accessed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["seed", "api", "worker"])
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.mode == "seed":
        return seed()
    _, app = compose()
    if args.mode == "worker":
        from app.jobs.worker import main as worker_main

        sys.argv = ["preview-worker"]
        return worker_main()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
