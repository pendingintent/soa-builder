from fastapi.testclient import TestClient

from soa_builder.web.app import _connect, app

client = TestClient(app)


def create_study(name="Rollback Study"):
    r = client.post("/soa", json={"name": name})
    assert r.status_code == 200
    return r.json()["id"]


def main():
    soa_id = create_study()
    # Add elements
    client.post(f"/soa/{soa_id}/elements", json={"name": "E1"})
    client.post(f"/soa/{soa_id}/elements", json={"name": "E2", "label": "L2"})
    # Freeze
    fr = client.post(f"/ui/soa/{soa_id}/freeze", data={"version_label": "vElem"})
    assert fr.status_code == 200
    # Modify elements (delete one, add another, update one)
    # Get current elements
    els = client.get(f"/soa/{soa_id}/elements").json()
    # Endpoint returns a list of element objects each with id, name, label, etc.
    e1 = els[0]["id"]
    e2 = els[1]["id"]
    # Update first, delete second
    client.post(
        f"/ui/soa/{soa_id}/update_element", data={"element_id": e1, "name": "E1_mod"}
    )
    client.post(f"/ui/soa/{soa_id}/delete_element", data={"element_id": e2})
    client.post(f"/soa/{soa_id}/elements", json={"name": "E3"})
    # Perform rollback
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM soa_freeze WHERE soa_id=? ORDER BY id DESC", (soa_id,))
    fid = cur.fetchone()[0]
    conn.close()
    rb = client.post(f"/ui/soa/{soa_id}/freeze/{fid}/rollback")
    assert rb.status_code == 200, rb.text
    # Verify elements restored to exactly original two
    final_elements = client.get(f"/soa/{soa_id}/elements").json()
    names = sorted(e["name"] for e in final_elements)
    assert names == ["E1", "E2"], names
    # Verify audit includes elements_restored
    audit_resp = client.get(f"/soa/{soa_id}/rollback_audit").json()
    assert "audit" in audit_resp, f"Expected 'audit' key in response, got: {audit_resp}"
    audit = audit_resp["audit"]
    assert audit[0].get("elements_restored") == 2, audit[0]
    print("Rollback element restoration check passed.")


if __name__ == "__main__":
    main()
