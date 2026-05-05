from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class TokenCipher:
    def __init__(self, key_value: str | None) -> None:
        if not key_value:
            raise RuntimeError("Не задан HH_TOKEN_ENCRYPTION_KEY. Токены HH нельзя хранить без шифрования.")
        self._key = self._decode_key(key_value)

    def encrypt(self, value: str) -> str:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, value.encode("utf-8"), None)
        return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str) -> str:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
        nonce, ciphertext = raw[:12], raw[12:]
        return AESGCM(self._key).decrypt(nonce, ciphertext, None).decode("utf-8")

    @staticmethod
    def _decode_key(value: str) -> bytes:
        cleaned = value.strip()
        for decoder in (base64.urlsafe_b64decode, base64.b64decode):
            try:
                key = decoder(cleaned + "=" * (-len(cleaned) % 4))
            except Exception:
                continue
            if len(key) in {16, 24, 32}:
                return key
        raw = cleaned.encode("utf-8")
        if len(raw) in {16, 24, 32}:
            return raw
        raise RuntimeError("HH_TOKEN_ENCRYPTION_KEY должен быть AES-ключом 16, 24 или 32 байта в base64 или raw-строке.")
