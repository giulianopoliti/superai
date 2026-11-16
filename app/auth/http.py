import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.routing import APIRoute
from starlette.datastructures import FormData

from app.auth.schemas import TenantContext
from app.auth.tokens import IdentityProviderUnavailable, InvalidAccessToken

WEB_PREFIXES = ("/operations/", "/sales/", "/procurement/", "/assistant/", "/auth/")
LOCAL_ENVIRONMENTS = {"development", "test"}
SERVER_FILE_IMPORTS = {
    "/procurement/catalog-imports",
    "/procurement/supplier-offers/from-json",
}


def protected_environment(config) -> bool:
    return config.app_env not in LOCAL_ENVIRONMENTS


def validate_auth_configuration(config, session_factory) -> None:
    if protected_environment(config) and not config.web_auth_enabled:
        raise RuntimeError("WEB_AUTH_ENABLED must be true outside development/test.")
    if protected_environment(config) and config.web_cors_origins:
        raise RuntimeError("Direct browser CORS is disabled outside development/test.")
    for origin in config.web_cors_origins:
        local = urlsplit(origin)
        if (
            local.scheme not in {"http", "https"}
            or local.hostname not in {"localhost", "127.0.0.1", "::1"}
            or local.path
            or local.query
            or local.fragment
            or local.username
            or local.password
        ):
            raise RuntimeError("WEB_CORS_ORIGINS accepts exact local origins only.")
    if not config.web_auth_enabled:
        return
    url = urlsplit(config.supabase_url or "")
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise RuntimeError("SUPABASE_URL must be a fixed HTTPS origin.")
    if (
        not config.aws_origin_secret
        or not config.aws_origin_secret.isascii()
        or len(config.aws_origin_secret) < 32
    ):
        raise RuntimeError("AWS_ORIGIN_SECRET must have at least 32 characters.")
    if session_factory is None:
        raise RuntimeError("Web authentication requires a database.")


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="Ingresá nuevamente para continuar.",
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


async def authenticate(request: Request) -> None:
    config = request.app.state.web_auth_config
    secret = request.headers.get("x-stock-ai-origin", "")
    if (
        len(request.headers.getlist("x-stock-ai-origin")) != 1
        or not secret.isascii()
        or not secrets.compare_digest(secret, config.aws_origin_secret)
    ):
        raise HTTPException(403, "Acceso no autorizado.")
    headers = request.headers.getlist("authorization")
    if len(headers) != 1:
        raise unauthorized()
    scheme, separator, token = headers[0].partition(" ")
    if scheme.lower() != "bearer" or not separator or not token or len(token) > 16384:
        raise unauthorized()
    try:
        identity = await run_in_threadpool(request.app.state.web_token_verifier.verify, token)
    except InvalidAccessToken as exc:
        raise unauthorized() from exc
    except IdentityProviderUnavailable as exc:
        raise HTTPException(503, "No se pudo verificar la sesión. Intentá nuevamente.") from exc
    access = await run_in_threadpool(
        request.app.state.web_access_repository.list_access, identity.subject
    )
    if not access:
        raise HTTPException(403, "Tu usuario no tiene comercios habilitados.")
    request.state.web_identity = identity
    request.state.web_access = access


async def authorize_tenant(request: Request) -> None:
    if request.url.path == "/auth/me":
        return
    query_businesses = request.query_params.getlist("business_id")
    if len(query_businesses) > 1:
        raise HTTPException(422, "Indicá un único comercio.")
    businesses = list(query_businesses)
    if request.method in {"POST", "PUT", "PATCH"}:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type == "application/json":
            try:
                body = await request.json()
            except ValueError as exc:
                raise HTTPException(422, "El cuerpo debe ser JSON válido.") from exc
            business = body.get("business_id") if isinstance(body, dict) else None
        elif content_type in {"multipart/form-data", "application/x-www-form-urlencoded"}:
            body: FormData = await request.form()
            fields = body.getlist("business_id")
            if len(fields) > 1:
                raise HTTPException(422, "Indicá un único comercio.")
            business = fields[0] if fields else None
        else:
            business = None
        # Query parameters must never authorize a different default in a POST body.
        if not isinstance(business, str) or not business.strip():
            raise HTTPException(422, "Falta indicar el comercio en la operación.")
        businesses.append(business)
    if not businesses:
        if request.url.path == "/procurement/supplier-offers/analysis-methods":
            return
        raise HTTPException(422, "Falta indicar el comercio.")
    if len(set(businesses)) != 1:
        raise HTTPException(403, "El comercio de la operación no coincide.")
    allowed = next(
        (row for row in request.state.web_access if row.business_id == businesses[0]), None
    )
    if allowed is None:
        raise HTTPException(403, "No tenés acceso a este comercio.")
    request.state.tenant_context = TenantContext(request.state.web_identity, allowed)


class WebAuthorizedRoute(APIRoute):
    """Protect every business route before FastAPI validation or provider execution."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def authorized(request: Request):
            protected = request.url.path.startswith(WEB_PREFIXES)
            enabled = request.app.state.web_auth_config.web_auth_enabled
            if not protected or not enabled:
                return await original(request)
            try:
                await authenticate(request)
                if request.method == "POST" and request.url.path in SERVER_FILE_IMPORTS:
                    raise HTTPException(403, "Usá la carga de archivos de la aplicación.")
                await authorize_tenant(request)
                response = await original(request)
            finally:
                # A wrapper can parse multipart before FastAPI owns the form context.
                # Close spooled files even when authorization/validation fails.
                await request.close()
            response.headers["Cache-Control"] = "no-store"
            return response

        return authorized
