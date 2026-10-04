from fastapi.testclient import TestClient

from app.api.deps import require_admin
from app.db.session import get_db
from app.main import create_app


class _Result:
    def scalar_one(self):
        return "160015"


class OkSession:
    def execute(self, *args, **kwargs):
        return _Result()


class BrokenSession:
    def execute(self, *args, **kwargs):
        raise RuntimeError("base indisponible")


def _app_as_admin(session):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_admin] = lambda: None
    return app


def test_liveness_is_public():
    r = TestClient(create_app()).get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_details_requires_authentication():
    r = TestClient(create_app()).get("/api/health/details")
    assert r.status_code == 401


def test_details_ok():
    r = TestClient(_app_as_admin(OkSession())).get("/api/health/details")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["database"] == {"ok": True, "server_version_num": 160015}
    assert body["mode"] == {"revocation_mode": "dry_run", "enforcer_enabled": False}


def test_details_returns_503_when_database_is_down():
    r = TestClient(_app_as_admin(BrokenSession())).get("/api/health/details")
    assert r.status_code == 503
    assert r.json()["database"]["ok"] is False
