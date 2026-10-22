from datetime import timedelta
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest

from conftest import CLIENT_ID, CLIENT_SECRET, NOW, REFRESH_TOKEN, keys, mock_refresh
from ghti.errors import AuthError, AuthRequired
from ghti.oauth import (
    REDIRECT_URI,
    SCOPES,
    TOKEN_ENDPOINT,
    StoredToken,
    build_auth_url,
    exchange_code,
    parse_redirect_url,
    refresh_access_token,
)


def link_params(link: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlsplit(link).query).items()}


def test_auth_url_has_pkce_loopback_and_scopes(settings):
    params = link_params(build_auth_url(settings, keys()))
    assert params["client_id"] == CLIENT_ID
    assert params["redirect_uri"] == REDIRECT_URI
    assert params["scope"].split() == list(SCOPES)
    assert params["access_type"] == "offline"
    assert params["prompt"] == "consent"
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"] == keys().pkce_challenge(params["state"])
    assert CLIENT_SECRET not in build_auth_url(settings, keys())


def test_auth_url_can_hide_client_id_from_github_masking(settings):
    link = build_auth_url(settings, keys(), encode_client_id=True)
    assert CLIENT_ID not in link
    assert link_params(link)["client_id"] == CLIENT_ID
    assert unquote(link.split("client_id=")[1].split("&")[0]) == CLIENT_ID


def test_parse_redirect_url():
    assert parse_redirect_url(' "http://127.0.0.1:8765/?state=1.abc&code=4/0Ab-c&scope=x" ') == ("4/0Ab-c", "1.abc")
    with pytest.raises(AuthError, match="not granted"):
        parse_redirect_url("http://127.0.0.1:8765/?error=access_denied&state=1.abc")
    with pytest.raises(AuthError, match="whole address"):
        parse_redirect_url("4/0Ab-c")
    with pytest.raises(AuthError, match="no 'code'"):
        parse_redirect_url("http://127.0.0.1:8765/?state=1.abc")
    with pytest.raises(AuthError, match="127.0.0.1"):
        parse_redirect_url("https://evil.example/?state=1.abc&code=x")


def test_exchange_code_sends_verifier_and_reads_lifetime(settings, mock_http):
    k = keys()
    state = k.make_state(NOW.timestamp())
    route = mock_http.post(TOKEN_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "ya29.x",
                "refresh_token": REFRESH_TOKEN,
                "refresh_token_expires_in": 604799,
                "scope": " ".join(SCOPES[:2]),
            },
        )
    )
    with httpx.Client() as http:
        token, missing = exchange_code(settings, k, f"{REDIRECT_URI}?state={state}&code=4/abc", http)
    form = parse_qs(route.calls[0].request.content.decode())
    assert form["code_verifier"] == [k.pkce_verifier(state)]
    assert form["redirect_uri"] == [REDIRECT_URI]
    assert form["grant_type"] == ["authorization_code"]
    assert token.refresh_token == REFRESH_TOKEN
    assert token.expires_at == NOW + timedelta(seconds=604799)
    assert missing == [SCOPES[2]]


def test_exchange_code_rejects_foreign_state(settings):
    with pytest.raises(AuthError, match="different secrets"), httpx.Client() as http:
        exchange_code(settings, keys(), f"{REDIRECT_URI}?state=1.forged&code=4/abc", http)


def test_exchange_code_explains_used_code(settings, mock_http):
    mock_http.post(TOKEN_ENDPOINT).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    state = keys().make_state(NOW.timestamp())
    with pytest.raises(AuthError, match="already used"), httpx.Client() as http:
        exchange_code(settings, keys(), f"{REDIRECT_URI}?state={state}&code=4/abc", http)


def test_refresh_expired_token_requires_authorization(settings, mock_http):
    mock_refresh(mock_http, 400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
    token = StoredToken(REFRESH_TOKEN, NOW - timedelta(days=8), NOW - timedelta(days=1), [])
    with pytest.raises(AuthRequired, match="expired"), httpx.Client() as http:
        refresh_access_token(settings, token, http)


def test_refresh_ok(settings, mock_http):
    mock_refresh(mock_http)
    token = StoredToken(REFRESH_TOKEN, NOW, NOW + timedelta(days=7), [])
    with httpx.Client() as http:
        assert refresh_access_token(settings, token, http) == ("ya29.test-access", None)
