from fastapi.testclient import TestClient

from soa_builder.web.app import _connect, app

client = TestClient(app)


def _create_soa(name="ElementTest"):
    resp = client.post("/soa", json={"name": name})
    assert resp.status_code == 200
    return resp.json()["id"]


def test_add_element_populates_element_id_if_present():
    soa_id = _create_soa()
    resp = client.post(f"/ui/soa/{soa_id}/add_element", data={"name": "Elem A"})
    assert resp.status_code == 200, resp.text
    conn = _connect()
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(element)")
    cols = {r[1] for r in cur.fetchall()}
    if "element_id" in cols:
        cur.execute(
            "SELECT id, element_id, name FROM element WHERE soa_id=? AND name=?",
            (soa_id, "Elem A"),
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None
        assert row[1] is not None and row[1] != "", "element_id should be populated"
    else:
        cur.execute(
            "SELECT id, name FROM element WHERE soa_id=? AND name=?", (soa_id, "Elem A")
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None
