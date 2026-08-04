"""Envelope cipher port tests."""

import base64

import pytest
from app.core.crypto import LocalEnvelopeCipher
from cryptography.fernet import InvalidToken

TEST_KEY = base64.urlsafe_b64encode(b"0" * 32).decode()


def test_cipher_round_trip_does_not_return_plaintext():
    cipher = LocalEnvelopeCipher.from_base64_key(TEST_KEY)
    token = cipher.encrypt("private message")
    assert token != "private message"
    assert cipher.decrypt(token) == "private message"


def test_cipher_round_trip_empty_string(cipher):
    token = cipher.encrypt("")
    assert token != ""
    assert cipher.decrypt(token) == ""


def test_cipher_rejects_tampered_ciphertext(cipher):
    token = cipher.encrypt("secret")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(InvalidToken):
        cipher.decrypt(tampered)
