"""Unit tests for CacheManager.

Uses a real SQLite database in a pytest tmp_path (not :memory:) to match
production behaviour including the schema migration path.

Tests are async (pytest-asyncio with asyncio_mode = auto in pytest.ini).
"""

import asyncio
import os
import pytest
import pytest_asyncio

import importlib.util, os as _os
def _load(name):
    path = _os.path.join(_os.path.dirname(__file__), '..', '..', 'custom_components', 'media_index', f'{name}.py')
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
CacheManager = _load('cache_manager').CacheManager


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def cache(tmp_path):
    """Return an initialised CacheManager backed by a temp SQLite file."""
    db_path = str(tmp_path / "test_media_index.db")
    mgr = CacheManager(db_path)
    ok = await mgr.async_setup()
    assert ok, "CacheManager.async_setup() returned False"
    yield mgr
    await mgr.close()


# ─── helpers ─────────────────────────────────────────────────────────────────

def _file_data(
    path: str,
    *,
    folder: str = "/media/photo/Test",
    file_size: int = 1_000_000,
    modified_time: str = "2023-06-23T10:00:00",
    burst_id: str | None = None,
) -> dict:
    filename = os.path.basename(path)
    return {
        "path": path,
        "filename": filename,
        "folder": folder,
        "file_type": "image",
        "file_size": file_size,
        "modified_time": modified_time,
        "created_time": modified_time,
        "width": 4000,
        "height": 3000,
        "orientation": "normal",
        "is_favorited": 0,
        "rating": 0,
    }


def _exif_data(
    *,
    date_taken: int = 1_687_514_000,
    latitude: float | None = None,
    longitude: float | None = None,
    burst_id: str | None = None,
    is_favorited: int = 0,
    rating: int | None = None,
) -> dict:
    return {
        "date_taken": date_taken,
        "latitude": latitude,
        "longitude": longitude,
        "is_favorited": is_favorited,
        "rating": rating,
        "burst_id": burst_id,
    }


# ─── schema / setup ──────────────────────────────────────────────────────────

class TestSchema:

    async def test_setup_creates_tables(self, cache):
        """async_setup() must create media_files, exif_data, and geocode_cache tables."""
        async with cache._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cur:
            tables = {r[0] async for r in cur}
        assert "media_files" in tables
        assert "exif_data" in tables
        assert "geocode_cache" in tables

    async def test_setup_idempotent(self, tmp_path):
        """Calling async_setup() twice on the same DB must not raise or corrupt."""
        db_path = str(tmp_path / "idempotent.db")
        mgr = CacheManager(db_path)
        assert await mgr.async_setup()
        assert await mgr.async_setup()
        await mgr.close()


# ─── add_file / get_file_by_path ─────────────────────────────────────────────

class TestAddFile:

    async def test_add_new_file_returns_id(self, cache):
        fid = await cache.add_file(_file_data("/media/photo/Test/img001.jpg"))
        assert isinstance(fid, int)
        assert fid > 0

    async def test_get_file_by_path_round_trip(self, cache):
        data = _file_data("/media/photo/Test/img002.jpg")
        fid = await cache.add_file(data)
        retrieved = await cache.get_file_by_path("/media/photo/Test/img002.jpg")
        assert retrieved is not None
        assert retrieved["id"] == fid
        assert retrieved["filename"] == "img002.jpg"
        assert retrieved["folder"] == "/media/photo/Test"

    async def test_add_same_path_is_upsert(self, cache):
        """Re-adding the same path must update, not duplicate."""
        data = _file_data("/media/photo/Test/img003.jpg", file_size=111)
        fid1 = await cache.add_file(data)
        data2 = _file_data("/media/photo/Test/img003.jpg", file_size=222)
        fid2 = await cache.add_file(data2)
        assert fid1 == fid2, "upsert should preserve the original file ID"
        total = await cache.get_total_files()
        assert total == 1

    async def test_get_total_files_empty(self, cache):
        assert await cache.get_total_files() == 0

    async def test_get_total_files_after_adds(self, cache):
        for i in range(3):
            await cache.add_file(_file_data(f"/media/photo/Test/img{i:03d}.jpg"))
        assert await cache.get_total_files() == 3


