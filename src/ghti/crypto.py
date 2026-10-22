"""Key derivation and encryption for the stored Google refresh token.

The key is derived with HKDF-SHA256 from two secrets the user already has
(GOOGLE_CLIENT_SECRET and INTERVALS_API_KEY), so on GitHub Actions no extra
secret or personal access token is needed. The token file is encrypted with
AES-256-GCM and can safely live in the (private) repository.

The same derivation also produces:
- the PKCE code verifier for each authorization link (derived from the link's
  `state`), so nothing has to be stored between `auth-url` and `auth-finish`;
- a short signature inside `state`, so we can tell if a link was created with
  different secrets.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

SALT = b"google-health-to-intervals/v1"
FILE_PREFIX = "ghti-token-v1:"


class DecryptError(Exception):
    """The token file cannot be decrypted with the current secrets."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def derive_key(client_secret: str, intervals_api_key: str, purpose: bytes) -> bytes:
    """32-byte key bound to both secrets and to a purpose label."""
    ikm = client_secret.encode("utf-8") + b"\x00" + intervals_api_key.encode("utf-8")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=SALT, info=purpose).derive(ikm)


class Keys:
    """All keys used by the tool, derived once from the two secrets."""

    def __init__(self, client_secret: str, intervals_api_key: str):
        self._secret = client_secret
        self._api_key = intervals_api_key
        self.token_key = derive_key(client_secret, intervals_api_key, b"token-file")
        self.state_key = derive_key(client_secret, intervals_api_key, b"oauth-state")

    # --- token file -------------------------------------------------------

    def encrypt(self, plaintext: bytes) -> str:
        nonce = os.urandom(12)
        sealed = AESGCM(self.token_key).encrypt(nonce, plaintext, FILE_PREFIX.encode())
        return FILE_PREFIX + _b64(nonce + sealed) + "\n"

    def decrypt(self, text: str) -> bytes:
        text = text.strip()
        if not text.startswith(FILE_PREFIX):
            raise DecryptError("not a ghti token file")
        try:
            raw = _unb64(text[len(FILE_PREFIX) :])
            return AESGCM(self.token_key).decrypt(raw[:12], raw[12:], FILE_PREFIX.encode())
        except (InvalidTag, ValueError) as exc:
            raise DecryptError("wrong key or damaged file") from exc

    # --- OAuth state and PKCE --------------------------------------------

    def make_state(self, issued_at: float | None = None) -> str:
        issued = str(int(issued_at if issued_at is not None else time.time()))
        return f"{issued}.{self._sign(issued)}"

    def check_state(self, state: str) -> bool:
        issued, _, signature = state.partition(".")
        return bool(issued) and hmac.compare_digest(signature, self._sign(issued))

    def _sign(self, payload: str) -> str:
        digest = hmac.new(self.state_key, payload.encode("utf-8"), hashlib.sha256).digest()
        return _b64(digest[:12])

    def pkce_verifier(self, state: str) -> str:
        """RFC 7636 code verifier (43 characters), reproducible from `state`."""
        key = derive_key(self._secret, self._api_key, b"pkce:" + state.encode("utf-8"))
        return _b64(key)

    def pkce_challenge(self, state: str) -> str:
        return _b64(hashlib.sha256(self.pkce_verifier(state).encode("ascii")).digest())
