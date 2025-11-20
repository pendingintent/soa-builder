from fastapi.testclient import TestClient

from soa_builder.web.app import _connect, app

client = TestClient(app)


def _create_soa(name="UIVisitTest"):
    resp = client.post("/soa", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_ui_add_visit_blank_epoch():
    soa_id = _create_soa()
    # Post without epoch (form supplies blank)
    resp = client.post(
        f"/ui/soa/{soa_id}/add_visit", data={"name": "Visit 1", "raw_header": ""}
    )
    assert resp.status_code == 200, resp.text
    # Fetch matrix to validate visit presence
    m = client.get(f"/soa/{soa_id}/matrix")
    assert m.status_code == 200
    data = m.json()
    visits = data.get("visits") or []
    assert any(
        v["name"] == "Visit 1" for v in visits
    ), f"Visit not found in matrix: {visits}"
    # Ensure epoch_id is null/None in DB
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT epoch_id FROM visit WHERE soa_id=? AND name=?", (soa_id, "Visit 1")
    )
    row = cur.fetchone()
    conn.close()
    assert row is not None
    assert row[0] is None, f"Expected NULL epoch_id, got {row[0]}"


def test_ui_add_visit_with_epoch():
    soa_id = _create_soa("UIVisitEpochTest")
    # Create an epoch first
    resp_epoch = client.post(f"/ui/soa/{soa_id}/add_epoch", data={"name": "Screening"})
    assert resp_epoch.status_code == 200
    # Lookup epoch id
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM epoch WHERE soa_id=? AND name=?", (soa_id, "Screening"))
    eid = cur.fetchone()[0]
    conn.close()
    # Provide legacy field name epoch_id
    resp = client.post(
        f"/ui/soa/{soa_id}/add_visit", data={"name": "Visit A", "epoch_id": str(eid)}
    )
    assert resp.status_code == 200, resp.text
    m = client.get(f"/soa/{soa_id}/matrix")
    data = m.json()
    visits = data.get("visits") or []
    found = [v for v in visits if v["name"] == "Visit A"]
    assert found, f"Visit A not found: {visits}"
    assert (
        found[0]["epoch_id"] == eid
    ), f"Expected epoch_id {eid}, got {found[0]['epoch_id']}"
