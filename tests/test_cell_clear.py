from fastapi.testclient import TestClient
from soa_builder.web.app import app, DB_PATH
import os
from importlib import reload
import soa_builder.web.app as webapp

client = TestClient(app)


def reset_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    reload(webapp)


def test_cell_clear_removes_row():
    reset_db()
    # create soa
    r = client.post("/soa", json={"name": "Clear Trial"})
    soa_id = r.json()["id"]
    # add visit & activity
        v = client.post(f"/soa/{soa_id}/visits", json={"name": "C1D1"}).json()["visit_id"]
    a = client.post(f"/soa/{soa_id}/activities", json={"name": "Hematology"}).json()[
        "activity_id"
    ]
        # set matrix_cell X
    c1 = client.post(
        f"/soa/{soa_id}/cells", json={"visit_id": v, "activity_id": a, "status": "X"}
    ).json()
    assert c1["status"] == "X"
        # confirm matrix_cell present
    m1 = client.get(f"/soa/{soa_id}/matrix").json()
    assert any(c["visit_id"] == v and c["activity_id"] == a for c in m1["cells"])
        # clear matrix_cell (blank status)
    cclear = client.post(
        f"/soa/{soa_id}/cells", json={"visit_id": v, "activity_id": a, "status": ""}
    ).json()
    assert cclear.get("deleted") is True
    # matrix should no longer have the cell
    m2 = client.get(f"/soa/{soa_id}/matrix").json()
        assert not any(c["visit_id"] == v and c["activity_id"] == a for c in m2["cells"])
    # re-add X
    c2 = client.post(
        f"/soa/{soa_id}/cells", json={"visit_id": v, "activity_id": a, "status": "X"}
    ).json()
    assert c2["status"] == "X"
    m3 = client.get(f"/soa/{soa_id}/matrix").json()
    assert any(
        c["visit_id"] == v and c["activity_id"] == a and c["status"] == "X"
        for c in m3["cells"]
    )
