from typing import Annotated

import pytest
from fastapi import File, Form, UploadFile
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import main
from app.auth.http import WEB_PREFIXES, WebAuthorizedRoute
from app.auth.schemas import WebIdentity
from app.auth.tokens import InvalidAccessToken
from app.db.models import (
    Base,
    BusinessModel,
    ProductAnalysisPreferenceModel,
    ProductModel,
    UserModel,
    WebUserAccessModel,
)
from app.db.repositories.sql_procurement import SqlProcurementRepository
from app.modules.procurement.schemas import Supplier, SupplierOfferDocument
from app.settings import settings

SUBJECT = "00000000-0000-4000-8000-000000000001"
SECRET = "test-origin-secret-with-at-least-32-characters"
HEADERS = {"X-Stock-AI-Origin": SECRET, "Authorization": "Bearer valid"}


@pytest.fixture
def secure_api(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        session.add_all([BusinessModel(id="one", name="Uno"), BusinessModel(id="two", name="Dos")])
        session.flush()
        session.add_all(
            [
                UserModel(
                    id="operator-one", business_id="one", display_name="Operador", role="operator"
                ),
                UserModel(
                    id="operator-two", business_id="two", display_name="Otro", role="operator"
                ),
            ]
        )
        session.flush()
        session.add(
            WebUserAccessModel(auth_subject=SUBJECT, business_id="one", user_id="operator-one")
        )
        session.commit()
    monkeypatch.setattr(main, "SessionLocal", factory)
    for name, value in {
        "app_env": "production",
        "web_auth_enabled": True,
        "supabase_url": "https://example.supabase.co",
        "aws_origin_secret": SECRET,
        "web_cors_origins": [],
        "scheduler_enabled": False,
        "llm_enabled": False,
        "openai_api_key": None,
        "gemini_api_key": None,
        "kapso_api_key": None,
        "internal_api_token": None,
        "kapso_webhook_secret": None,
    }.items():
        monkeypatch.setattr(settings, name, value)
    app = main.create_app()

    def verify(token):
        if token != "valid":
            raise InvalidAccessToken
        return WebIdentity(SUBJECT)

    monkeypatch.setattr(app.state.web_token_verifier, "verify", verify)

    @app.post("/procurement/upload-probe")
    async def upload(file: Annotated[UploadFile, File()], business_id: Annotated[str, Form()]):
        return {
            "business_id": business_id,
            "filename": file.filename,
            "contents": (await file.read()).decode(),
        }

    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def test_route_inventory_is_protected_without_token(secure_api):
    client, _ = secure_api
    checked = 0
    for route in client.app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith(WEB_PREFIXES):
            continue
        assert isinstance(route, WebAuthorizedRoute)
        path = route.path
        for param in route.param_convertors:
            path = path.replace("{" + param + "}", "unknown")
        for method in route.methods:
            response = client.request(method, path, headers={"X-Stock-AI-Origin": SECRET})
            assert response.status_code == 401, (method, path, response.text)
            assert response.headers["cache-control"] == "no-store"
            checked += 1
    assert checked >= 30


def test_origin_is_required_even_with_valid_bearer(secure_api):
    client, _ = secure_api
    assert client.get("/auth/me", headers={"Authorization": "Bearer valid"}).status_code == 403
    assert (
        client.get("/auth/me", headers={**HEADERS, "X-Stock-AI-Origin": "wrong"}).status_code == 403
    )


def test_me_contains_server_controlled_access_only(secure_api):
    client, _ = secure_api
    response = client.get("/auth/me", headers=HEADERS)
    assert response.status_code == 200
    assert response.json() == {
        "businesses": [
            {
                "business_id": "one",
                "business_name": "Uno",
                "user_id": "operator-one",
                "display_name": "Operador",
                "role": "operator",
            }
        ]
    }
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "url",
    [
        "/sales/status?business_id=two",
        "/sales/status?business_id=one&business_id=two",
        "/sales/status",
        "/procurement/supplier-offers/foreign/analysis?business_id=two",
    ],
)
def test_rejects_unassigned_missing_or_duplicate_business(secure_api, url):
    client, _ = secure_api
    assert client.get(url, headers=HEADERS).status_code in {403, 422}


