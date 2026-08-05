"""Request-context decoding tests: Supabase JWT verification and profile role loading.

The JWT verifier and profile role loader are injected so these tests run without a
live Supabase stack. The "valid" tokens are signed locally with PyJWT using the local
Supabase JWT secret (HS256), mirroring how the local stack issues user tokens.
"""

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import jwt as pyjwt
import pytest
from app.api.deps import RoleLoader, decode_request_context
from app.core.context import RequestContext
from app.core.errors import ApiError
from cryptography.hazmat.primitives.asymmetric import ec

# Same HS256 secret the local Supabase stack signs user JWTs with.
JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET", "test-only-jwt-secret-for-local-tests")

USER_A = UUID("11111111-1111-4111-8111-111111111111")
USER_B = UUID("22222222-2222-4222-8222-222222222222")


def _mint_token(sub: str) -> str:
    now = datetime.now(UTC)
    return pyjwt.encode(
        {
            "sub": sub,
            "role": "authenticated",
            "aud": "authenticated",
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _verify_hs256(token: str) -> dict[str, Any]:
    """A JWT verifier backed by the local Supabase JWT secret."""
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"], audience="authenticated")
    except pyjwt.PyJWTError:
        raise ApiError("AUTH_REQUIRED", "Invalid authentication token.", False) from None


def _role_loader(profiles: dict[str, str]) -> RoleLoader:
    """A profile role loader backed by an in-memory {user_id: role} map.

    A "disabled" entry represents a profile with ``disabled_at`` set; a missing
    entry represents a user with no profile row.
    """

    def load(user_id: UUID) -> str:
        role = profiles.get(str(user_id))
        if role == "disabled":
            raise ApiError("AUTHZ_DENIED", "Account is disabled.", False)
        if role is None:
            raise ApiError("AUTHZ_DENIED", "User profile not found.", False)
        return cast(str, role)

    return load


def test_context_rejects_missing_bearer_token():
    with pytest.raises(ApiError, match="AUTH_REQUIRED"):
        decode_request_context(None)


def test_context_rejects_non_bearer_authorization():
    with pytest.raises(ApiError, match="AUTH_REQUIRED"):
        decode_request_context("")
    with pytest.raises(ApiError, match="AUTH_REQUIRED"):
        decode_request_context("some-token")


def test_context_accepts_valid_token_with_profile_role():
    token = f"Bearer {_mint_token(str(USER_A))}"
    context = decode_request_context(token, jwt_verifier=_verify_hs256, role_loader=_role_loader({str(USER_A): "user"}))

    assert isinstance(context, RequestContext)
    assert context.user_id == USER_A
    assert context.role == "user"
    assert context.request_id


def test_context_accepts_admin_role():
    token = f"Bearer {_mint_token(str(USER_A))}"
    context = decode_request_context(
        token, jwt_verifier=_verify_hs256, role_loader=_role_loader({str(USER_A): "admin"})
    )

    assert context.role == "admin"


def test_context_rejects_disabled_profile():
    token = f"Bearer {_mint_token(str(USER_A))}"
    with pytest.raises(ApiError, match="AUTHZ_DENIED"):
        decode_request_context(token, jwt_verifier=_verify_hs256, role_loader=_role_loader({str(USER_A): "disabled"}))


def test_context_rejects_missing_profile():
    token = f"Bearer {_mint_token(str(USER_A))}"
    with pytest.raises(ApiError, match="AUTHZ_DENIED"):
        decode_request_context(token, jwt_verifier=_verify_hs256, role_loader=_role_loader({}))


def test_context_rejects_garbage_token():
    with pytest.raises(ApiError, match="AUTH_REQUIRED"):
        decode_request_context(
            "Bearer garbage", jwt_verifier=_verify_hs256, role_loader=_role_loader({str(USER_A): "user"})
        )


def test_context_rejects_tampered_signature():
    wrong_secret = "some-other-secret-that-forges-tokens"
    forged = pyjwt.encode({"sub": str(USER_A), "aud": "authenticated"}, wrong_secret, algorithm="HS256")
    with pytest.raises(ApiError, match="AUTH_REQUIRED"):
        decode_request_context(
            f"Bearer {forged}", jwt_verifier=_verify_hs256, role_loader=_role_loader({str(USER_A): "user"})
        )


def test_role_loader_is_keyed_by_verified_sub():
    """The loader is called with the token's sub, so user A can never read user B's profile."""
    called_with: list[UUID] = []

    def recording_loader(user_id: UUID) -> str:
        called_with.append(user_id)
        return cast(str, "user")

    token = f"Bearer {_mint_token(str(USER_A))}"
    context = decode_request_context(token, jwt_verifier=_verify_hs256, role_loader=recording_loader)

    assert context.user_id == USER_A
    assert called_with == [USER_A]
    assert USER_B not in called_with


def test_token_for_user_a_cannot_load_profile_b():
    """Only user B has a profile row; user A's token must not satisfy it with B's row."""
    profiles = {str(USER_B): "user"}
    token = f"Bearer {_mint_token(str(USER_A))}"
    with pytest.raises(ApiError, match="AUTHZ_DENIED"):
        decode_request_context(token, jwt_verifier=_verify_hs256, role_loader=_role_loader(profiles))


def test_request_id_is_unique_per_call():
    token = f"Bearer {_mint_token(str(USER_A))}"
    loader = _role_loader({str(USER_A): "user"})
    first = decode_request_context(token, jwt_verifier=_verify_hs256, role_loader=loader)
    second = decode_request_context(token, jwt_verifier=_verify_hs256, role_loader=loader)

    assert first.request_id != second.request_id


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _es256_jwks(private_key: object) -> dict[str, Any]:
    """Build a JWKS payload for an ES256 (P-256) signing key."""
    public_numbers = private_key.public_key().public_numbers()
    x = public_numbers.x.to_bytes(32, "big")
    y = public_numbers.y.to_bytes(32, "big")
    return {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "x": _b64u(x),
                "y": _b64u(y),
                "kid": "test-es256-kid",
                "alg": "ES256",
                "use": "sig",
            }
        ]
    }


