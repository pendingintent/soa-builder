from fastapi.testclient import TestClient
from soa_builder.web.app import app


def test_index_page_loads():
    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    assert "Create New SoA" in r.text or "Existing SoAs" in r.text
