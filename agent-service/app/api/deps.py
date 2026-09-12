"""Request-context decoding: Supabase JWT verification and profile role loading.

The ``jwt_verifier`` and ``role_loader`` dependencies are injectable so unit tests
run without a live Supabase stack. The defaults validate the token against the
Supabase JWKS endpoint and load the profile role through PostgREST under the
caller's own JWT, so RLS still constrains the read to the user's own row.
"""

import base64
import os
from collections.abc import Callable
from typing import Any, Final, cast
from uuid import UUID

import httpx
import jwt as pyjwt
from cryptography.hazmat.primitives.asymmetric import ec as ec_impl

from app.core.context import RequestContext, UserRole, new_request_id
from app.core.errors import ApiError

Claims = dict[str, Any]
JwtVerifier = Callable[[str], Claims]
RoleLoader = Callable[[UUID], UserRole]

# Algorithms accepted for user JWTs. Local Supabase signs with ES256, hosted with RS256.
_ALLOWED_JWT_ALGS: Final = ("RS256", "ES256")


def _key_from_jwk(jwk: dict[str, Any]) -> Any:
    """Return a PyJWT-compatible key object from a JWKS entry.

    PyJWT accepts RSA JWK dicts directly but requires a ``cryptography`` key for EC.
    """
    kty = jwk.get("kty")
    if kty == "RSA":
        return jwk
    if kty == "EC":
        if jwk.get("crv") != "P-256":
            raise ApiError("AUTH_REQUIRED", "Invalid authentication token.", False)
        x = _jwk_b64u(jwk["x"])
        y = _jwk_b64u(jwk["y"])
        return ec_impl.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec_impl.SECP256R1()
        ).public_key()
    raise ApiError("AUTH_REQUIRED", "Invalid authentication token.", False)


def _jwk_b64u(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def decode_request_context(
    authorization: str | None,
    *,
    jwt_verifier: JwtVerifier | None = None,
    role_loader: RoleLoader | None = None,
) -> RequestContext:
    """Validate the Bearer token and build an immutable per-request context.

    ``user_id`` always comes from the verified JWT ``sub`` claim; it is never
    accepted from a JSON body or query parameter.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError("AUTH_REQUIRED", "Authentication is required.", False)
    token = authorization.removeprefix("Bearer ").strip()
    verify = jwt_verifier or _make_jwks_verifier()
    load_role = role_loader or _make_postgrest_role_loader(token)
    claims = verify(token)
    user_id = _user_id_from_claims(claims)
    role = load_role(user_id)
    return RequestContext(user_id=user_id, role=role, request_id=new_request_id())


def _user_id_from_claims(claims: Claims) -> UUID:
    sub = claims.get("sub")
    try:
        return UUID(str(sub))
    except (ValueError, TypeError):
        raise ApiError("AUTH_REQUIRED", "Invalid authentication token.", False) from None


def _make_jwks_verifier() -> JwtVerifier:
    """Verify a Supabase user JWT against the JWKS endpoint (RS256 or ES256)."""
    jwks_url = os.getenv("SUPABASE_JWKS_URL")
    if not jwks_url:
        raise ApiError("AUTH_REQUIRED", "Authentication is not configured.", False)

    def verify(token: str) -> Claims:
        try:
            response = httpx.get(jwks_url, timeout=5.0)
            response.raise_for_status()
            keys = response.json().get("keys", [])
        except (httpx.HTTPError, ValueError) as exc:
            raise ApiError("AUTH_REQUIRED", "Unable to verify authentication token.", False) from exc
        try:
            header = pyjwt.get_unverified_header(token)
        except pyjwt.PyJWTError:
            raise ApiError("AUTH_REQUIRED", "Invalid authentication token.", False) from None
        algorithm = header.get("alg")
        if algorithm not in _ALLOWED_JWT_ALGS:
            raise ApiError("AUTH_REQUIRED", "Invalid authentication token.", False)
        try:
            key = next(k for k in keys if k.get("kid") == header.get("kid"))
        except StopIteration:
            raise ApiError("AUTH_REQUIRED", "Invalid authentication token.", False) from None
        try:
            return pyjwt.decode(token, _key_from_jwk(key), algorithms=[algorithm], audience="authenticated")
        except (pyjwt.PyJWTError, KeyError, TypeError, ValueError):
            raise ApiError("AUTH_REQUIRED", "Invalid authentication token.", False) from None

    return verify


def _make_postgrest_role_loader(token: str) -> RoleLoader:
    """Load the profile role through PostgREST under the caller's own JWT.

    RLS constrains the query to the profile row owned by the JWT subject, so a
    token for user A can never read user B's profile.
    """
    supabase_url = os.getenv("SUPABASE_URL")
    if not supabase_url:
        raise ApiError("AUTH_REQUIRED", "Authentication is not configured.", False)
    apikey = os.getenv("SUPABASE_ANON_KEY", token)
    headers = {"Authorization": f"Bearer {token}", "apikey": apikey}

    def load_role(user_id: UUID) -> UserRole:
        try:
            response = httpx.get(
                f"{supabase_url}/rest/v1/profiles",
                params={"select": "role,disabled_at", "id": f"eq.{user_id}"},
                headers=headers,
                timeout=5.0,
            )
            response.raise_for_status()
            rows = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ApiError("AUTH_REQUIRED", "Unable to load user profile.", False) from exc
        if not rows:
            raise ApiError("AUTHZ_DENIED", "User profile not found.", False)
        row = rows[0]
        if row.get("disabled_at") is not None:
            raise ApiError("AUTHZ_DENIED", "Account is disabled.", False)
        role = row.get("role")
        if role not in ("user", "admin"):
            raise ApiError("AUTHZ_DENIED", "Invalid user role.", False)
        return cast(UserRole, role)

    return load_role
