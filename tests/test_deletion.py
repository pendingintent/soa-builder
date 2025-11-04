from fastapi.testclient import TestClient
from soa_builder.web.app import app

client = TestClient(app)


def _create_soa(name="DelTest"):
    r = client.post("/soa", json={"name": name})
    assert r.status_code == 200
    return r.json()["id"]


def _add_visit(soa_id, name, raw_header=None):
    payload = {"name": name, "raw_header": raw_header or name}
    r = client.post(f"/soa/{soa_id}/visits", json=payload)
    assert r.status_code == 200
    return r.json()["visit_id"]


def _add_activity(soa_id, name):
    r = client.post(f"/soa/{soa_id}/activities", json={"name": name})
    assert r.status_code == 200
    return r.json()["activity_id"]


def _set_cell(soa_id, visit_id, activity_id, status="X"):
    r = client.post(
        f"/soa/{soa_id}/cells",
        json={"visit_id": visit_id, "activity_id": activity_id, "status": status},
    )
    assert r.status_code == 200


def test_delete_visit_cascades_and_reindexes():
    soa_id = _create_soa()
    v1 = _add_visit(soa_id, "V1")
    v2 = _add_visit(soa_id, "V2")
    a1 = _add_activity(soa_id, "A1")
    a2 = _add_activity(soa_id, "A2")
    _set_cell(soa_id, v1, a1, "X")
    _set_cell(soa_id, v2, a1, "X")
    _set_cell(soa_id, v2, a2, "O")

    # initial matrix
    m = client.get(f"/soa/{soa_id}/matrix").json()
    assert len(m["visits"]) == 2
    assert len(m["activities"]) == 2
    assert len(m["cells"]) >= 3  # upserted cells present

    # delete first visit
    dr = client.delete(f"/soa/{soa_id}/visits/{v1}")
    assert dr.status_code == 200

    m2 = client.get(f"/soa/{soa_id}/matrix").json()
    assert len(m2["visits"]) == 1
    remaining_visit = m2["visits"][0]
    # reindexed
    assert remaining_visit["order_index"] == 1
    assert remaining_visit["name"] == "V2"
    # cells referencing deleted visit removed
    assert all(c["visit_id"] != v1 for c in m2["cells"])

    # delete activity A1
    dr2 = client.delete(f"/soa/{soa_id}/activities/{a1}")
    assert dr2.status_code == 200
    m3 = client.get(f"/soa/{soa_id}/matrix").json()
    assert len(m3["activities"]) == 1
    remaining_activity = m3["activities"][0]
    assert remaining_activity["order_index"] == 1
    assert remaining_activity["name"] == "A2"
    assert all(c["activity_id"] != a1 for c in m3["cells"])


def test_delete_activity_reindexes_only():
    soa_id = _create_soa("DelTest2")
    v1 = _add_visit(soa_id, "Day1")
    a1 = _add_activity(soa_id, "Draw")
    a2 = _add_activity(soa_id, "Scan")
    _set_cell(soa_id, v1, a1)
    _set_cell(soa_id, v1, a2)

    m = client.get(f"/soa/{soa_id}/matrix").json()
    assert [a["order_index"] for a in m["activities"]] == [1, 2]

    dr = client.delete(f"/soa/{soa_id}/activities/{a1}")
    assert dr.status_code == 200
    m2 = client.get(f"/soa/{soa_id}/matrix").json()
    assert len(m2["activities"]) == 1
    assert m2["activities"][0]["order_index"] == 1
    assert m2["activities"][0]["name"] == "Scan"
    # cell referencing removed activity gone
    assert all(c["activity_id"] != a1 for c in m2["cells"])
