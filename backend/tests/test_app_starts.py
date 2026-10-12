"""Importing the application runs the start-up code: logging setup, settings and route registration. A cleanup
that removes a setting still in use breaks here, rather than in the running container."""


def test_application_imports_and_registers_its_routes():
    from app.main import app

    paths = set(app.openapi()["paths"])
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/sso/callback" in paths
    assert "/api/v1/admin/sso" in paths
    assert not any(p.startswith("/api/v1/demo") for p in paths)
    assert "/health" in paths
