from fastapi.testclient import TestClient

from soa_builder.web.app import app

client = TestClient(app)


def reset_db():
    # Disabled: preserve persistent DB across tests
    return


def test_ddf_dataset_date_column():
    reset_db()
    r = client.post("/admin/load_ddf_terminology")
    assert r.status_code == 200
    data = r.json()
    assert "dataset_date" in data["columns"] or "sheet_dataset_date" in data["columns"]
    # Query first row
    q = client.get("/ddf/terminology", params={"limit": 1})
    assert q.status_code == 200
    rows = q.json()["rows"]
    assert rows
    row = rows[0]
    col_name = "dataset_date" if "dataset_date" in row else "sheet_dataset_date"
    assert row[col_name] == "2025-09-26"
    # Audit row includes dataset_date
    audit = client.get("/ddf/terminology/audit")
    assert audit.status_code == 200
    audit_rows = audit.json()["rows"]
    assert audit_rows
    ar = audit_rows[-1]  # last inserted
    assert ar.get("dataset_date") == "2025-09-26"


def test_protocol_dataset_date_column():
    reset_db()
    r = client.post("/admin/load_protocol_terminology")
    assert r.status_code == 200
    data = r.json()
    assert "dataset_date" in data["columns"] or "sheet_dataset_date" in data["columns"]
    q = client.get("/protocol/terminology", params={"limit": 1})
    assert q.status_code == 200
    rows = q.json()["rows"]
    assert rows
    row = rows[0]
    col_name = "dataset_date" if "dataset_date" in row else "sheet_dataset_date"
    assert row[col_name] == "2025-09-26"
    audit = client.get("/protocol/terminology/audit")
    assert audit.status_code == 200
    audit_rows = audit.json()["rows"]
    assert audit_rows
    ar = audit_rows[-1]
    assert ar.get("dataset_date") == "2025-09-26"
