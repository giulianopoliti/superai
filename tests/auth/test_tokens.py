import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from app.auth.tokens import (
    IdentityProviderUnavailable,
    InvalidAccessToken,
    SupabaseTokenVerifier,
)


@pytest.fixture
def signed(monkeypatch):
    private = ec.generate_private_key(ec.SECP256R1())
    verifier = SupabaseTokenVerifier("https://example.supabase.co")
    jwk = jwt.PyJWK.from_json(jwt.algorithms.ECAlgorithm.to_jwk(private.public_key()))
    monkeypatch.setattr(verifier.jwks, "get_signing_key_from_jwt", lambda _: jwk)
    now = datetime.now(UTC)
    claims = {
        "iss": verifier.issuer,
        "sub": str(uuid4()),
        "aud": "authenticated",
        "exp": now + timedelta(minutes=5),
        "iat": now,
        "role": "authenticated",
    }

    def sign(changes=None, remove=None, key=None):
        payload = {**claims, **(changes or {})}
        if remove:
            payload.pop(remove)
        return jwt.encode(payload, key or private, algorithm="ES256", headers={"kid": "test"})

    return verifier, claims, sign


def test_real_signature_is_verified(signed):
    verifier, claims, sign = signed
    assert verifier.verify(sign()).subject == claims["sub"]


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://other.supabase.co/auth/v1"},
        {"aud": "anon"},
        {"role": "service_role"},
        {"is_anonymous": True},
        {"sub": "not-a-uuid"},
        {"exp": 1},
        {"iat": 9999999999},
        {"nbf": 9999999999},
    ],
)
def test_rejects_invalid_claims(signed, changes):
    verifier, _, sign = signed
    with pytest.raises(InvalidAccessToken):
        verifier.verify(sign(changes))


@pytest.mark.parametrize("claim", ["iss", "aud", "exp", "iat", "sub", "role"])
def test_required_claims(signed, claim):
    verifier, _, sign = signed
    with pytest.raises(InvalidAccessToken):
        verifier.verify(sign(remove=claim))


def test_rejects_wrong_signature(signed):
    verifier, _, sign = signed
    with pytest.raises(InvalidAccessToken):
        verifier.verify(sign(key=ec.generate_private_key(ec.SECP256R1())))


@pytest.mark.parametrize(
    "token",
    [
        "not.a.jwt",
        jwt.encode({"sub": "x"}, "a" * 32, algorithm="HS256"),
        jwt.encode({"sub": "x"}, "", algorithm="none"),
    ],
)
def test_unaccepted_algorithms_never_discover_keys(signed, monkeypatch, token):
    verifier, _, _ = signed
    monkeypatch.setattr(
        verifier.jwks,
        "get_signing_key_from_jwt",
        lambda _: pytest.fail("Untrusted algorithm caused JWKS request"),
    )
    with pytest.raises(InvalidAccessToken):
        verifier.verify(token)


def test_jwks_connection_failure_is_unavailable(signed, monkeypatch):
    verifier, _, sign = signed

    def unavailable(_):
        raise jwt.PyJWKClientConnectionError("offline")

    monkeypatch.setattr(verifier.jwks, "get_signing_key_from_jwt", unavailable)
    with pytest.raises(IdentityProviderUnavailable):
        verifier.verify(sign())


def test_user_metadata_does_not_change_identity(signed):
    verifier, claims, sign = signed
    identity = verifier.verify(
        sign(
            {
                "user_metadata": {
                    "sub": str(uuid4()),
                    "business_id": "other",
                    "role": "admin",
                }
            }
        )
    )
    assert identity.subject == claims["sub"]


def test_jwks_cache_refreshes_for_a_rotated_key(monkeypatch):
    verifier = SupabaseTokenVerifier("https://example.supabase.co")
    keys = [ec.generate_private_key(ec.SECP256R1()) for _ in range(2)]
    calls = []

    def jwks_response(request, **kwargs):
        index = min(len(calls), 1)
        calls.append(request.full_url)
        public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(keys[index].public_key()))
        return io.BytesIO(
            json.dumps(
                {"keys": [{**public, "kid": f"key-{index}", "alg": "ES256", "use": "sig"}]}
            ).encode()
        )

    monkeypatch.setattr(
        "urllib.request.build_opener", lambda *args: SimpleNamespace(open=jwks_response)
    )
    now = datetime.now(UTC)
    claims = {
        "iss": verifier.issuer,
        "aud": "authenticated",
        "role": "authenticated",
        "sub": str(uuid4()),
        "iat": now,
        "exp": now + timedelta(minutes=5),
    }
    for index in [0, 0, 1]:
        if index == 1:
            # PyJWT 2.15 bounds refreshes for unknown kid values with a cooldown.
            verifier.jwks._last_successful_fetch -= 31
        token = jwt.encode(claims, keys[index], algorithm="ES256", headers={"kid": f"key-{index}"})
        assert verifier.verify(token).subject == claims["sub"]
    assert len(calls) == 2
    assert calls == [verifier.issuer + "/.well-known/jwks.json"] * 2
