import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pymongo import ReturnDocument

from app import config, github
from app.db import users, utcnow
from app.logs import log
from app.security import encrypt_token

router = APIRouter(prefix="/auth")

# public_repo: add labels/comments on public repos. admin:repo_hook: create the webhook.
SCOPES = "public_repo admin:repo_hook"


@router.get("/login")
def login(request: Request):
    if not config.GITHUB_CLIENT_ID or not config.GITHUB_CLIENT_SECRET:
        raise HTTPException(503, "GitHub login is not configured")
    # A random one-time "state" proves the callback belongs to a login WE started (blocks login CSRF).
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    query = urlencode({
        "client_id": config.GITHUB_CLIENT_ID,
        "redirect_uri": config.APP_URL + "/auth/callback",
        "scope": SCOPES,
        "state": state,
    })
    return RedirectResponse("https://github.com/login/oauth/authorize?" + query)


@router.get("/callback")
def callback(request: Request, code: str = "", state: str = ""):
    expected = request.session.pop("oauth_state", None)
    # Bytes, not str: compare_digest raises on non-ASCII str and "state" comes from the URL.
    if not expected or not secrets.compare_digest(expected.encode(), state.encode()):
        raise HTTPException(400, "Login failed: invalid or expired state. Please try again.")
    if not code:
        raise HTTPException(400, "Login was cancelled.")
    try:
        token = github.exchange_code(code)
        profile = github.get_user(token)
    except (github.GitHubError, httpx.HTTPError) as e:
        log("login", "failed", detail=type(e).__name__)
        raise HTTPException(502, "Login failed: could not talk to GitHub.")

    user = users.find_one_and_update(
        {"github_id": profile["id"]},
        {
            "$set": {
                "login": profile["login"],
                "avatar_url": profile.get("avatar_url"),
                "access_token_enc": encrypt_token(token),
            },
            "$setOnInsert": {"created_at": utcnow()},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    request.session.clear()
    request.session["user_id"] = str(user["_id"])
    log("login", "ok", detail=user["login"])
    return RedirectResponse("/dashboard", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)
