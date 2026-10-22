import base64
import hashlib
import re
from datetime import timedelta

import pytest

from conftest import API_KEY, CLIENT_SECRET, NOW, REFRESH_TOKEN, keys
from ghti.crypto import DecryptError, Keys
from ghti.errors import AuthRequired
from ghti.oauth import StoredToken, TokenStore


def test_encrypt_decrypt_roundtrip():
    k = keys()
    sealed = k.encrypt(b"secret data")
    assert sealed.startswith("ghti-token-v1:")
    assert b"secret" not in sealed.encode()
    assert k.decrypt(sealed) == b"secret data"


def test_each_encryption_uses_a_new_nonce():
    assert keys().encrypt(b"x") != keys().encrypt(b"x")


def test_other_secrets_cannot_decrypt():
    sealed = keys().encrypt(b"x")
    with pytest.raises(DecryptError):
        Keys(CLIENT_SECRET, "another-intervals-key").decrypt(sealed)
    with pytest.raises(DecryptError):
        Keys("another-client-secret", API_KEY).decrypt(sealed)


def test_tampered_file_is_rejected():
    sealed = keys().encrypt(b"x").strip()
    tampered = sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1]
    with pytest.raises(DecryptError):
        keys().decrypt(tampered)
    with pytest.raises(DecryptError):
        keys().decrypt("not a token file")


def test_token_store_roundtrip_and_no_rewrite_when_unchanged(tmp_path):
    store = TokenStore(tmp_path / "state" / "token.enc", keys())
    token = StoredToken(REFRESH_TOKEN, NOW, NOW + timedelta(days=7), ["scope-a"])
    assert store.save(token) is True
    before = store.path.read_bytes()
    assert REFRESH_TOKEN.encode() not in before
    assert store.load() == token
    assert store.save(token) is False
    assert store.path.read_bytes() == before


def test_token_store_with_changed_secrets_asks_to_authorize(tmp_path):
    path = tmp_path / "token.enc"
    TokenStore(path, keys()).save(StoredToken(REFRESH_TOKEN, NOW, NOW, []))
    with pytest.raises(AuthRequired, match="changed"):
        TokenStore(path, Keys("new-secret", API_KEY)).load()


def test_pkce_is_reproducible_and_valid():
    k = keys()
    state = k.make_state(1_700_000_000)
    verifier = k.pkce_verifier(state)
    assert verifier == keys().pkce_verifier(state)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", verifier)
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert k.pkce_challenge(state) == expected
    assert k.pkce_verifier(k.make_state(1_700_000_001)) != verifier


def test_state_signature():
    k = keys()
    state = k.make_state(1_700_000_000)
    assert k.check_state(state)
    assert not k.check_state(state.replace("1700000000", "1700000001"))
    assert not k.check_state("garbage")
    assert not Keys("other", API_KEY).check_state(state)
