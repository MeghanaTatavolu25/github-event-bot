import re

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pymongo.errors import DuplicateKeyError

from app import github
from app.db import repos, utcnow
from app.logs import log
from app.rules import add_example_rule
from app.security import decrypt_token
from app.web import current_user, flash, render

router = APIRouter()

# Also keeps odd characters out of the GitHub API paths we build from this text.
FULL_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def token_of(user: dict) -> str:
    try:
        return decrypt_token(user["access_token_enc"])
    except InvalidToken:
        raise ValueError("Your saved GitHub login is no longer valid. Sign out and sign in again.")


def connect_repo(user: dict, name: str) -> str:
    """Connect a repo. Returns a message for the user, or raises ValueError with one."""
    if not FULL_NAME.match(name):
        raise ValueError("Use the format owner/name, for example octocat/hello-world.")
    token = token_of(user)
    try:
        repo = github.get_repo(token, name)
        if not repo.get("permissions", {}).get("admin"):
            raise ValueError("You need admin access to that repo to add a webhook.")
        full_name = repo["full_name"]  # GitHub's spelling: webhook payloads use exactly this name
        if repos.find_one({"full_name": full_name}):
            raise ValueError(f"{full_name} is already connected.")
        first_repo = repos.count_documents({"user_id": user["_id"]}) == 0
        hook_id = github.create_hook(token, full_name)
    except github.GitHubError as e:
        raise ValueError("Repo not found, or you cannot see it." if e.status == 404 else str(e))
    except httpx.HTTPError:
        raise ValueError("Could not reach GitHub. Try again in a moment.")

    try:
        repos.insert_one(
            {"user_id": user["_id"], "full_name": full_name, "webhook_id": hook_id, "connected_at": utcnow()}
        )
    except DuplicateKeyError:  # someone connected it between our check and now
        raise ValueError(f"{full_name} is already connected.")
    if first_repo:
        add_example_rule(user["_id"], full_name)
    log("repo", "connected", repo=full_name)
    return f"Connected {full_name}."


def disconnect_repo(user: dict, full_name: str) -> str:
    repo = repos.find_one({"user_id": user["_id"], "full_name": full_name})
    if not repo:
        raise ValueError("That repo is not connected.")
    try:
        github.delete_hook(token_of(user), full_name, repo["webhook_id"])
    except github.GitHubError as e:
        raise ValueError(f"Could not remove the webhook ({e}). The repo stays connected.")
    except httpx.HTTPError:
        raise ValueError("Could not reach GitHub. Try again in a moment.")
    repos.delete_one({"_id": repo["_id"]})
    log("repo", "disconnected", repo=full_name)
    return f"Disconnected {full_name}."


@router.get("/repos")
def repos_page(request: Request, user: dict = Depends(current_user)):
    connected = list(repos.find({"user_id": user["_id"]}).sort("connected_at", 1))
    return render(request, "repos.html", user, repos=connected)


@router.post("/repos/connect")
def connect(request: Request, full_name: str = Form(...), user: dict = Depends(current_user)):
    try:
        flash(request, connect_repo(user, full_name.strip()))
    except ValueError as e:
        flash(request, str(e))
    return RedirectResponse("/repos", status_code=303)


@router.post("/repos/disconnect")
def disconnect(request: Request, full_name: str = Form(...), user: dict = Depends(current_user)):
    try:
        flash(request, disconnect_repo(user, full_name))
    except ValueError as e:
        flash(request, str(e))
    return RedirectResponse("/repos", status_code=303)
