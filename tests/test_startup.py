import pytest
from fastapi.testclient import TestClient
from pymongo.errors import ServerSelectionTimeoutError

from app import main
from app.main import app


def test_app_starts_and_stops_cleanly_with_the_retry_loop():
    with TestClient(app) as running:  # entering the block runs the startup code
        assert running.get("/health").json() == {"status": "ok"}


def test_startup_stops_if_the_unique_indexes_cannot_be_created(monkeypatch):
    def database_down():
        raise ServerSelectionTimeoutError("no servers")

    monkeypatch.setattr(main, "init_db", database_down)
    with pytest.raises(ServerSelectionTimeoutError):
        with TestClient(app):
            pass
