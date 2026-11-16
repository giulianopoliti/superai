from uuid import UUID

import jwt
from jwt import PyJWKClient

from app.auth.schemas import WebIdentity


class InvalidAccessToken(Exception):
    pass


class IdentityProviderUnavailable(Exception):
    pass


class SupabaseTokenVerifier:
    """Only asymmetric user JWTs from the configured project are accepted.

    No JWT URL/header controls key discovery. Cache the JWKS for five minutes,
    without the separate permanent key cache, so removed keys eventually expire.
    """

    def __init__(self, supabase_url: str) -> None:
        self.issuer = supabase_url.rstrip("/") + "/auth/v1"
        self.jwks = PyJWKClient(
            self.issuer + "/.well-known/jwks.json",
            lifespan=300,
            cache_keys=False,
            timeout=5,
        )

    def verify(self, token: str) -> WebIdentity:
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") not in {"ES256", "RS256"} or not header.get("kid"):
                raise InvalidAccessToken
            key = self.jwks.get_signing_key_from_jwt(token)
            if key.algorithm_name != header["alg"]:
                raise InvalidAccessToken
            claims = jwt.decode(
                token,
                key.key,
                algorithms=[key.algorithm_name],
                issuer=self.issuer,
                audience="authenticated",
                options={"require": ["iss", "aud", "exp", "iat", "sub", "role"]},
            )
            if claims["role"] != "authenticated" or claims.get("is_anonymous", False):
                raise InvalidAccessToken
            subject = str(UUID(claims["sub"]))
            return WebIdentity(subject=subject)
        except jwt.PyJWKClientConnectionError as exc:
            raise IdentityProviderUnavailable from exc
        except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
            raise InvalidAccessToken from exc
