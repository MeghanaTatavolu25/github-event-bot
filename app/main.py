from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pymongo.errors import PyMongoError
from starlette.middleware.sessions import SessionMiddleware

from app import auth, config, dashboard, repos, webhook
from app.db import client, init_db
from app.logs import log
from app.web import templates

# Fail at startup with a clear message instead of misbehaving later (a blank secret signs cookies with nothing).
if not config.SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is not set")
if not config.APP_URL.startswith(("http://", "https://")):
    raise RuntimeError("APP_URL must start with http:// or https:// (check .env for a typo)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once when the app starts.
    try:
        init_db()
    except PyMongoError:
        log("startup", "db_error", detail="could not create indexes")
    yield


app = FastAPI(title="GitHub Event Bot", lifespan=lifespan)
# The session cookie is signed, HttpOnly (Starlette default) and SameSite=Lax, which also stops
# other websites from making the browser send our POST forms. Secure only when served over https.
app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    session_cookie="session",
    max_age=14 * 24 * 3600,
    same_site="lax",
    https_only=config.APP_URL.startswith("https://"),
)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(repos.router)
app.include_router(webhook.router)


@app.get("/")
def landing(request: Request):
    if request.session.get("user_id"):
        return RedirectResponse("/dashboard")
    return templates.TemplateResponse(request, "landing.html", {"user": None, "flash": None})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    try:
        client.admin.command("ping")
        return {"database": "connected"}
    except Exception:
        return JSONResponse({"database": "unreachable"}, status_code=503)
