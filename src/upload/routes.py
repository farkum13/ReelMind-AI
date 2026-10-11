"""FastAPI routes for the YouTube OAuth web-server flow.

Mount in main.py:
    app.include_router(youtube_auth_router)

Endpoints:
    GET /auth/youtube           -> redirects browser to Google consent
    GET /auth/youtube/callback  -> exchanges the code, caches the token

In production set YT_REDIRECT_URI to your HTTPS callback so redirect URIs stay
stable behind proxies/load balancers (otherwise it is derived from the request).
"""

from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.upload.auth import (
    YOUTUBE_CALLBACK_PATH,
    authorization_url,
    build_flow,
    fetch_token_and_save,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["youtube-auth"])

# state -> redirect URI used for that consent run (single-operator app).
_pending: dict[str, str] = {}


def _callback_uri(request: Request) -> str:
    override = os.getenv("YT_REDIRECT_URI")
    if override:
        return override
    base = str(request.base_url).rstrip("/")
    return base + YOUTUBE_CALLBACK_PATH


@router.get("/auth/youtube")
async def auth_start(request: Request):
    redirect_uri = _callback_uri(request)
    state = secrets.token_urlsafe(16)
    _pending[state] = redirect_uri
    try:
        flow = build_flow(redirect_uri=redirect_uri)
    except Exception as exc:  # noqa: BLE001
        if state in _pending:
            _pending.pop(state)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth_url, _ = authorization_url(flow, state=state)
    return RedirectResponse(auth_url)


@router.get("/auth/youtube/callback")
async def auth_callback(request: Request, code: str | None = None,
                        state: str | None = None, error: str | None = None):
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    redirect_uri = _pending.pop(state, None)
    if redirect_uri is None:
        raise HTTPException(status_code=400, detail="Unknown or expired OAuth state")

    try:
        fetch_token_and_save(code=code, redirect_uri=redirect_uri)
    except Exception as exc:  # noqa: BLE001
        logger.exception("OAuth code exchange failed")
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {exc}") from exc

    return HTMLResponse(
        "<h2>YouTube authorized.</h2>"
        "<p>The publisher can now upload to this channel. You can close this tab.</p>"
    )