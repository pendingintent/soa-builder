import os
import json
from importlib import reload
from fastapi.testclient import TestClient
import soa_builder.web.app as webapp
from soa_builder.web.app import app, DB_PATH, fetch_biomedical_concepts, _concept_cache

client = TestClient(app)

SAMPLE1 = {
    "items": [
        {"concept_code": "BC001", "title": "Weight"},
        {"concept_code": "BC002", "title": "Height"},
    ]
}
SAMPLE2 = {
    "items": [
        {"concept_code": "BC001", "title": "Weight"},
        {"concept_code": "BC002", "title": "Height"},
        {"concept_code": "BC003", "title": "Blood Pressure"},
    ]
}


def reset_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    os.environ["CDISC_CONCEPTS_JSON"] = json.dumps(SAMPLE1)
    os.environ.pop("CDISC_SKIP_REMOTE", None)
    reload(webapp)


def test_refresh_concepts_updates_cache_and_ui():
    reset_db()
    # preload via startup should populate cache with SAMPLE1
    concepts_initial = fetch_biomedical_concepts()
    assert len(concepts_initial) == 2
    # create soa + activity
    r = client.post("/soa", json={"name": "Refresh Trial"})
    soa_id = r.json()["id"]
    client.post(f"/soa/{soa_id}/activities", json={"name": "Vitals"})
    # Change override to SAMPLE2 then hit refresh endpoint
    os.environ["CDISC_CONCEPTS_JSON"] = json.dumps(SAMPLE2)
    resp_refresh = client.post(f"/ui/soa/{soa_id}/concepts_refresh")
    assert resp_refresh.status_code == 200
    # cache should now have 3 concepts
    concepts_after = fetch_biomedical_concepts()
    assert len(concepts_after) == 3
    # edit page should include Blood Pressure
    html = client.get(f"/ui/soa/{soa_id}/edit").text
    assert "Blood Pressure" in html
