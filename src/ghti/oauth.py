"""Google OAuth for a "Desktop app" client in Testing mode.

Flow (no server needed, works on GitHub Actions too):
1. `auth-url` prints a consent link with a loopback redirect and PKCE.
2. After approving, the browser tries to open http://127.0.0.1:8765/?state=...&code=...
   and shows a connection error. That is expected: the user copies the address.
3. `auth-finish <address>` exchanges the code for a refresh token and stores it
   encrypted. Google's old copy/paste "OOB" flow is not used (it was shut down).

In Testing mode Google expires the refresh token 7 days after consent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import httpx

from ghti import config
from ghti.config import Settings
from ghti.crypto import DecryptError, Keys
from ghti.errors import AuthError, AuthRequired

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
# Desktop clients accept any loopback port without registering it.
REDIRECT_URI = "http://127.0.0.1:8765/"
SCOPE_PREFIX = "https://www.googleapis.com/auth/googlehealth."
SCOPES = (
    SCOPE_PREFIX + "sleep.readonly",
    SCOPE_PREFIX + "health_metrics_and_measurements.readonly",
    SCOPE_PREFIX + "activity_and_fitness.readonly",
)
# Testing-mode lifetime, used when Google does not send refresh_token_expires_in.
DEFAULT_REFRESH_LIFETIME = timedelta(days=7)


@dataclass
class StoredToken:
    refresh_token: str
    obtained_at: datetime
    expires_at: datetime
    scopes: list[str] = field(default_factory=list)

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "refresh_token": self.refresh_token,
                "obtained_at": self.obtained_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "scopes": self.scopes,
            },
            sort_keys=True,
        ).encode("utf-8")

    @classmethod
    def from_json(cls, raw: bytes) -> StoredToken:
        data = json.loads(raw)
        return cls(
            refresh_token=data["refresh_token"],
            obtained_at=datetime.fromisoformat(data["obtained_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            scopes=list(data.get("scopes", [])),
        )

    def remaining(self, now: datetime | None = None) -> timedelta:
        return self.expires_at - (now or config.utcnow())


class TokenStore:
    """Reads and writes the encrypted token file."""

    def __init__(self, path: Path, keys: Keys):
        self.path = path
        self.keys = keys

    def load(self) -> StoredToken:
        if not self.path.is_file():
            raise AuthRequired(f"No Google authorization found ({self.path}).")
        try:
            raw = self.keys.decrypt(self.path.read_text(encoding="utf-8"))
        except DecryptError:
            raise AuthRequired(
                f"Cannot decrypt {self.path}: GOOGLE_CLIENT_SECRET or INTERVALS_API_KEY "
                "changed since the last authorization."
            ) from None
        return StoredToken.from_json(raw)

    def save(self, token: StoredToken) -> bool:
        """Write the token. Returns False (and writes nothing) when it is unchanged,
        so the GitHub workflow does not create a commit on every run."""
        try:
            if self.load().to_json() == token.to_json():
                return False
        except AuthRequired:
            pass
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self.keys.encrypt(token.to_json()), encoding="utf-8")
        return True


def build_auth_url(settings: Settings, keys: Keys, encode_client_id: bool = False) -> str:
    """Consent link.

    With `encode_client_id` every character of the client ID is percent-encoded.
    The client ID is not secret (it is part of every OAuth link), but when it is
    stored as a GitHub secret, GitHub hides it in logs and job summaries and the
    link would break. Google decodes the encoded form normally.
    """
    state = keys.make_state(config.utcnow().timestamp())
    params = {
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": keys.pkce_challenge(state),
        "code_challenge_method": "S256",
    }
    if encode_client_id:
        client_id = "".join(f"%{b:02X}" for b in settings.google_client_id.encode("utf-8"))
    else:
        client_id = quote(settings.google_client_id, safe="")
    return f"{AUTH_ENDPOINT}?client_id={client_id}&{urlencode(params)}"


def parse_redirect_url(url: str) -> tuple[str, str]:
    """Return (code, state) from the address the browser ended on."""
    url = url.strip().strip("\"'")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.query:
        raise AuthError(
            "That does not look like the address from the browser. Copy the whole "
            "address bar: it starts with http://127.0.0.1:8765/?state="
        )
    if parts.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise AuthError(f"Unexpected address host {parts.hostname!r}: expected 127.0.0.1.")
    query = parse_qs(parts.query)
    if "error" in query:
        raise AuthError(
            f"Google returned '{query['error'][0]}': access was not granted. "
            "Open a new link and click Continue / Allow."
        )
    code = query.get("code", [""])[0]
    state = query.get("state", [""])[0]
    if not code or not state:
        raise AuthError("The address has no 'code' or 'state'. Copy the whole address bar.")
    return code, state


def _token_error(response: httpx.Response) -> tuple[str, str]:
    try:
        data = response.json()
    except ValueError:
        return f"http_{response.status_code}", response.text[:200]
    return str(data.get("error", "")), str(data.get("error_description", ""))


def exchange_code(
    settings: Settings, keys: Keys, redirect_url: str, http: httpx.Client
) -> tuple[StoredToken, list[str]]:
    """Exchange the code in `redirect_url` for a refresh token.

    Returns the token and the requested scopes that the user did not grant
    (Google lets users untick individual permissions on the consent screen).
    """
    code, state = parse_redirect_url(redirect_url)
    if not keys.check_state(state):
        raise AuthError(
            "This address belongs to a link created with different secrets "
            "(GOOGLE_CLIENT_SECRET / INTERVALS_API_KEY). Create a new link with auth-url."
        )
    response = http.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
            "code_verifier": keys.pkce_verifier(state),
        },
    )
    if response.status_code != 200:
        error, description = _token_error(response)
        hints = {
            "invalid_grant": "The code was already used or is too old (codes last a few "
            "minutes). Open a new link and paste the new address right away.",
            "invalid_client": "GOOGLE_CLIENT_ID or GOOGLE_CLIENT_SECRET is wrong.",
            "redirect_uri_mismatch": "The OAuth client must be of type 'Desktop app'.",
        }
        raise AuthError(f"Google refused the code ({error}: {description}). {hints.get(error, '')}".strip())
    data = response.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        raise AuthError(
            "Google did not return a refresh token. Remove the app's access at "
            "https://myaccount.google.com/permissions and authorize again."
        )
    now = config.utcnow()
    lifetime = data.get("refresh_token_expires_in")
    expires_at = now + (timedelta(seconds=int(lifetime)) if lifetime else DEFAULT_REFRESH_LIFETIME)
    granted = str(data.get("scope", "")).split()
    missing = [s for s in SCOPES if granted and s not in granted]
    token = StoredToken(refresh_token, now, expires_at, granted or list(SCOPES))
    return token, missing


def refresh_access_token(settings: Settings, token: StoredToken, http: httpx.Client) -> tuple[str, str | None]:
    """Return (access_token, new_refresh_token or None)."""
    response = http.post(
        TOKEN_ENDPOINT,
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": token.refresh_token,
            "grant_type": "refresh_token",
        },
    )
    if response.status_code == 200:
        data = response.json()
        return data["access_token"], data.get("refresh_token")
    error, description = _token_error(response)
    if error == "invalid_grant":
        raise AuthRequired(f"The Google authorization has expired or was revoked ({description or error}).")
    if error in ("invalid_client", "unauthorized_client"):
        raise AuthError(f"Google rejected the OAuth client ({error}). Check GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
    raise AuthError(f"Could not refresh the Google token (HTTP {response.status_code}: {error} {description}).")
