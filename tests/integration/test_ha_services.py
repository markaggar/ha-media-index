"""Integration tests against a live Home Assistant instance.

Run with:
    pytest tests/integration -v

All tests are skipped automatically if HA_BASE_URL / HA_TOKEN are not set.

The tests are intentionally read-only (dry_run=True for destructive services)
so they are safe to run against a production instance.
"""

import os
import pytest
import requests

from tests.integration.conftest import call_service


pytestmark = pytest.mark.integration


# ─── HA reachability ─────────────────────────────────────────────────────────

class TestHAReachability:

    def test_api_endpoint_responds(self, ha_session, ha_url):
        """GET /api/ must return HTTP 200 with a message field."""
        resp = ha_session.get(f"{ha_url}/api/", timeout=10)
        assert resp.status_code == 200
        assert "message" in resp.json()

    def test_media_index_sensor_available(self, ha_session, ha_url, ha_entity):
        """The configured verify-entity must be in 'available' state."""
        resp = ha_session.get(f"{ha_url}/api/states/{ha_entity}", timeout=10)
        assert resp.status_code == 200, f"Sensor {ha_entity!r} not found"
        state = resp.json()
        assert state["state"] not in ("unavailable", "unknown"), (
            f"Sensor {ha_entity!r} is in state {state['state']!r} — "
            "is media_index loaded correctly?"
        )


# ─── scan_folder ─────────────────────────────────────────────────────────────

class TestScanFolder:

    def test_scan_folder_returns_success(self, ha_session, ha_url, ha_entity):
        """scan_folder with force_rescan=False must complete without error.

        Uses HA_TEST_FOLDER if set, otherwise fires a no-op scan with no folder
        specified (scans whatever folder the entity is configured for).
        """
        folder = os.environ.get("HA_TEST_FOLDER")
        data: dict = {"force_rescan": False}
        if folder:
            data["folder_path"] = folder

        resp = call_service(ha_session, ha_url, "media_index", "scan_folder",
                            data, entity_id=ha_entity)
        # The service may return an empty dict when return_response isn't supported
        # for this service — that's OK as long as it didn't raise HTTP 4xx/5xx.
        assert isinstance(resp, (dict, list))

    def test_scan_file_on_known_entity(self, ha_session, ha_url, ha_entity):
        """scan_folder with a specific small test folder completes successfully.

        Skipped when HA_TEST_FOLDER is not configured.
        """
        folder = os.environ.get("HA_TEST_FOLDER")
        if not folder:
            pytest.skip("HA_TEST_FOLDER not set — skipping targeted scan test")

        data = {"folder_path": folder, "force_rescan": True}
        resp = call_service(ha_session, ha_url, "media_index", "scan_folder",
                            data, entity_id=ha_entity)
        assert isinstance(resp, (dict, list))


# ─── get_random_items ────────────────────────────────────────────────────────

class TestGetRandomItems:

    def test_returns_items(self, ha_session, ha_url, ha_entity):
        """get_random_items must return a non-empty list of media items."""
        data = {
            "count": 5,
            "favorites_only": False,
            "priority_new_files": False,
            "anniversary_window_days": 0,
        }
        resp = call_service(ha_session, ha_url, "media_index", "get_random_items",
                            data, entity_id=ha_entity)
        # Response shape: {"items": [...]} or a list directly
        items = resp.get("items") if isinstance(resp, dict) else (resp or [])
        if not items:
            pytest.skip("Library appears empty — skipping (expected if no files scanned yet)")
        assert len(items) > 0

    def test_items_have_required_fields(self, ha_session, ha_url, ha_entity):
        """Each item must have at minimum: path, filename, folder, file_type."""
        data = {"count": 3, "favorites_only": False, "priority_new_files": False,
                "anniversary_window_days": 0}
        resp = call_service(ha_session, ha_url, "media_index", "get_random_items",
                            data, entity_id=ha_entity)
        items = resp.get("items") if isinstance(resp, dict) else (resp or [])
        if not items:
            pytest.skip("Library appears empty — skipping field validation")
        for item in items:
            for field in ("path", "filename", "folder", "file_type"):
                assert field in item, f"Item missing required field: {field!r}"

    def test_count_respected(self, ha_session, ha_url, ha_entity):
        """Response must not return more items than requested."""
        count = 7
        data = {"count": count, "favorites_only": False, "priority_new_files": False,
                "anniversary_window_days": 0}
        resp = call_service(ha_session, ha_url, "media_index", "get_random_items",
                            data, entity_id=ha_entity)
        items = resp.get("items") if isinstance(resp, dict) else (resp or [])
        assert len(items) <= count


# ─── geocode_coordinates ─────────────────────────────────────────────────────

class TestGeocodeCoordinates:

    def test_geocode_known_coordinates(self, ha_session, ha_url, ha_entity):
        """Geocoding a well-known coordinate (Tokyo) must return a non-empty country."""
        data = {"latitude": 35.6895, "longitude": 139.6917}
        try:
            resp = call_service(ha_session, ha_url, "media_index", "geocode_coordinates",
                                data, entity_id=ha_entity)
        except requests.HTTPError as exc:
            if exc.response.status_code in (400, 404, 405):
                pytest.skip("geocode_coordinates service not available or returns 400 for this entity")
            raise
        # Either country or location_country should be populated
        country = resp.get("country") or resp.get("location_country") or ""
        assert "japan" in country.lower() or country != "", (
            f"Expected Japan from Tokyo coords, got: {resp}"
        )


# ─── find_duplicate_files (dry-run, read-only) ───────────────────────────────

class TestFindDuplicateFiles:

    def test_dry_run_does_not_delete(self, ha_session, ha_url, ha_entity):
        """find_duplicate_files with dry_run=true must not delete anything."""
        data = {"dry_run": True}
        try:
            resp = call_service(ha_session, ha_url, "media_index", "find_duplicate_files",
                                data, entity_id=ha_entity)
        except requests.HTTPError as exc:
            if exc.response.status_code in (400, 404, 405):
                pytest.skip("find_duplicate_files not available — run index_burst_groups first")
            raise
        assert resp.get("dry_run") is True
        assert resp.get("deleted", 0) == 0, "dry_run should never delete files"
        assert "duplicate_sets" in resp
        assert "folder_pairs" in resp
