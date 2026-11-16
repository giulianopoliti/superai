"""Existing local UI + original Cloud catalog fields + fresh matching. Never deploy this."""

import argparse
import json
import sys
from pathlib import Path
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import Request  # noqa: E402
from fastapi.concurrency import run_in_threadpool  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse, Response  # noqa: E402
from sqlalchemy import create_engine, make_url, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.repositories.sql_procurement import SqlProcurementRepository  # noqa: E402
from app.modules.procurement.review import ProductMatchReviewService  # noqa: E402
from app.settings import settings  # noqa: E402

NOTICE = (
    "Prueba local con tus datos reales. "
    "Las listas guardadas se recalculan con el catálogo completo. "
    "No se guardan cambios ni se analizan archivos nuevos."
)
LOCAL_SCRIPT = r"""
function lockLocalWrites() {
  document.querySelectorAll('button[type="submit"],button[data-action="accept"],button[data-action="reject"],button[data-action="select-product"]')
    .forEach(b => {
      b.disabled = true; b.title = 'El guardado está deshabilitado en esta prueba local.';
    });
  document.getElementById('businessId').readOnly = true;
}
lockLocalWrites();
new MutationObserver(lockLocalWrites).observe(document.body, {childList:true, subtree:true});
document.addEventListener('submit', e => {
  e.preventDefault(); e.stopImmediatePropagation();
}, true);
fetch('/local/catalog-status').then(r => r.json()).then(s => {
  document.getElementById('localCatalogCoverage').textContent =
    `${s.total} productos disponibles con sus nombres y datos originales.`;
}).catch(() => {
  document.getElementById('localCatalogCoverage').textContent =
    'No se pudo consultar la cobertura del catálogo.';
});
"""


class LocalCatalogReviewService(ProductMatchReviewService):
    def __init__(self, repository):
        super().__init__(repository)
        self._cache = {}
        self._lock = Lock()

    def list_candidates(self, *, business_id, supplier_offer_document_id):
        key = (business_id, supplier_offer_document_id)
        with self._lock:
            cached = self._cache.get(key)
            if cached and monotonic() - cached[0] < 600:
                return cached[1].model_copy(deep=True)
            result = self.preview_candidates(
                business_id=business_id, supplier_offer_document_id=supplier_offer_document_id,
            )
            if len(self._cache) >= 8:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = (monotonic(), result.model_copy(deep=True))
            return result


def configure():
    settings.app_env = "development"
    settings.web_auth_enabled = False
    settings.jobs_enabled = False
    settings.scheduler_enabled = False
    settings.llm_enabled = False
    settings.web_cors_origins = []
    for field in ("openai_api_key", "gemini_api_key", "typesafe_api_key", "kapso_api_key"):
        setattr(settings, field, None)


def cloud_read_only_factory():
    if (not settings.database_url
            or make_url(settings.database_url).get_backend_name() != "postgresql"):
        raise RuntimeError("Use the existing PostgreSQL DATABASE_URL")
    engine = create_engine(settings.database_url, connect_args={"connect_timeout": 10},
                           execution_options={"postgresql_readonly": True})
    with engine.connect() as connection:
        if connection.scalar(text("SHOW transaction_read_only")) != "on":
            raise RuntimeError("PostgreSQL read-only mode was not enforced")
    return sessionmaker(bind=engine, expire_on_commit=False)


def compose(factory=None, business_id=None):
    configure()
    factory = factory or cloud_read_only_factory()
    business_id = business_id or settings.default_business_id
    from app import main as backend

    repository = SqlProcurementRepository(factory, use_auxiliary_catalog=False)
    review = LocalCatalogReviewService(repository)
    backend.SessionLocal = factory
    backend.ProductMatchReviewService = lambda _: review
    api = backend.create_app()
    api.state.local_catalog_review = review

    def status():
        products = repository.list_products(business_id=business_id)
        interpreted = sum(p.auxiliary is not None for p in products)
        return {"total": len(products), "interpreted": interpreted,
                "original_only": len(products) - interpreted}

    @api.middleware("http")
    async def local_read_only(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost"}:
            return JSONResponse({"detail": "Localhost only"}, status_code=403)
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
                    or parsed.port != request.url.port):
                return JSONResponse({"detail": "Local origin required"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            return JSONResponse({"detail": "Prueba local: guardado y extracciones deshabilitados."},
                                status_code=405)
        if any(value != business_id for value in request.query_params.getlist("business_id")):
            return JSONResponse({"detail": "Negocio fuera de esta prueba local."}, status_code=403)
        path = request.url.path
        if path == "/ui/config.js":
            config = {"authEnabled": False, "jobsEnabled": False, "apiBaseUrl": ""}
            return Response("window.STOCK_AI_CONFIG=" + json.dumps(config) + ";",
                            media_type="text/javascript", headers={"Cache-Control": "no-store"})
        if path == "/ui/auth.js":
            return Response("/* Local read-only composition. */", media_type="text/javascript")
        if path == "/local/catalog-preview.js":
            return Response(LOCAL_SCRIPT, media_type="text/javascript")
        if path == "/local/catalog-status":
            return JSONResponse(await run_in_threadpool(status),
                                headers={"Cache-Control": "no-store"})
        if path in {"/", "/ui", "/ui/", "/ui/index.html"}:
            html = (Path(backend.__file__).parent / "static/index.html").read_text(encoding="utf-8")
            html = html.replace('<input id="businessId" value="demo-business"',
                                '<input id="businessId" value=' + json.dumps(business_id))
            html = html.replace("<body>", '<body><aside class="pageWrap" role="status">'
                                + NOTICE + '<p id="localCatalogCoverage">Consultando catálogo…</p>'
                                '</aside>')
            html = html.replace(
                "</body>", '<script src="/local/catalog-preview.js"></script></body>',
            )
            return HTMLResponse(html, headers={"Cache-Control": "no-store"})
        return await call_next(request)

    return api


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(compose(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