def test_revoked_access_is_checked_each_request(secure_api):
    client, factory = secure_api
    assert client.get("/auth/me", headers=HEADERS).status_code == 200
    with factory() as session:
        session.get(WebUserAccessModel, (SUBJECT, "one")).is_active = False
        session.commit()
    assert client.get("/auth/me", headers=HEADERS).status_code == 403


def test_product_preference_uses_authenticated_actor_and_rejects_foreign_products(secure_api):
    client, factory = secure_api
    with factory() as session:
        session.add_all([
            ProductModel(id="own-product", business_id="one", name="Bolsa",
                         normalized_name="bolsa"),
            ProductModel(id="foreign-product", business_id="two", name="Privado",
                         normalized_name="privado"),
        ])
        session.commit()
    path = "/procurement/products/own-product/analysis-preference"
    body = {"business_id": "one", "exclude_from_sales_rankings": True}
    response = client.post(path, headers=HEADERS, json=body)
    assert response.status_code == 200
    with factory() as session:
        saved = session.get(ProductAnalysisPreferenceModel, ("one", "own-product"))
        assert saved.updated_by_user_id == "operator-one"
        assert saved.exclude_from_sales_rankings is True
    assert client.post(path, headers=HEADERS, json={
        **body, "updated_by_user_id": "operator-two",
    }).status_code == 422
    assert client.post(path, headers=HEADERS, json={
        **body, "business_id": "two",
    }).status_code == 403
    foreign = client.post(
        "/procurement/products/foreign-product/analysis-preference", headers=HEADERS, json=body,
    )
    assert foreign.status_code == 404
    assert "Privado" not in foreign.text
    assert client.get(
        "/procurement/products/analysis-preferences?business_id=one", headers=HEADERS,
    ).json()["excluded_product_ids"] == ["own-product"]


def test_multipart_preserves_file_and_checks_form_tenant(secure_api):
    client, _ = secure_api
    response = client.post(
        "/procurement/upload-probe",
        headers=HEADERS,
        data={"business_id": "one"},
        files={"file": ("offers.pdf", b"fixture")},
    )
    assert response.status_code == 200
    assert response.json() == {
        "business_id": "one",
        "filename": "offers.pdf",
        "contents": "fixture",
    }
    for data in [{"business_id": "two"}, {}]:
        response = client.post(
            "/procurement/upload-probe?business_id=one",
            headers=HEADERS,
            data=data,
            files={"file": ("offers.pdf", b"fixture")},
        )
        assert response.status_code in {403, 422}


def test_chat_uses_authenticated_identity(secure_api, monkeypatch):
    client, _ = secure_api
    received = []

    def handle(self, request):
        from app.schemas.assistant import AssistantResponse

        received.append(request)
        return AssistantResponse(reply="Listo")

    monkeypatch.setattr(main.AssistantEngine, "handle_message", handle)
    payload = {
        "channel": "whatsapp",
        "external_user_id": "forged",
        "business_id": "one",
        "message_type": "text",
        "text": "Hola",
    }
    response = client.post("/assistant/message", headers=HEADERS, json=payload)
    assert response.status_code == 200
    assert received[0].external_user_id == "operator-one"
    assert received[0].channel == "web"
    assert (
        client.post(
            "/assistant/message", headers=HEADERS, json={**payload, "business_id": "two"}
        ).status_code
        == 403
    )
    assert len(received) == 1


