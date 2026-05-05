"""Shared fixtures for integration tests (require a live HA instance).

Tests are skipped automatically if HA_BASE_URL or HA_TOKEN are not set,
so this suite is safe to run in CI — missing env vars → all tests skipped,
not failed.

Required env vars (already defined in LOCAL_SETUP.md / PowerShell profile):
  HA_BASE_URL      e.g. http://10.0.0.62:8123
  HA_TOKEN         Long-lived access token
  HA_VERIFY_ENTITY sensor entity_id to use as default target
                   e.g. sensor.media_index_media_photo_photolibrary_total_files

Optional:
  HA_TEST_FOLDER   Folder path on the HA server to use for scan tests
                   (a small folder with known files is ideal)
"""

import os
import pytest
import requests


def _require_env(*names: str) -> None:
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.skip(f"Integration test skipped — env vars not set: {', '.join(missing)}")


@pytest.fixture(scope="session")
def ha_url() -> str:
    _require_env("HA_BASE_URL", "HA_TOKEN")
    return os.environ["HA_BASE_URL"].rstrip("/")


@pytest.fixture(scope="session")
def ha_headers() -> dict:
    _require_env("HA_BASE_URL", "HA_TOKEN")
    return {
        "Authorization": f"Bearer {os.environ['HA_TOKEN']}",
        "Content-Type": "application/json",
    }


@pytest.fixture(scope="session")
def ha_entity() -> str:
    _require_env("HA_VERIFY_ENTITY")
    return os.environ["HA_VERIFY_ENTITY"]


@pytest.fixture(scope="session")
def ha_session(ha_url, ha_headers) -> requests.Session:
    """Requests session pre-configured for the HA REST API."""
    s = requests.Session()
    s.headers.update(ha_headers)
    s.verify = False        # HA dev instances often use self-signed certs
    return s


def call_service(session: requests.Session, ha_url: str, domain: str, service: str,
                 data: dict, *, entity_id: str | None = None) -> dict:
    """Helper: POST to HA service endpoint and return the service_response payload.

    HA wraps service responses in {'changed_states': [...], 'service_response': {...}}.
    This helper unwraps that envelope so callers see the service payload directly.
    """
    payload: dict = dict(data)
    if entity_id:
        payload.setdefault("target", {})["entity_id"] = entity_id
    resp = session.post(
        f"{ha_url}/api/services/{domain}/{service}",
        json=payload,
        params={"return_response": "true"},
        timeout=120,
    )
    resp.raise_for_status()
    try:
        body = resp.json()
    except Exception:
        return {}
    # Unwrap HA's service-response envelope when present
    if isinstance(body, dict) and "service_response" in body:
        return body["service_response"]
    return body
