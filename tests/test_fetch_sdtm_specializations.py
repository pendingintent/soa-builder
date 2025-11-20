import json
from unittest.mock import Mock, patch

from soa_builder.web.app import _sdtm_specializations_cache, fetch_sdtm_specializations


def test_fetch_sdtm_specializations_env_override(monkeypatch):
    # Reset cache
    _sdtm_specializations_cache.update(data=None, fetched_at=0)
    # Prepare override JSON for full-list mode
    override = [
        {
            "title": "Spec A",
            "href": "/mdr/specializations/sdtm/datasetspecializations/AAA",
        },
        {
            "datasetSpecializationName": "Spec B",
            "datasetSpecializationId": "BBB",
        },
    ]
    monkeypatch.setenv("CDISC_SDTM_SPECIALIZATIONS_JSON", json.dumps(override))

    result = fetch_sdtm_specializations(force=True)

    assert len(result) == 2
    titles = sorted(p["title"] for p in result)
    assert titles == ["Spec A", "Spec B"]
    # hrefs should be absolute
    for p in result:
        assert p["href"].startswith("https://api.library.cdisc.org/")


def test_fetch_sdtm_specializations_remote_no_code(monkeypatch):
    # Reset cache and ensure no override
    _sdtm_specializations_cache.update(data=None, fetched_at=0)
    monkeypatch.delenv("CDISC_SDTM_SPECIALIZATIONS_JSON", raising=False)
    monkeypatch.delenv("CDISC_SKIP_REMOTE", raising=False)

    fake_json = {
        "items": [
            {
                "title": "Remote Spec 1",
                "href": "/mdr/specializations/sdtm/datasetspecializations/R1",
            },
            {
                "title": "Remote Spec 2",
                "href": "/mdr/specializations/sdtm/datasetspecializations/R2",
            },
        ]
    }

    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps(fake_json)
    mock_resp.json.return_value = fake_json

    with patch("soa_builder.web.app.requests.get", return_value=mock_resp) as mock_get:
        result = fetch_sdtm_specializations(force=True)

    mock_get.assert_called_once()
    assert len(result) == 2
    titles = sorted(p["title"] for p in result)
    assert titles == ["Remote Spec 1", "Remote Spec 2"]


def test_fetch_sdtm_specializations_with_code_uses_generic_endpoint(monkeypatch):
    _sdtm_specializations_cache.update(data=None, fetched_at=0)
    monkeypatch.delenv("CDISC_SDTM_SPECIALIZATIONS_JSON", raising=False)

    hal_json = {
        "_links": {
            "datasetSpecializations": {
                "sdtm": [
                    {
                        "href": "/mdr/specializations/sdtm/datasetspecializations/AAA",
                        "title": "Spec A",
                        "type": "SDTM Dataset Specialization",
                    },
                    {
                        "href": "/mdr/specializations/sdtm/datasetspecializations/BBB",
                        "title": "Spec B",
                        "type": "SDTM Dataset Specialization",
                    },
                ]
            }
        }
    }

    mock_resp = Mock()
    mock_resp.status_code = 200
    mock_resp.text = json.dumps(hal_json)
    mock_resp.json.return_value = hal_json

    with patch("soa_builder.web.app.requests.get", return_value=mock_resp) as mock_get:
        result = fetch_sdtm_specializations(force=True, code="C105585")

    # Ensure it called the generic endpoint with biomedicalconcept
    called_url = mock_get.call_args[0][0]
    assert "mdr/specializations/datasetspecializations" in called_url
    assert "biomedicalconcept=C105585" in called_url

    assert len(result) == 2
    titles = sorted(p["title"] for p in result)
    assert titles == ["Spec A", "Spec B"]
