import os
import json
import tempfile
import sqlite3
from fastapi.testclient import TestClient
from soa_builder.web.app import app, DB_PATH

client = TestClient(app)


def reset_db():
    # Disabled: preserve persistent DB across tests
    return


def test_create_and_normalize_flow():
    reset_db()
    # create soa
    r = client.post("/soa", json={"name": "Test Trial"})
    assert r.status_code == 200
    soa_id = r.json()["id"]
    # add visit
    rv = client.post(
        f"/soa/{soa_id}/visits",
        json={"name": "C1D1", "raw_header": "Cycle 1 Day 1 (C1D1)"},
    )
    assert rv.status_code == 200
    visit_id = rv.json()["visit_id"]
    # add activity
    ra = client.post(f"/soa/{soa_id}/activities", json={"name": "Hematology"})
    assert ra.status_code == 200
    activity_id = ra.json()["activity_id"]
    # set matrix_cell
    rc = client.post(
        f"/soa/{soa_id}/cells",
        json={"visit_id": visit_id, "activity_id": activity_id, "status": "X"},
    )
    assert rc.status_code == 200
    # matrix
    rm = client.get(f"/soa/{soa_id}/matrix")
    assert rm.status_code == 200
    data = rm.json()
    assert len(data["visits"]) == 1 and len(data["activities"]) == 1
    # normalized
    rn = client.get(f"/soa/{soa_id}/normalized")
    assert rn.status_code == 200
    summary = rn.json()["summary"]
    assert summary["visits"] >= 1
    assert summary["activities"] >= 1
