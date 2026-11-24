from unittest.mock import Mock

from fastapi.testclient import TestClient

from t2s.main import create_app


def test_health_does_not_call_dependencies():
    mysql = Mock(side_effect=RuntimeError("private connection string"))
    app = create_app(probes={"mysql": mysql})
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    mysql.assert_not_called()


def test_ready_checks_each_dependency():
    probes = {name: Mock() for name in ("mysql", "redis", "catalog")}
    with TestClient(create_app(probes=probes)) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"mysql": "ok", "redis": "ok", "catalog": "ok"},
    }
    for probe in probes.values():
        probe.assert_called_once_with()


def test_ready_returns_503_for_a_failed_dependency():
    probes = {
        "mysql": Mock(side_effect=RuntimeError("private connection string")),
        "redis": Mock(),
        "catalog": Mock(),
    }
    with TestClient(create_app(probes=probes)) as client:
        response = client.get("/ready")
        assert client.get("/health").status_code == 200
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"mysql": "unavailable", "redis": "ok", "catalog": "ok"},
    }
    assert "private connection string" not in response.text
