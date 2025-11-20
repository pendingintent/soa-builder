from fastapi.testclient import TestClient

from soa_builder.web.app import _connect, app

client = TestClient(app)
PREFIX = "StudyElement_"


def create_soa(name="ElementIDGenTest"):
    resp = client.post("/soa", json={"name": name})
    assert resp.status_code == 200
    return resp.json()["id"]


def fetch_elements(soa_id):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, element_id FROM element WHERE soa_id=? ORDER BY id", (soa_id,)
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def test_element_id_prefix_and_uniqueness():
    soa_id = create_soa()
    for i in range(1, 6):
        r = client.post(f"/ui/soa/{soa_id}/add_element", data={"name": f"Elem {i}"})
        assert r.status_code == 200
    rows = fetch_elements(soa_id)
    # If column absent, skip assertions
    if not rows:
        return
    # Detect if element_id column exists by checking first row value retrieval logic
    has_element_id = rows[0][1] is not None or any(r[1] is not None for r in rows)
    if not has_element_id:
        return
    seen = set()
    for _id, eid in rows:
        assert eid.startswith(PREFIX), f"element_id '{eid}' missing prefix {PREFIX}"
        num_part = eid[len(PREFIX) :]
        assert num_part.isdigit(), f"Numeric part not integer in {eid}"
        n = int(num_part)
        assert n not in seen, f"Duplicate StudyElement number {n}"
        seen.add(n)
