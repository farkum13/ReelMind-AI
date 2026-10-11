"""OAuth 2.0 for YouTube Data API v3 (web-server flow).

Two ways to authorize, depending on environment:

1. **Web (deployed app)** — mount `src.upload.routes.router` in FastAPI:
   `GET /auth/youtube` redirects to Google, `GET /auth/youtube/callback`
   exchanges the code and caches the token. Uses a registered HTTPS
   redirect URI from your *Web application* OAuth client.

2. **Dev (local)** — `python -m src.upload.auth`. Uses a loopback redirect
   URI that you register on the SAME web client (e.g.
   `http://localhost:7000/` in "Authorized redirect URIs"). The helper picks
   the registered loopback URI automatically (host/port/trailing slash all
   match). Token is cached at data/youtube_token.json for local testing.

The consent happens once (things only recur if you revoke the grant or leave
the OAuth consent screen in *Testing*, where refresh tokens die after 7 days).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_FORCE_SSL_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"

SCOPES = [YOUTUBE_UPLOAD_SCOPE, YOUTUBE_FORCE_SSL_SCOPE]

DEFAULT_CLIENT_SECRETS = Path(
    os.getenv("YT_CLIENT_SECRETS", REPO_ROOT / "data" / "youtube_client_secrets.json")
)
DEFAULT_TOKEN_FILE = Path(
    os.getenv("YT_TOKEN_FILE", REPO_ROOT / "data" / "youtube_token.json")
)

# Loopback redirect used only by the local/desktop authorize helper.
# Port defaults to the app's development port (uvicorn default here is 7000);
# override via YT_AUTH_PORT if yours differs.
AUTH_HOST = os.getenv("YT_AUTH_HOST", "127.0.0.1")
AUTH_PORT = int(os.getenv("YT_AUTH_PORT", "7000"))

# Canonical callback for the deployed app: <scheme>://<host>/auth/youtube/callback.
YOUTUBE_CALLBACK_PATH = "/auth/youtube/callback"


class MissingClientSecretsError(FileNotFoundError):
    pass


class RedirectUriNotRegisteredError(RuntimeError):
    pass


class AuthorizationRequiredError(RuntimeError):
    """No usable token cached — user must complete the web/CLI OAuth flow."""


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def load_config(client_secrets_file: Path | str) -> dict:
    import json

    path = Path(client_secrets_file)
    if not path.exists():
        raise MissingClientSecretsError(
            f"OAuth client secrets not found at {path}. Create a Web application "
            "OAuth client in Google Cloud Console and download the JSON here."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def config_type(config: dict) -> str:
    if "installed" in config:
        return "installed"
    if "web" in config:
        return "web"
    return "unknown"


def _registered_redirect_uris(config: dict) -> list[str]:
    return list((config.get("installed") or config.get("web") or {}).get(
        "redirect_uris", []
    ))


def _normalize_redirect(uri: str) -> str:
    """Strip the trailing slash so `http://host:7000` == `http://host:7000/`.

    Google treats the trailing slash as insignificant when matching loopback
    redirects, and you may register either spelling.
    """
    return uri.rstrip("/")


def _loopback_variant(uri: str) -> str:
    """Swap localhost <-> 127.0.0.1 for a loopback equivalence check."""
    if "://localhost:" in uri:
        return uri.replace("://localhost:", "://127.0.0.1:")
    if "://127.0.0.1:" in uri:
        return uri.replace("://127.0.0.1:", "://localhost:")
    return uri


def require_redirect_uri(client_secrets_file: Path | str, redirect_uri: str) -> None:
    """Fail fast if `redirect_uri` isn't registered on the OAuth client.

    Loopback (localhost/127.0.0.1) redirects are exempt for **Desktop app**
    clients, but Web clients must list every redirect URI exactly. Comparison
    ignores trailing slashes and treats localhost/127.0.0.1 as equivalent.
    """
    config = load_config(client_secrets_file)
    if config_type(config) == "installed":
        return
    allowed = _registered_redirect_uris(config)
    normalized = _normalize_redirect(redirect_uri)
    if normalized in {_normalize_redirect(u) for u in allowed}:
        return
    if _loopback_variant(normalized) in {_normalize_redirect(u) for u in allowed}:
        return
    hint = ""
    if not allowed:
        hint = (
            " If you just added URIs in the console, RE-DOWNLOAD the OAuth "
            "client JSON (Console → Credentials → your client → download) — this "
            "file does not update by itself."
        )
    raise RedirectUriNotRegisteredError(
        f"Redirect URI {redirect_uri!r} is not registered on your Web OAuth client. "
        "In Google Cloud Console → Credentials → your OAuth client → 'Authorized "
        "redirect URIs', add it. Currently registered: "
        + (", ".join(allowed) if allowed else "none")
        + hint
    )


# ---------------------------------------------------------------------------
# Cached token (used by the scheduler/crontab — must not prompt)
# ---------------------------------------------------------------------------
def get_credentials(
    client_secrets_file: Path | str = DEFAULT_CLIENT_SECRETS,
    token_file: Path | str = DEFAULT_TOKEN_FILE,
    scopes: list[str] | None = None,
) -> Credentials:
    """Return valid cached credentials, refresh when needed.

    Raises AuthorizationRequiredError when no token is cached — the operator
    must visit /auth/youtube (deployed) or run `python -m src.upload.auth`.
    """
    scopes = scopes or SCOPES
    token_file = Path(token_file)

    if not token_file.exists():
        raise AuthorizationRequiredError(
            f"No cached token at {token_file}. Complete OAuth once: "
            "http://<your-app>/auth/youtube (web) or `python -m src.upload.auth` "
            "on a machine with a browser."
        )

    creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        logger.info("Access token expired — refreshing with cached refresh token")
        creds.refresh(Request())
        _save_token(creds, token_file)
        return creds

    raise AuthorizationRequiredError(
        f"Token at {token_file} has no usable refresh token — re-run the OAuth flow."
    )


# ---------------------------------------------------------------------------
# Web-server flow (deployed FastAPI app)
# ---------------------------------------------------------------------------
def build_flow(
    redirect_uri: str,
    client_secrets_file: Path | str = DEFAULT_CLIENT_SECRETS,
    scopes: list[str] | None = None,
) -> Flow:
    scopes = scopes or SCOPES
    require_redirect_uri(client_secrets_file, redirect_uri)
    flow = Flow.from_client_secrets_file(str(client_secrets_file), scopes=scopes)
    flow.redirect_uri = redirect_uri
    return flow


def authorization_url(
    flow: Flow,
    state: str | None = None,
) -> tuple[str, str]:
    """Return (google auth URL, state) for redirecting the user's browser."""
    return flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
        state=state,
    )


