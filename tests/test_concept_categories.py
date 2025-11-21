import json
from typing import List
from fastapi.testclient import TestClient
import pytest

from soa_builder.web.app import app, fetch_biomedical_concepts_by_category

client = TestClient(app)


class DummyResp:
    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json = json_data
        self.text = text or json.dumps(json_data or {})

    def json(self):
        if self._json is None:
            raise ValueError("No JSON")
        return self._json


@pytest.mark.parametrize(
    "raw_category, expected_url_fragment",
    [
        (
            "Liver Findings",
            "biomedicalconcepts?category=Liver%20Findings",
        ),  # encoding applied
        (
            "Already%20Encoded",
            "biomedicalconcepts?category=Already%20Encoded",
        ),  # no double encoding
    ],
)
def test_helper_category_url_encoding(monkeypatch, raw_category, expected_url_fragment):
    """Ensure category name is encoded only once and request performed."""
    captured_urls: List[str] = []

    def fake_get(url, headers=None, timeout=0):
        captured_urls.append(url)
        return DummyResp(200, {"items": []})

    monkeypatch.setattr("requests.get", fake_get)
    fetch_biomedical_concepts_by_category(raw_category)
    assert len(captured_urls) == 1
    assert expected_url_fragment in captured_urls[0]


def test_helper_parses_items_list(monkeypatch):
    """Direct items list should return normalized concept dicts."""
    payload = {
        "items": [
            {
                "code": "ALT",
                "title": "Alanine Aminotransferase",
                "href": "/mdr/bc/biomedicalconcepts/ALT",
            },
            {
                "code": "AST",
                "title": "Aspartate Aminotransferase",
                "href": "/mdr/bc/biomedicalconcepts/AST",
            },
        ]
    }

    def fake_get(url, headers=None, timeout=0):
        return DummyResp(200, payload)

    monkeypatch.setattr("requests.get", fake_get)
    concepts = fetch_biomedical_concepts_by_category("Liver Findings")
    assert {c["code"] for c in concepts} == {"ALT", "AST"}
    assert all(
        c["href"].startswith(
            "https://api.library.cdisc.org/api/cosmos/v2/mdr/bc/biomedicalconcepts/"
        )
        for c in concepts
    )


def test_helper_parses_hal_links(monkeypatch):
    """HAL _links with concept hrefs should be parsed correctly."""
    payload = {
        "_links": {
            "self": {"href": "/mdr/bc/biomedicalconcepts?category=Liver%20Findings"},
            "concepts": [
                {
                    "href": "/mdr/bc/biomedicalconcepts/ALT",
                    "title": "Alanine Aminotransferase",
                },
                {
                    "href": "/mdr/bc/biomedicalconcepts/AST",
                    "title": "Aspartate Aminotransferase",
                },
            ],
        }
    }

    def fake_get(url, headers=None, timeout=0):
        return DummyResp(200, payload)

    monkeypatch.setattr("requests.get", fake_get)
    concepts = fetch_biomedical_concepts_by_category("Liver Findings")
    codes = {c["code"] for c in concepts}
    assert codes == {"ALT", "AST"}
    # Titles preserved
    assert any(c["title"] == "Alanine Aminotransferase" for c in concepts)


def test_helper_handles_non_200(monkeypatch):
    """HTTP != 200 should yield empty list."""

    def fake_get(url, headers=None, timeout=0):
        return DummyResp(404, {}, text="Not Found")

    monkeypatch.setattr("requests.get", fake_get)
    concepts = fetch_biomedical_concepts_by_category("Liver Findings")
    assert concepts == []


def test_category_ui_endpoint_renders_links(monkeypatch):
    """UI endpoint should render internal concept detail links."""
    payload = {
        "items": [
            {
                "code": "ALT",
                "title": "Alanine Aminotransferase",
                "href": "/mdr/bc/biomedicalconcepts/ALT",
            },
            {
                "code": "AST",
                "title": "Aspartate Aminotransferase",
                "href": "/mdr/bc/biomedicalconcepts/AST",
            },
        ]
    }

    def fake_get(url, headers=None, timeout=0):
        return DummyResp(200, payload)

    monkeypatch.setattr("requests.get", fake_get)
    resp = client.get("/ui/concept_categories/view", params={"name": "Liver Findings"})
    assert resp.status_code == 200
    text = resp.text
    # Internal links to concept detail page
    assert "/ui/concepts/ALT" in text
    assert "/ui/concepts/AST" in text
    # Category name present
    assert "Biomedical Concepts in Category: Liver Findings" in text


def test_category_ui_endpoint_empty(monkeypatch):
    """Empty concepts should show fallback message."""

    def fake_get(url, headers=None, timeout=0):
        return DummyResp(200, {"items": []})

    monkeypatch.setattr("requests.get", fake_get)
    resp = client.get("/ui/concept_categories/view", params={"name": "Liver Findings"})
    assert resp.status_code == 200
    assert "No concepts found for this category." in resp.text
