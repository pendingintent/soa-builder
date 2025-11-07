from fastapi.testclient import TestClient
from soa_builder.web.app import app, _connect

client = TestClient(app)


def create_study(name="Test Study"):
    resp = client.post("/soa", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_element_crud_and_audit():
    soa_id = create_study()
    r = client.post(
        f"/soa/{soa_id}/elements",
        json={"name": "ElemA", "label": "A", "description": "Desc"},
    )
    assert r.status_code == 200, r.text
    eid = r.json()["element_id"]
    r2 = client.patch(f"/soa/{soa_id}/elements/{eid}", json={"name": "ElemA2"})
    assert r2.status_code == 200, r2.text
    assert "name" in r2.json()["updated_fields"]
    r3 = client.delete(f"/soa/{soa_id}/elements/{eid}")
    assert r3.status_code == 200, r3.text
    audit = client.get(f"/soa/{soa_id}/element_audit").json()
    actions = {a["action"] for a in audit}
    assert actions == {"create", "update", "delete"}


def test_elements_in_freeze_snapshot():
    soa_id = create_study("Freeze Study")
    client.post(f"/soa/{soa_id}/elements", json={"name": "E1"})
    client.post(f"/soa/{soa_id}/elements", json={"name": "E2", "label": "Label2"})
    fr = client.post(f"/ui/soa/{soa_id}/freeze", data={"version_label": "vTest"})
    assert fr.status_code == 200, fr.text
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM soa_freeze WHERE soa_id=? ORDER BY id DESC", (soa_id,))
    fid = cur.fetchone()[0]
    conn.close()
    snap = client.get(f"/soa/{soa_id}/freeze/{fid}").json()
    assert "elements" in snap
    assert len(snap["elements"]) == 2
