from fastapi import FastAPI
from fastapi.responses import JSONResponse
from app.db import client

app = FastAPI(title="GitHub Event Bot")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/db")
def health_db():
    try:
        client.admin.command("ping")
        return {"database": "connected"}
    except Exception:
        # Never return the real error: it can contain connection details.
        return JSONResponse({"database": "unreachable"}, status_code=503)