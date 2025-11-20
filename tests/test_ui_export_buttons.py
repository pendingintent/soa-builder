from fastapi.testclient import TestClient

from soa_builder.web.app import app

client = TestClient(app)


def reset_db():
    # Disabled: preserve persistent DB across tests
    return


def test_edit_page_has_export_buttons():
    reset_db()
    # create soa and minimal visit/activity to allow exports
    r = client.post("/soa", json={"name": "Export Buttons Trial"})
    soa_id = r.json()["id"]
    client.post(f"/soa/{soa_id}/visits", json={"name": "C1D1"})
    client.post(f"/soa/{soa_id}/activities", json={"name": "Lab"})
    # fetch edit UI
    resp = client.get(f"/ui/soa/{soa_id}/edit")
    assert resp.status_code == 200
    html = resp.text
    # verify links present
    assert f"/soa/{soa_id}/export/xlsx" in html
    assert f"/soa/{soa_id}/export/pdf" in html