@pytest.mark.parametrize("action", ["accept", "reject", "correct"])
def test_review_replaces_forged_reviewer(secure_api, monkeypatch, action):
    client, _ = secure_api
    received = []

    def review(self, *, product_match_candidate_id, request):
        received.append(request)
        raise ValueError("Fixture candidate not found")

    monkeypatch.setattr(main.ProductMatchReviewService, f"{action}_candidate", review)
    payload = {"business_id": "one", "reviewed_by_user_id": "forged"}
    if action == "correct":
        payload["product_id"] = "fixture"
    response = client.post(
        f"/procurement/product-matches/fixture/{action}", headers=HEADERS, json=payload
    )
    assert response.status_code == 404
    assert received[0].reviewed_by_user_id == "operator-one"


def test_service_routes_fail_closed_but_do_not_require_web_token(secure_api, monkeypatch):
    client, _ = secure_api
    assert client.post("/internal/reminders/dispatch-due").status_code == 503
    monkeypatch.setattr(settings, "internal_api_token", "service-token")
    assert client.post("/internal/reminders/dispatch-due").status_code == 401
    assert (
        client.post(
            "/internal/reminders/dispatch-due", headers={"X-Internal-Token": "service-token"}
        ).status_code
        == 200
    )
    assert client.post("/webhooks/kapso", json={}).status_code == 503
    monkeypatch.setattr(settings, "kapso_webhook_secret", "webhook-secret")
    assert client.post("/webhooks/kapso", json={}).status_code == 401


def test_health_public_docs_disabled(secure_api):
    client, _ = secure_api
    assert client.get("/health").json() == {"status": "ok"}
    for path in ["/docs", "/redoc", "/openapi.json"]:
        assert client.get(path).status_code == 404


def test_production_cannot_silently_disable_auth(secure_api, monkeypatch):
    monkeypatch.setattr(settings, "web_auth_enabled", False)
    with pytest.raises(RuntimeError, match="WEB_AUTH_ENABLED"):
        main.create_app()


@pytest.mark.parametrize("authorization", ["Bearer invalid", "Basic valid", "Bearer", "Bearer "])
def test_invalid_authorization_is_401(secure_api, authorization):
    client, _ = secure_api
    response = client.get("/auth/me", headers={**HEADERS, "Authorization": authorization})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_another_business_document_id_does_not_grant_access(secure_api):
    client, factory = secure_api
    repository = SqlProcurementRepository(factory)
    supplier = repository.add_supplier(
        Supplier(
            business_id="two",
            name="Otro proveedor",
            normalized_name="otro proveedor",
        )
    )
    document = repository.add_supplier_offer_document(
        SupplierOfferDocument(
            business_id="two",
            supplier_id=supplier.id,
            source_filename="private.pdf",
        )
    )
    response = client.get(
        f"/procurement/supplier-offers/{document.id}/analysis?business_id=one",
        headers=HEADERS,
    )
    assert response.status_code == 404
    assert "private.pdf" not in response.text


@pytest.mark.parametrize(
    "path,body",
    [
        ("/procurement/catalog-imports", {"business_id": "one", "csv_path": "/etc/passwd"}),
        (
            "/procurement/supplier-offers/from-json",
            {"business_id": "one", "json_path": "/etc/passwd"},
        ),
    ],
)
def test_web_users_cannot_select_files_from_server(secure_api, path, body):
    client, _ = secure_api
    assert client.post(path, headers=HEADERS, json=body).status_code == 403


def test_local_cors_preflight_precedes_auth_but_does_not_allow_origin_secret(
    secure_api, monkeypatch
):
    _, _factory = secure_api
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "web_cors_origins", ["http://localhost:8766"])
    with TestClient(main.create_app()) as client:
        headers = {
            "Origin": "http://localhost:8766",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization,Content-Type,Idempotency-Key",
        }
        response = client.options("/assistant/message", headers=headers)
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:8766"
        assert "access-control-allow-credentials" not in response.headers
        response = client.options(
            "/assistant/message",
            headers={
                **headers,
                "Access-Control-Request-Headers": "X-Stock-AI-Origin",
            },
        )
        assert response.status_code == 400
        response = client.options(
            "/assistant/message",
            headers={
                **headers,
                "Origin": "https://untrusted.vercel.app",
            },
        )
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers
