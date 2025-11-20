from fastapi.testclient import TestClient

from soa_builder.web.app import _connect, app

client = TestClient(app)


def _create_soa(name="EpochAssignTest"):
    resp = client.post("/soa", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _get_epoch_id(soa_id, name):
    conn = _connect()
    cur = conn.cursor()
    cur.execute("SELECT id FROM epoch WHERE soa_id=? AND name=?", (soa_id, name))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None


def _get_visit_row(soa_id, name):
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, epoch_id FROM visit WHERE soa_id=? AND name=?", (soa_id, name)
    )
    row = cur.fetchone()
    conn.close()
    return row


def test_assign_epoch_to_existing_visit():
    soa_id = _create_soa()
    # Add visit with no epoch
    resp_v = client.post(
        f"/ui/soa/{soa_id}/add_visit", data={"name": "V1", "raw_header": ""}
    )
    assert resp_v.status_code == 200
    visit_row = _get_visit_row(soa_id, "V1")
    assert visit_row and visit_row[1] is None
    # Add an epoch
    resp_e = client.post(f"/ui/soa/{soa_id}/add_epoch", data={"name": "Screening"})
    assert resp_e.status_code == 200
    eid = _get_epoch_id(soa_id, "Screening")
    assert eid is not None
    # Assign epoch using legacy field name epoch_id
    resp_set = client.post(
        f"/ui/soa/{soa_id}/set_visit_epoch",
        data={"visit_id": visit_row[0], "epoch_id": str(eid)},
    )
    assert resp_set.status_code == 200, resp_set.text
    visit_row_updated = _get_visit_row(soa_id, "V1")
    assert (
        visit_row_updated[1] == eid
    ), f"Expected epoch_id {eid}, got {visit_row_updated[1]}"


def test_clear_epoch_from_visit():
    soa_id = _create_soa("EpochClearTest")
    client.post(f"/ui/soa/{soa_id}/add_epoch", data={"name": "Treatment"})
    eid = _get_epoch_id(soa_id, "Treatment")
    # Add visit with epoch
    resp_v = client.post(
        f"/ui/soa/{soa_id}/add_visit", data={"name": "V1", "epoch_id": str(eid)}
    )
    assert resp_v.status_code == 200
    visit_row = _get_visit_row(soa_id, "V1")
    assert visit_row[1] == eid
    # Clear epoch (send blank)
    resp_clear = client.post(
        f"/ui/soa/{soa_id}/set_visit_epoch",
        data={"visit_id": visit_row[0], "epoch_id": ""},
    )
    assert resp_clear.status_code == 200
    visit_row_cleared = _get_visit_row(soa_id, "V1")
    assert (
        visit_row_cleared[1] is None
    ), f"Expected epoch cleared to NULL, got {visit_row_cleared[1]}"
