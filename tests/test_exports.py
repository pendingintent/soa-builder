from fastapi.testclient import TestClient

from soa_builder.web.app import app

client = TestClient(app)


def reset_db():
    # Disabled: preserve persistent DB across tests
    return


def _setup_matrix():
    r = client.post("/soa", json={"name": "Export Trial"})
    soa_id = r.json()["id"]
    v1 = client.post(f"/soa/{soa_id}/visits", json={"name": "C1D1"}).json()["visit_id"]
    v2 = client.post(f"/soa/{soa_id}/visits", json={"name": "C1D8"}).json()["visit_id"]
    a1 = client.post(f"/soa/{soa_id}/activities", json={"name": "Lab"}).json()[
        "activity_id"
    ]
    a2 = client.post(f"/soa/{soa_id}/activities", json={"name": "ECG"}).json()[
        "activity_id"
    ]
    client.post(
        f"/soa/{soa_id}/cells", json={"visit_id": v1, "activity_id": a1, "status": "X"}
    )
    client.post(
        f"/soa/{soa_id}/cells", json={"visit_id": v2, "activity_id": a2, "status": "X"}
    )
    return soa_id


def test_export_xlsx():
    reset_db()
    soa_id = _setup_matrix()
    resp = client.get(f"/soa/{soa_id}/export/xlsx")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(resp.content) > 1000  # basic size sanity


def test_export_pdf():
    reset_db()
    soa_id = _setup_matrix()
    resp = client.get(f"/soa/{soa_id}/export/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert len(resp.content) > 800  # PDF minimal bytes
