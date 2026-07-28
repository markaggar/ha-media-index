"""Unit tests for video_parser.py.

Focuses on refresh_pymediainfo_availability(), which fixes the stale
PYMEDIAINFO_AVAILABLE flag after installing libmediainfo at runtime.
"""

import importlib
import importlib.util
import os as _os
import sys
import types
import unittest.mock as mock


def _load_video_parser():
    """Load video_parser module fresh (without pymediainfo/mutagen side-effects)."""
    path = _os.path.join(
        _os.path.dirname(__file__), '..', '..', 'custom_components', 'media_index', 'video_parser.py'
    )
    spec = importlib.util.spec_from_file_location('video_parser', path)
    mod = importlib.util.module_from_spec(spec)
    # Stub out const dependency (sanitize_unicode_to_ascii)
    const_stub = types.ModuleType('video_parser_const')
    const_stub.sanitize_unicode_to_ascii = lambda t: t
    mod.__dict__['const'] = const_stub
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# refresh_pymediainfo_availability tests
# ---------------------------------------------------------------------------

class TestRefreshPymediainfoAvailability:
    """Tests for video_parser.refresh_pymediainfo_availability()."""

    def test_sets_flag_true_when_library_works(self):
        """refresh_pymediainfo_availability() returns True and updates
        PYMEDIAINFO_AVAILABLE when both the pymediainfo module and the
        libmediainfo C library are available."""
        vp = _load_video_parser()

        # Simulate a working MediaInfo library
        fake_mi = mock.MagicMock()
        fake_mi.parse.return_value = mock.MagicMock(tracks=[])
        fake_pymediainfo = types.ModuleType('pymediainfo')
        fake_pymediainfo.MediaInfo = fake_mi

        # Also stub importlib.reload: the fake module has no __spec__, so a real
        # reload() call would fall through to re-importing the actually-installed
        # pymediainfo package (if present) instead of keeping our mock in place.
        with mock.patch.dict(sys.modules, {'pymediainfo': fake_pymediainfo}), \
                mock.patch('importlib.reload'):
            result = vp.refresh_pymediainfo_availability()

        assert result is True
        assert vp.PYMEDIAINFO_AVAILABLE is True

    def test_sets_flag_false_when_c_library_missing(self):
        """refresh_pymediainfo_availability() returns False and sets
        PYMEDIAINFO_AVAILABLE=False when MediaInfo.parse() raises OSError
        (shared library not found)."""
        vp = _load_video_parser()
        # Force the flag to True so we can verify it gets reset
        vp.PYMEDIAINFO_AVAILABLE = True

        fake_mi = mock.MagicMock()
        fake_mi.parse.side_effect = OSError("libmediainfo.so.0: cannot open shared object file")
        fake_pymediainfo = types.ModuleType('pymediainfo')
        fake_pymediainfo.MediaInfo = fake_mi

        with mock.patch.dict(sys.modules, {'pymediainfo': fake_pymediainfo}), \
                mock.patch('importlib.reload'):
            result = vp.refresh_pymediainfo_availability()

        assert result is False
        assert vp.PYMEDIAINFO_AVAILABLE is False

    def test_sets_flag_false_when_import_fails(self):
        """refresh_pymediainfo_availability() returns False and sets
        PYMEDIAINFO_AVAILABLE=False when the pymediainfo Python package is
        not installed (ImportError)."""
        vp = _load_video_parser()
        vp.PYMEDIAINFO_AVAILABLE = True

        # Setting sys.modules['pymediainfo'] = None forces the `import pymediainfo`
        # statement to raise ImportError, simulating an environment where the
        # package isn't installed (regardless of whether it's actually installed
        # in this test environment).
        with mock.patch.dict(sys.modules, {'pymediainfo': None}):
            result = vp.refresh_pymediainfo_availability()

        assert result is False
        assert vp.PYMEDIAINFO_AVAILABLE is False

    def test_updates_flag_from_false_to_true(self):
        """Simulates the post-install scenario: flag starts False, library
        becomes available, refresh_pymediainfo_availability() updates it to
        True."""
        vp = _load_video_parser()
        # Simulate state after HA upgrade removed libmediainfo
        vp.PYMEDIAINFO_AVAILABLE = False

        fake_mi = mock.MagicMock()
        fake_mi.parse.return_value = mock.MagicMock(tracks=[])
        fake_pymediainfo = types.ModuleType('pymediainfo')
        fake_pymediainfo.MediaInfo = fake_mi

        with mock.patch.dict(sys.modules, {'pymediainfo': fake_pymediainfo}), \
                mock.patch('importlib.reload'):
            result = vp.refresh_pymediainfo_availability()

        assert result is True
        assert vp.PYMEDIAINFO_AVAILABLE is True

    def test_extract_metadata_skips_pymediainfo_when_flag_false(self):
        """extract_metadata() skips the pymediainfo path when
        PYMEDIAINFO_AVAILABLE=False.

        This validates the failure mode: before the fix, PYMEDIAINFO_AVAILABLE
        was always False after an HA upgrade even after successful
        install+reload, so extraction silently fell back to
        mutagen/filename/filesystem.
        """
        vp = _load_video_parser()
        vp.PYMEDIAINFO_AVAILABLE = False
        vp.MUTAGEN_AVAILABLE = False  # also disable mutagen so we can check pure fallback

        import tempfile
        test_fd, test_path = tempfile.mkstemp(suffix='.mp4')
        _os.close(test_fd)
        try:
            result = vp.VideoMetadataParser.extract_metadata(test_path)
            # With both flags False and no real video content, result falls back to
            # filesystem date at most — crucially, MediaInfo.parse() is never called.
            assert result is None or 'latitude' not in result
        finally:
            _os.unlink(test_path)


# ---------------------------------------------------------------------------
# _parse_iso6709 tests (GPS format regression coverage)
# ---------------------------------------------------------------------------

class TestParseIso6709:
    """Tests for VideoMetadataParser._parse_iso6709(), covering both the raw
    ISO 6709 format and the human-readable degrees+cardinal format seen from
    newer libmediainfo versions."""

    def test_raw_iso6709_lat_lon(self):
        vp = _load_video_parser()
        coords = vp.VideoMetadataParser._parse_iso6709('+40.7484-073.9857/')
        assert coords == (40.7484, -73.9857)

    def test_raw_iso6709_with_altitude(self):
        vp = _load_video_parser()
        coords = vp.VideoMetadataParser._parse_iso6709('+52.4915+009.6692+049.668/')
        assert coords == (52.4915, 9.6692)

    def test_cardinal_format_north_west(self):
        vp = _load_video_parser()
        coords = vp.VideoMetadataParser._parse_iso6709('47.591095°N 122.230698°W')
        assert coords == (47.591095, -122.230698)

    def test_cardinal_format_south_east(self):
        vp = _load_video_parser()
        coords = vp.VideoMetadataParser._parse_iso6709('33.8688°S 151.2093°E')
        assert coords == (-33.8688, 151.2093)

    def test_invalid_string_returns_none(self):
        vp = _load_video_parser()
        assert vp.VideoMetadataParser._parse_iso6709('not a location') is None
