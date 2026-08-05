"""Signed tool grant: mint and verify HMAC-signed tokens for sandbox tool access.

The worker mints a one-time grant when a sandbox run starts; the sandbox container
presents it to the broker. The token carries run_id, user_id, plan_hash, expiry,
and allowed step ids. The signature is HMAC-SHA256 with a shared secret
(``AGENT_TOOL_GRANT_SECRET``) known only to the agent-service and the sandbox
worker — the container never receives the secret, only the signed token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class GrantPayload:
    """The verified contents of a tool grant token."""

    run_id: str
    user_id: str
    plan_hash: str
    step_ids: list[str]
    exp: int  # Unix timestamp


class GrantSigner:
    """Mints HMAC-signed grant tokens with a shared secret."""

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    def sign(
        self, *, run_id: str, user_id: str, plan_hash: str, step_ids: list[str], ttl_seconds: int = 600
    ) -> str:
        """Return a base64-encoded grant token string."""
        payload = {
            "run_id": run_id,
            "user_id": user_id,
            "plan_hash": plan_hash,
            "step_ids": step_ids,
            "exp": int(time.time()) + ttl_seconds,
        }
        payload_b64 = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        ).decode().rstrip("=")
        sig = hmac.new(
            self._secret, payload_b64.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"{payload_b64}.{sig}"


class GrantVerifier:
    """Verifies HMAC-signed grant tokens with a shared secret."""

    def __init__(self, secret: str) -> None:
        self._secret = secret.encode("utf-8")

    def verify(self, token: str) -> GrantPayload:
        """Decode and verify a grant token; raise GrantInvalid on any failure.

        Checks: well-formed token, valid HMAC signature, expiry, required fields.
        """
        if "." not in token:
            raise GrantInvalid("SANDBOX_GRANT_INVALID", "Malformed grant token.")
        payload_b64, sig = token.rsplit(".", 1)
        expected_sig = hmac.new(
            self._secret, payload_b64.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, sig):
            raise GrantInvalid("SANDBOX_GRANT_INVALID", "Grant signature is invalid.")

        # Pad for base64 decoding
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        try:
            raw = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except Exception:
            raise GrantInvalid("SANDBOX_GRANT_INVALID", "Grant payload is not valid JSON.") from None

        if not isinstance(raw, dict):
            raise GrantInvalid("SANDBOX_GRANT_INVALID", "Grant payload must be an object.")

        for key in ("run_id", "user_id", "plan_hash", "step_ids", "exp"):
            if key not in raw:
                raise GrantInvalid("SANDBOX_GRANT_INVALID", f"Grant is missing {key!r}.")

        if not isinstance(raw["step_ids"], list):
            raise GrantInvalid("SANDBOX_GRANT_INVALID", "Grant step_ids must be a list.")

        exp = int(raw["exp"])
        if time.time() > exp:
            raise GrantInvalid("SANDBOX_GRANT_INVALID", "Grant has expired.")

        return GrantPayload(
            run_id=str(raw["run_id"]),
            user_id=str(raw["user_id"]),
            plan_hash=str(raw["plan_hash"]),
            step_ids=[str(s) for s in raw["step_ids"]],
            exp=exp,
        )


@dataclass(frozen=True)
class GrantInvalid(Exception):
    """Raised when a grant token fails verification."""
    code: str
    message: str
