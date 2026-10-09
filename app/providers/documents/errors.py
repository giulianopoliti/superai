"""Safe, actionable provider diagnostics without request bodies or credentials."""


def provider_error(exc: Exception) -> dict:
    status = getattr(exc, "status_code", None)
    body = getattr(exc, "body", None)
    error = body if isinstance(body, dict) else {}
    if isinstance(error.get("error"), dict):
        error = error["error"]
    code = error.get("code")
    # Restrict API fields to machine identifiers, never echo arbitrary bodies.
    def identifier(value):
        value = str(value or "")[:150]
        return value if all(c.isalnum() or c in "_.-[]" for c in value) else None

    permanent = status in {401, 403, 404} or code in {
        "invalid_json_schema", "model_not_found", "invalid_api_key", "insufficient_quota",
    }
    return {
        "error_type": type(exc).__name__,
        "status_code": status if isinstance(status, int) else None,
        "code": identifier(code),
        "param": identifier(error.get("param")),
        "request_id": identifier(getattr(exc, "request_id", None)),
        "retryable": not permanent,
    }
