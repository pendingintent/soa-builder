from fastapi.testclient import TestClient

from soa_builder.web.app import app

client = TestClient(app)


def reset_db():
    # Disabled: preserve persistent DB across tests
    return


def extract_cell_text(html: str) -> str:
    # crude extraction of inner text between > and < for first td with class="cell"
    import re

    m = re.search(r'<td[^>]*class="cell"[^>]*>(.*?)</td>', html, re.DOTALL)
    return m.group(1).strip() if m else ""


def test_toggle_cycle_blank_to_x_and_back():
    reset_db()
    # create soa
    r = client.post("/soa", json={"name": "Toggle Trial"})
    soa_id = r.json()["id"]
    v = client.post(f"/soa/{soa_id}/visits", json={"name": "C1D1"}).json()["visit_id"]
    a = client.post(f"/soa/{soa_id}/activities", json={"name": "Lab"}).json()[
        "activity_id"
    ]
    # initial matrix fetch
    m = client.get(f"/soa/{soa_id}/matrix").json()
    assert m["cells"] == []  # blank
    # toggle to X
    resp1 = client.post(
        f"/ui/soa/{soa_id}/toggle_cell", data={"visit_id": v, "activity_id": a}
    )
    assert resp1.status_code == 200
    assert extract_cell_text(resp1.text) == "X"
    # toggle back to blank
    resp2 = client.post(
        f"/ui/soa/{soa_id}/toggle_cell", data={"visit_id": v, "activity_id": a}
    )
    assert resp2.status_code == 200
    assert extract_cell_text(resp2.text) == ""
    # verify DB state cleared
    m2 = client.get(f"/soa/{soa_id}/matrix").json()
    assert m2["cells"] == []
    # toggle again -> X
    resp3 = client.post(
        f"/ui/soa/{soa_id}/toggle_cell", data={"visit_id": v, "activity_id": a}
    )
    assert extract_cell_text(resp3.text) == "X"