# ─── add_exif_data ────────────────────────────────────────────────────────────

class TestAddExifData:

    async def test_add_exif_round_trip(self, cache):
        fid = await cache.add_file(_file_data("/media/photo/Test/exif_test.jpg"))
        exif = _exif_data(latitude=35.711, longitude=139.796, is_favorited=1, rating=4)
        await cache.add_exif_data(fid, exif)

        async with cache._db.execute(
            "SELECT latitude, longitude, is_favorited, rating FROM exif_data WHERE file_id=?",
            (fid,),
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert abs(row[0] - 35.711) < 0.001
        assert abs(row[1] - 139.796) < 0.001
        assert row[2] == 1   # is_favorited
        assert row[3] == 4   # rating

    async def test_rescan_preserves_geocoded_location(self, cache):
        """Re-calling add_exif_data must not wipe an already-geocoded city."""
        fid = await cache.add_file(_file_data("/media/photo/Test/geocoded.jpg"))
        # First scan: lat/lon provided, location geocoded externally
        await cache.add_exif_data(fid, _exif_data(latitude=35.711, longitude=139.796))
        # Simulate geocoding result being written directly
        await cache._db.execute(
            "UPDATE exif_data SET location_city='Tokyo', location_country='Japan' WHERE file_id=?",
            (fid,),
        )
        await cache._db.commit()
        # Second scan: same lat/lon, should preserve geocoded values
        await cache.add_exif_data(fid, _exif_data(latitude=35.711, longitude=139.796))

        async with cache._db.execute(
            "SELECT location_city FROM exif_data WHERE file_id=?", (fid,)
        ) as cur:
            row = await cur.fetchone()
        assert row[0] == "Tokyo", "geocoded city must survive a rescan"


# ─── find_duplicate_files ─────────────────────────────────────────────────────

class TestFindDuplicateFiles:
    """Tests for the folder-pair-aware duplicate detection logic."""

    async def _add_burst_file(
        self,
        cache: CacheManager,
        path: str,
        folder: str,
        burst_id: str,
        file_size: int = 2_000_000,
        date_taken: int = 1_687_514_000,
        is_favorited: int = 0,
    ) -> int:
        """Insert a file + exif row with a burst_id (prerequisite for duplicate detection).

        burst_id is owned by index_burst_groups, not by add_exif_data, so we
        write it directly to the database after the initial insert.
        """
        fid = await cache.add_file(
            _file_data(path, folder=folder, file_size=file_size)
        )
        await cache.add_exif_data(
            fid,
            _exif_data(date_taken=date_taken, is_favorited=is_favorited),
        )
        # Simulate what index_burst_groups does: set burst_id directly.
        # add_exif_data deliberately never writes burst_id — it preserves
        # the existing value set by index_burst_groups.
        await cache._db.execute(
            "UPDATE exif_data SET burst_id = ?, is_favorited = ? WHERE file_id = ?",
            (burst_id, is_favorited, fid),
        )
        await cache._db.commit()
        # find_duplicate_files matches on (burst_id, file_size, date_taken, width, height)
        # width/height come from media_files.  Default _file_data() sets 4000x3000.
        return fid

    async def test_no_duplicates_returns_empty(self, cache):
        folder = "/media/photo/Test"
        await self._add_burst_file(cache, f"{folder}/img_a.jpg", folder, "burst_001")
        await self._add_burst_file(cache, f"{folder}/img_b.jpg", folder, "burst_002",
                                   file_size=999_999)  # different size → not a duplicate
        result = await cache.find_duplicate_files()
        assert result["sets"] == []
        assert result["folder_pairs"] == []

    async def test_exact_duplicate_detected(self, cache):
        """Two files with same (burst_id, file_size, date_taken, width, height) are duplicates."""
        folder_a = "/media/photo/Folder_A"
        folder_b = "/media/photo/Folder_B"
        await self._add_burst_file(cache, f"{folder_a}/original.jpg", folder_a, "burst_dup",
                                   is_favorited=1)
        await self._add_burst_file(cache, f"{folder_b}/copy.jpg", folder_b, "burst_dup")

        result = await cache.find_duplicate_files()
        assert len(result["sets"]) == 1, "one duplicate set expected"
        dup_set = result["sets"][0]
        # The favorited file should be the keeper
        assert dup_set["keeper"]["path"] == f"{folder_a}/original.jpg"
        assert len(dup_set["duplicates"]) == 1
        assert dup_set["duplicates"][0]["path"] == f"{folder_b}/copy.jpg"

    async def test_folder_pair_majority_vote(self, cache):
        """When multiple sets span the same two folders, the majority-vote folder wins."""
        folder_a = "/media/photo/A"
        folder_b = "/media/photo/B"
        # 3 duplicate pairs, all originals in folder_a
        for i in range(3):
            await self._add_burst_file(
                cache, f"{folder_a}/orig_{i}.jpg", folder_a, f"burst_{i}", is_favorited=1
            )
            await self._add_burst_file(
                cache, f"{folder_b}/copy_{i}.jpg", folder_b, f"burst_{i}"
            )

        result = await cache.find_duplicate_files()
        assert len(result["folder_pairs"]) == 1
        pair = result["folder_pairs"][0]
        assert pair["keeper_folder"] == folder_a
        assert pair["duplicate_folder"] == folder_b
        assert pair["duplicate_sets"] == 3

    async def test_prefer_folder_overrides_vote(self, cache):
        """prefer_folder forces the specified folder to be keeper regardless of vote."""
        folder_a = "/media/photo/A"
        folder_b = "/media/photo/B"
        # folder_b has the favorited file (would normally win the vote)
        await self._add_burst_file(cache, f"{folder_a}/copy.jpg", folder_a, "burst_x")
        await self._add_burst_file(
            cache, f"{folder_b}/orig.jpg", folder_b, "burst_x", is_favorited=1
        )

        result = await cache.find_duplicate_files(prefer_folder=folder_a)
        assert result["sets"][0]["keeper"]["folder"] == folder_a

    async def test_folder_scope_filter(self, cache):
        """When folder= is supplied, only files under that prefix are searched."""
        folder_in  = "/media/photo/Scoped"
        folder_out = "/media/photo/Other"
        # Duplicate inside scoped folder
        await self._add_burst_file(cache, f"{folder_in}/a.jpg", folder_in, "burst_s1")
        await self._add_burst_file(cache, f"{folder_in}/b.jpg", folder_in, "burst_s1")
        # Duplicate in unrelated folder — should NOT appear
        await self._add_burst_file(cache, f"{folder_out}/a.jpg", folder_out, "burst_s2")
        await self._add_burst_file(cache, f"{folder_out}/b.jpg", folder_out, "burst_s2")

        result = await cache.find_duplicate_files(folder=folder_in)
        paths_in_results = [
            f["path"]
            for s in result["sets"]
            for f in [s["keeper"]] + s["duplicates"]
        ]
        assert all(p.startswith(folder_in) for p in paths_in_results), (
            "folder= filter must exclude files outside the specified prefix"
        )

    async def test_files_without_burst_id_ignored(self, cache):
        """Files with burst_id=None must never appear in duplicate results."""
        folder = "/media/photo/NoBurst"
        fid = await cache.add_file(_file_data(f"{folder}/no_burst.jpg", folder=folder))
        await cache.add_exif_data(fid, _exif_data())   # burst_id = None by default
        result = await cache.find_duplicate_files()
        assert result["sets"] == []