def fetch_token_and_save(
    code: str,
    redirect_uri: str,
    client_secrets_file: Path | str = DEFAULT_CLIENT_SECRETS,
    token_file: Path | str = DEFAULT_TOKEN_FILE,
    scopes: list[str] | None = None,
) -> Credentials:
    scopes = scopes or SCOPES
    token_file = Path(token_file)
    flow = build_flow(redirect_uri, client_secrets_file=client_secrets_file, scopes=scopes)
    flow.fetch_token(code=code)
    _save_token(flow.credentials, token_file)
    logger.info("Authorized; token cached at %s", token_file)
    return flow.credentials


# ---------------------------------------------------------------------------
# Local/desktop helper (`python -m src.upload.auth`)
# ---------------------------------------------------------------------------
def _pick_loopback(config: dict, env_port: int) -> tuple[str, str, int, bool]:
    """Return (redirect_uri, host, port, trailing_slash) for the local server.

    Prefers a loopback URI that is *actually registered* on the client so the
    redirect Google checks matches exactly. Falls back to 127.0.0.1/<env port>
    for Desktop clients (any loopback port is allowed there).
    """
    for uri in _registered_redirect_uris(config):
        parsed = urlparse(uri)
        if parsed.scheme == "http" and parsed.hostname in (
            "localhost", "127.0.0.1", "::1",
        ):
            host = parsed.hostname
            port = parsed.port or env_port
            path = parsed.path.rstrip("/")
            if path:
                # run_local_server can only serve at the root; a loopback URI
                # with a path can't be reproduced locally.
                continue
            host = "[::1]" if host == "::1" else host
            slash = uri.endswith("/")
            redirect = f"http://{host}:{port}/" if slash else f"http://{host}:{port}"
            return redirect, host, port, slash
    return f"http://{AUTH_HOST}:{env_port}/", AUTH_HOST, env_port, True


def interactive_authorize(
    client_secrets_file: Path | str = DEFAULT_CLIENT_SECRETS,
    token_file: Path | str = DEFAULT_TOKEN_FILE,
    scopes: list[str] | None = None,
) -> Credentials:
    scopes = scopes or SCOPES
    config = load_config(client_secrets_file)

    redirect_uri, host, port, trailing_slash = _pick_loopback(config, AUTH_PORT)
    if config_type(config) == "web" and not _registered_redirect_uris(config):
        raise RedirectUriNotRegisteredError(
            f"Your Web client (at {client_secrets_file}) registers no redirect URIs. "
            "Add e.g. `http://localhost:7000/` to 'Authorized redirect URIs' in "
            "Google Cloud Console → Credentials → your OAuth client, then "
            "RE-DOWNLOAD the JSON and replace this file."
        )
    require_redirect_uri(client_secrets_file, redirect_uri)

    logger.info("Opening %s:%s for one-time consent...", host, port)
    logger.info("Redirect URI: %s", redirect_uri)
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_file), scopes)
    creds = flow.run_local_server(
        host=host,
        port=port,
        open_browser=not _is_headless(),
        prompt="consent",
        redirect_uri_trailing_slash=trailing_slash,
    )
    _save_token(creds, token_file)
    logger.info("Authorized; token cached at %s", token_file)
    return creds


def _save_token(creds: Credentials, token_file: Path) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")


def _is_headless() -> bool:
    if os.environ.get("BROWSER"):
        return False
    if os.name == "nt":
        return False
    return not bool(os.environ.get("DISPLAY"))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    interactive_authorize()
    print("✓ YouTube OAuth authorized.")