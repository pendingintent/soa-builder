from fastapi.testclient import TestClient
from soa_builder.web.app import app, _connect

client = TestClient(app)


def create_study(name="Test Study"):
    resp = client.post("/soa", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def check_crud_audit():
    soa_id = create_study()
    r = client.post(
        f"/soa/{soa_id}/elements",
        json={
            "name": "ElemA",
            "label": "A",
            "description": "Desc",
            "testrl": "start X",
            "teenrl": "end Y",
        },
    )
    # create_element returns 201 and payload includes 'id'
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    r2 = client.patch(f"/soa/{soa_id}/elements/{eid}", json={"name": "ElemA2"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["name"] == "ElemA2"
    assert "name" in r2.json()["updated_fields"]
    r2b = client.patch(f"/soa/{soa_id}/elements/{eid}", json={"testrl": "start Z"})
    assert r2b.status_code == 200, r2b.text
    assert r2b.json()["testrl"] == "start Z"
    assert "testrl" in r2b.json()["updated_fields"]
    r3 = client.delete(f"/soa/{soa_id}/elements/{eid}")
    assert r3.status_code == 200, r3.text
    audit = client.get(
        f"/soa/{soa_id}/element_audit"
    ).json()  # endpoint returns raw list
    actions = {a["action"] for a in audit}
    assert actions == {"create", "update", "delete"}, actions


def check_freeze_elements():
    soa_id = create_study("Freeze Study")
    rA = client.post(
        f"/soa/{soa_id}/elements",
        json={"name": "E1", "label": "L1", "description": "D1", "testrl": "R1"},
    )
    rB = client.post(
        f"/soa/{soa_id}/elements",
        json={"name": "E2", "label": "Label2", "description": "Desc2", "teenrl": "R2"},
    )
    assert rA.status_code == 201 and rB.status_code == 201
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
    assert all("etcd" not in e for e in snap["elements"])  # ensure legacy field gone
    assert any(e.get("testrl") == "R1" for e in snap["elements"]) and any(
        e.get("teenrl") == "R2" for e in snap["elements"]
    )


def check_mandatory_enforcement():
    soa_id = create_study("Mandatory Study")
    # Only 'name' is required; optional fields may be omitted or blank (blank trimmed to None)
    r = client.post(f"/soa/{soa_id}/elements", json={"name": "X"})
    assert r.status_code == 201
    data = r.json()
    assert data["id"] and data.get("label") is None and data.get("description") is None
    # Blank fields become None
    r2 = client.post(
        f"/soa/{soa_id}/elements",
        json={"name": "Y", "label": "   ", "description": "   "},
    )
    assert r2.status_code == 201
    d2 = r2.json()
    assert d2.get("label") is None and d2.get("description") is None
    # Rules blank -> None
    r3 = client.post(
        f"/soa/{soa_id}/elements", json={"name": "Z", "testrl": "   ", "teenrl": ""}
    )
    assert r3.status_code == 201
    d3 = r3.json()
    assert d3.get("testrl") is None and d3.get("teenrl") is None
    # Update keeps existing name when partial
    eid = d3["id"]
    r4 = client.patch(f"/soa/{soa_id}/elements/{eid}", json={"label": "LabelZ"})
    assert r4.status_code == 200 and r4.json()["label"] == "LabelZ"
    assert (
        "label" in r4.json()["updated_fields"] and len(r4.json()["updated_fields"]) == 1
    )


if __name__ == "__main__":
    check_crud_audit()
    check_freeze_elements()
    check_mandatory_enforcement()
    print("Element checks passed.")