def _mint_es256_token(sub: str, private_key: object) -> str:
    now = datetime.now(UTC)
    return pyjwt.encode(
        {
            "sub": sub,
            "role": "authenticated",
            "aud": "authenticated",
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        private_key,
        algorithm="ES256",
        headers={"alg": "ES256", "kid": "test-es256-kid", "typ": "JWT"},
    )


def test_context_verifies_es256_token_against_jwks(monkeypatch):
    """The production JWKS verifier must accept ES256-signed user tokens (local Supabase).

    Local Supabase signs user JWTs with ES256; hosted Supabase uses RS256. The default
    verifier must handle the algorithm named in the token header.
    """
    private_key = ec.generate_private_key(ec.SECP256R1())
    jwks = _es256_jwks(private_key)
    token = f"Bearer {_mint_es256_token(str(USER_A), private_key)}"

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return jwks

    monkeypatch.setenv("SUPABASE_JWKS_URL", "http://supabase.invalid/.well-known/jwks.json")
    monkeypatch.setattr("app.api.deps.httpx.get", lambda *args, **kwargs: FakeResponse())

    context = decode_request_context(token, role_loader=_role_loader({str(USER_A): "user"}))

    assert context.user_id == USER_A
    assert context.role == "user"


def _alg_none_token(sub: str) -> str:
    now = datetime.now(UTC)
    header = _b64u(json.dumps({"alg": "none", "kid": "test-es256-kid", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64u(
        json.dumps(
            {
                "sub": sub,
                "aud": "authenticated",
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(hours=1)).timestamp()),
            },
            separators=(",", ":"),
        ).encode()
    )
    return f"{header}.{payload}."  # alg=none: empty signature


def test_context_rejects_unsupported_jwt_algorithm(monkeypatch):
    private_key = ec.generate_private_key(ec.SECP256R1())
    jwks = _es256_jwks(private_key)
    token = _alg_none_token(str(USER_A))

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return jwks

    monkeypatch.setenv("SUPABASE_JWKS_URL", "http://supabase.invalid/.well-known/jwks.json")
    monkeypatch.setattr("app.api.deps.httpx.get", lambda *args, **kwargs: FakeResponse())

    with pytest.raises(ApiError, match="AUTH_REQUIRED"):
        decode_request_context(f"Bearer {token}", role_loader=_role_loader({str(USER_A): "user"}))
