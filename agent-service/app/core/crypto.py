"""Envelope encryption port backed by Fernet for local development."""

from typing import Protocol

from cryptography.fernet import Fernet, MultiFernet


class EnvelopeCipher(Protocol):
    """Port for envelope encryption. Plaintext is never exposed outside encrypt/decrypt."""

    def encrypt(self, plaintext: str) -> str: ...

    def decrypt(self, ciphertext: str) -> str: ...


class LocalEnvelopeCipher:
    """A MultiFernet-backed cipher for local development and tests."""

    def __init__(self, fernet: MultiFernet) -> None:
        self._fernet = fernet

    @classmethod
    def from_base64_key(cls, key_b64: str) -> "LocalEnvelopeCipher":
        return cls(MultiFernet([Fernet(key_b64.encode("ascii"))]))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()
