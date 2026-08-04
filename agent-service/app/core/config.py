"""Runtime settings loaded from the environment."""

import base64
import os
from dataclasses import dataclass

# DEV-ONLY default: a well-known all-zero key used only when AGENT_CRYPTO_KEY is
# unset during local development and tests. Production must set AGENT_CRYPTO_KEY
# from the cloud KMS; this value must never be deployed.
DEV_CRYPTO_KEY = base64.urlsafe_b64encode(b"0" * 32).decode()


@dataclass(frozen=True)
class Settings:
    app_name: str = "Goudan Agent API"
    version: str = "1.0.0"
    crypto_key_b64: str = DEV_CRYPTO_KEY

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(crypto_key_b64=os.getenv("AGENT_CRYPTO_KEY", DEV_CRYPTO_KEY))
