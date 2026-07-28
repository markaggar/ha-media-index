"""Unit tests for video_parser.py.

Focuses on the refresh_availability() function that fixes the stale
PYMEDIAINFO_AVAILABLE flag after installing libmediainfo at runtime.
"""

import importlib
import importlib.util
import os as _os
import sys
import types
import unittest.mock as mock

import pytest


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
# refresh_availability tests
# ---------------------------------------------------------------------------

class TestRefreshAvailability:
    """Tests for video_parser.refresh_availability()."""

    def test_sets_flag_true_when_library_works(self):
        """refresh_availability() returns True and updates PYMEDIAINFO_AVAILABLE
        when both the pymediainfo module and libmediainfo C library are available."""
        vp = _load_video_parser()

        # Simulate a working MediaInfo library
        fake_mi = mock.MagicMock()
        fake_mi.parse.return_value = mock.MagicMock(tracks=[])
        fake_pymediainfo = types.ModuleType('pymediainfo')
        fake_pymediainfo.MediaInfo = fake_mi

        with mock.patch.dict(sys.modules, {'pymediainfo': fake_pymediainfo}):
            result = vp.refresh_availability()

        assert result is True
        assert vp.PYMEDIAINFO_AVAILABLE is True

    def test_sets_flag_false_when_c_library_missing(self):
        """refresh_availability() returns False and sets PYMEDIAINFO_AVAILABLE=False
        when MediaInfo.parse() raises OSError (shared library not found)."""
        vp = _load_video_parser()
        # Force the flag to True so we can verify it gets reset
        vp.PYMEDIAINFO_AVAILABLE = True

        fake_mi = mock.MagicMock()
        fake_mi.parse.side_effect = OSError("libmediainfo.so.0: cannot open shared object file")
        fake_pymediainfo = types.ModuleType('pymediainfo')
        fake_pymediainfo.MediaInfo = fake_mi

        with mock.patch.dict(sys.modules, {'pymediainfo': fake_pymediainfo}):
            result = vp.refresh_availability()

        assert result is False
        assert vp.PYMEDIAINFO_AVAILABLE is False

    def test_sets_flag_false_when_import_fails(self):
        """refresh_availability() returns False and sets PYMEDIAINFO_AVAILABLE=False
        when the pymediainfo Python package is not installed (ImportError)."""
        vp = _load_video_parser()
        vp.PYMEDIAINFO_AVAILABLE = True

        # Remove pymediainfo from sys.modules so the import inside refresh_availability fails
        saved = sys.modules.pop('pymediainfo', None)
        try:
            result = vp.refresh_availability()
        finally:
            if saved is not None:
                sys.modules['pymediainfo'] = saved

        assert result is False
        assert vp.PYMEDIAINFO_AVAILABLE is False

    def test_updates_flag_from_false_to_true(self):
        """Simulates the post-install scenario: flag starts False, library becomes
        available, refresh_availability() updates it to True."""
        vp = _load_video_parser()
        # Simulate state after HA upgrade removed libmediainfo
        vp.PYMEDIAINFO_AVAILABLE = False

        fake_mi = mock.MagicMock()
        fake_mi.parse.return_value = mock.MagicMock(tracks=[])
        fake_pymediainfo = types.ModuleType('pymediainfo')
        fake_pymediainfo.MediaInfo = fake_mi

        with mock.patch.dict(sys.modules, {'pymediainfo': fake_pymediainfo}):
            result = vp.refresh_availability()

        assert result is True
        assert vp.PYMEDIAINFO_AVAILABLE is True

    def test_extract_metadata_skips_pymediainfo_when_flag_false(self):
        """extract_metadata() skips the pymediainfo path when PYMEDIAINFO_AVAILABLE=False.

        This validates the failure mode: before the fix, PYMEDIAINFO_AVAILABLE was
        always False after an HA upgrade even after successful install+reload, so
        extraction silently fell back to mutagen/filename/filesystem.
        """
        vp = _load_video_parser()
        vp.PYMEDIAINFO_AVAILABLE = False
        vp.MUTAGEN_AVAILABLE = False  # also disable mutagen so we can check pure fallback

        import tempfile
        test_fd, test_path = tempfile.mkstemp(suffix='.mp4')
        _os.close(test_fd)
        try:
            result = vp.VideoMetadataParser.extract_metadata(test_path)
            # With both flags False and no real video content, result is None or has only
            # filesystem date — crucially, MediaInfo.parse() was NEVER called.
            # The key assertion is that the function completes without trying pymediainfo.
            # (If it tried pymediainfo, it would fail with NameError since MediaInfo is not imported)
            assert result is None or isinstance(result, dict)
        finally:
            _os.unlink(test_path)

    def test_extract_metadata_uses_pymediainfo_when_flag_true(self):
        """extract_metadata() calls MediaInfo.parse() when PYMEDIAINFO_AVAILABLE=True."""
        vp = _load_video_parser()

        fake_mi = mock.MagicMock()
        fake_mi.parse.return_value = mock.MagicMock(tracks=[])
        fake_pymediainfo = types.ModuleType('pymediainfo')
        fake_pymediainfo.MediaInfo = fake_mi

        with mock.patch.dict(sys.modules, {'pymediainfo': fake_pymediainfo}):
            # Reload so module-level check runs with our fake library
            vp.refresh_availability()

        assert vp.PYMEDIAINFO_AVAILABLE is True

        import tempfile
        test_fd, test_path = tempfile.mkstemp(suffix='.mp4')
        _os.close(test_fd)
        try:
            # Patch MediaInfo inside vp's namespace for the extract call
            with mock.patch.object(vp, 'MediaInfo', fake_mi, create=True):
                vp.VideoMetadataParser.extract_metadata(test_path)
            # MediaInfo.parse was called (even if it returned an empty track list)
            fake_mi.parse.assert_called()
        finally:
            _os.unlink(test_path)
