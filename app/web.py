from pathlib import Path

from bson import ObjectId
from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from app.db import users

# Jinja2Templates escapes HTML by default, which matters: issue titles are written by strangers.
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


def current_user(request: Request) -> dict:
    """Dependency for every protected route: the logged-in user, or a redirect to the landing page."""
    user_id = request.session.get("user_id")
    user = users.find_one({"_id": ObjectId(user_id)}) if user_id else None
    if not user:
        # Clear a stale cookie, otherwise "/" and "/dashboard" would redirect to each other forever.
        request.session.clear()
        raise HTTPException(status_code=303, headers={"Location": "/"})
    return user


def flash(request: Request, message: str):
    """A one-time message shown on the next page (we redirect after every POST)."""
    request.session["flash"] = message


def render(request: Request, name: str, user: dict, status_code: int = 200, **context):
    context.update(user=user, flash=request.session.pop("flash", None))
    return templates.TemplateResponse(request, name, context, status_code=status_code)
