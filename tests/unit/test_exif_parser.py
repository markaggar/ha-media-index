"""Unit tests for ExifParser.

Covers the bug-prone areas found during development:
  - GPS DMS extraction: both rational-tuple (piexif/ARM64 Pillow) and float formats
  - Rating extraction: EXIF tag 0x4746, XMP attribute/element forms, MicrosoftPhoto RatingPercent
  - EXIF Rating=0 must NOT block the XMP fallback
  - image dimensions come from img.size, not ExifImageWidth/Height
"""

import io
import struct
import pytest
from PIL import Image
import piexif

import importlib.util, os as _os
def _load(name):
    path = _os.path.join(_os.path.dirname(__file__), '..', '..', 'custom_components', 'media_index', f'{name}.py')
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
ExifParser = _load('exif_parser').ExifParser


# ─── JPEG helpers ────────────────────────────────────────────────────────────

def _make_jpeg(width: int = 10, height: int = 8) -> bytes:
    """Return raw bytes for a minimal valid JPEG (no EXIF)."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=0).save(buf, format="JPEG")
    return buf.getvalue()


def _inject_exif(jpeg_bytes: bytes, exif_dict: dict) -> bytes:
    """Inject a piexif-format exif_dict into JPEG bytes (replaces any existing EXIF)."""
    exif_bytes = piexif.dump(exif_dict)
    out = io.BytesIO()
    piexif.insert(exif_bytes, jpeg_bytes, out)
    return out.getvalue()


def _inject_xmp(jpeg_bytes: bytes, xmp_text: str) -> bytes:
    """Insert an XMP APP1 segment into JPEG bytes, just before the SOS marker.

    APP markers must appear before SOS in a valid JPEG.  Both PIL (img.info['xmp'])
    and _read_jpeg_xmp() scan only the header region (before SOS), so inserting
    after SOS would cause both to miss the XMP block.
    """
    ns = b"http://ns.adobe.com/xap/1.0/\x00"
    payload = ns + xmp_text.encode("utf-8")
    seg_len = len(payload) + 2          # length field includes itself
    app1 = b"\xff\xe1" + struct.pack(">H", seg_len) + payload
    sos_pos = jpeg_bytes.find(b"\xff\xda")  # SOS marker
    if sos_pos == -1:
        return jpeg_bytes[:-2] + app1 + b"\xff\xd9"  # fallback: before EOI
    return jpeg_bytes[:sos_pos] + app1 + jpeg_bytes[sos_pos:]


def _base_exif() -> dict:
    """Minimal IFD0 dict.  Ensures img.getexif() is non-empty on read-back."""
    return {"0th": {piexif.ImageIFD.Make: b"TestCam"}}


# ─── GPS: _convert_to_degrees ─────────────────────────────────────────────────

class TestConvertToDegrees:
    """Direct unit tests for the private helper method."""

    def test_rational_tuple_format(self):
        """ARM64 / piexif format: each DMS element is a (numerator, denominator) tuple.

        This is the exact format that triggered the GPS=null bug on HA (ARM64 Linux).
        35° 42′ 41.04″ N  →  35 + 42/60 + 41.04/3600  ≈  35.71140
        """
        val = ExifParser._convert_to_degrees(((35, 1), (42, 1), (4104, 100)))
        assert abs(val - 35.71140) < 0.0001

    def test_float_format(self):
        """Desktop Pillow format: elements are already converted to float."""
        val = ExifParser._convert_to_degrees((35.0, 42.0, 41.04))
        assert abs(val - 35.71140) < 0.0001

    def test_all_zeros(self):
        val = ExifParser._convert_to_degrees(((0, 1), (0, 1), (0, 1)))
        assert val == 0.0

    def test_mixed_types(self):
        """Degree as int, minutes as float, seconds as rational — defensive coverage."""
        val = ExifParser._convert_to_degrees((35, 42.0, (4104, 100)))
        assert abs(val - 35.71140) < 0.0001

    def test_london(self):
        """51° 30′ 26.10″ N  ≈  51.50725"""
        val = ExifParser._convert_to_degrees(((51, 1), (30, 1), (2610, 100)))
        assert abs(val - 51.50725) < 0.0001


# ─── GPS: full extract_exif path ─────────────────────────────────────────────

class TestGPSExtraction:
    """End-to-end GPS extraction using synthetic JPEG files written with piexif.

    piexif always writes rational tuples — these are read back as floats by
    x86-64 Pillow but as raw tuples on ARM64.  The direct tests above cover
    the ARM64 path; these tests verify the complete extract_exif() pipeline.
    """

    def _make_gps_jpeg(
        self,
        tmp_path,
        name: str,
        lat_dms,
        lon_dms,
        lat_ref: bytes = b"N",
        lon_ref: bytes = b"E",
    ) -> str:
        exif_dict = _base_exif()
        exif_dict["GPS"] = {
            piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
            piexif.GPSIFD.GPSLatitudeRef: lat_ref,
            piexif.GPSIFD.GPSLatitude: lat_dms,
            piexif.GPSIFD.GPSLongitudeRef: lon_ref,
            piexif.GPSIFD.GPSLongitude: lon_dms,
        }
        path = tmp_path / name
        path.write_bytes(_inject_exif(_make_jpeg(), exif_dict))
        return str(path)

    def test_north_east(self, tmp_path):
        """Tokyo: N/E → positive lat and lon."""
        path = self._make_gps_jpeg(
            tmp_path, "ne.jpg",
            lat_dms=((35, 1), (42, 1), (4104, 100)),
            lon_dms=((139, 1), (47, 1), (4668, 100)),
        )
        result = ExifParser.extract_exif(path)
        assert result is not None
        assert abs(result["latitude"]  - 35.71140) < 0.001
        assert abs(result["longitude"] - 139.79630) < 0.001

    def test_south_hemisphere(self, tmp_path):
        """Sydney: GPSLatitudeRef=S → negative latitude."""
        path = self._make_gps_jpeg(
            tmp_path, "south.jpg",
            lat_dms=((33, 1), (51, 1), (5400, 100)),
            lon_dms=((151, 1), (12, 1), (3600, 100)),
            lat_ref=b"S",
        )
        result = ExifParser.extract_exif(path)
        assert result is not None
        assert result["latitude"] < 0
        assert result["longitude"] > 0

    def test_west_longitude(self, tmp_path):
        """New York: GPSLongitudeRef=W → negative longitude."""
        path = self._make_gps_jpeg(
            tmp_path, "west.jpg",
            lat_dms=((40, 1), (42, 1), (4600, 100)),
            lon_dms=((74, 1), (0, 1), (2100, 100)),
            lon_ref=b"W",
        )
        result = ExifParser.extract_exif(path)
        assert result is not None
        assert result["latitude"] > 0
        assert result["longitude"] < 0

    def test_no_gps(self, tmp_path):
        """File with no GPS IFD → lat/lon are None."""
        path = tmp_path / "no_gps.jpg"
        path.write_bytes(_inject_exif(_make_jpeg(), _base_exif()))
        result = ExifParser.extract_exif(str(path))
        if result is not None:
            assert result["latitude"] is None
            assert result["longitude"] is None


# ─── Ratings ─────────────────────────────────────────────────────────────────

class TestRatingExtraction:
    """Tests for EXIF 0x4746 and XMP/MicrosoftPhoto rating extraction."""

    # PIL allows writing arbitrary EXIF tags by tag ID, bypassing piexif validation.
    def _jpeg_with_exif_rating(self, tmp_path, name: str, rating: int) -> str:
        img = Image.new("RGB", (10, 8), color=0)
        exif = img.getexif()
        exif[0x0112] = 1       # Orientation — ensures getexif() is non-empty on readback
        exif[0x4746] = rating  # Rating (Microsoft extension tag)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        path = tmp_path / name
        path.write_bytes(buf.getvalue())
        return str(path)

    def _jpeg_with_xmp_rating(self, tmp_path, name: str, xmp_text: str) -> str:
        """Minimal EXIF (so extract_exif doesn't bail) + XMP APP1 with rating."""
        jpeg = _inject_xmp(_inject_exif(_make_jpeg(), _base_exif()), xmp_text)
        path = tmp_path / name
        path.write_bytes(jpeg)
        return str(path)

    # EXIF tag tests ---------------------------------------------------------

    def test_exif_rating_5(self, tmp_path):
        path = self._jpeg_with_exif_rating(tmp_path, "r5.jpg", 5)
        result = ExifParser.extract_exif(path)
        assert result is not None
        assert result["rating"] == 5

    def test_exif_rating_1(self, tmp_path):
        path = self._jpeg_with_exif_rating(tmp_path, "r1.jpg", 1)
        result = ExifParser.extract_exif(path)
        assert result is not None
        assert result["rating"] == 1

    def test_exif_rating_zero_falls_through_to_xmp(self, tmp_path):
        """EXIF Rating=0 must be treated as 'absent' so the XMP fallback runs."""
        xmp = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
            ' xmp:Rating="4"/></rdf:RDF></x:xmpmeta>'
        )
        img = Image.new("RGB", (10, 8), color=0)
        exif = img.getexif()
        exif[0x0112] = 1
        exif[0x4746] = 0   # Rating = 0 (Lightroom "unrated")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif.tobytes())
        jpeg = _inject_xmp(buf.getvalue(), xmp)
        path = tmp_path / "exif0_xmp4.jpg"
        path.write_bytes(jpeg)

        result = ExifParser.extract_exif(str(path))
        assert result is not None
        assert result["rating"] == 4, (
            "XMP Rating=4 should win when EXIF Rating=0 (EXIF=0 means 'unrated')"
        )

    # XMP attribute form -----------------------------------------------------

    def test_xmp_rating_attribute_form(self, tmp_path):
        """xmp:Rating="5" written as an XML attribute."""
        xmp = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/"'
            ' xmp:Rating="5"/></rdf:RDF></x:xmpmeta>'
        )
        path = self._jpeg_with_xmp_rating(tmp_path, "xmp_attr.jpg", xmp)
        result = ExifParser.extract_exif(path)
        assert result is not None
        assert result["rating"] == 5

    def test_xmp_rating_element_form(self, tmp_path):
        """<xmp:Rating>3</xmp:Rating> written as an XML element."""
        xmp = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
            "<xmp:Rating>3</xmp:Rating>"
            "</rdf:Description></rdf:RDF></x:xmpmeta>"
        )
        path = self._jpeg_with_xmp_rating(tmp_path, "xmp_elem.jpg", xmp)
        result = ExifParser.extract_exif(path)
        assert result is not None
        assert result["rating"] == 3

    # MicrosoftPhoto RatingPercent -------------------------------------------
    # Written by Windows Explorer, older Lightroom-on-Windows exports, etc.
    # Mapping: pct >= 99 → 5★, >= 75 → 4★, >= 50 → 3★, >= 25 → 2★, >= 1 → 1★

    @pytest.mark.parametrize("pct,expected_stars", [
        (99, 5),
        (75, 4),
        (50, 3),
        (25, 2),
        (1,  1),
    ])
    def test_microsoft_rating_percent(self, pct, expected_stars, tmp_path):
        xmp = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about=""'
            ' xmlns:MicrosoftPhoto="http://ns.microsoft.com/photo/1.0/"'
            f' MicrosoftPhoto:RatingPercent="{pct}"/>'
            "</rdf:RDF></x:xmpmeta>"
        )
        path = self._jpeg_with_xmp_rating(tmp_path, f"winpct{pct}.jpg", xmp)
        result = ExifParser.extract_exif(path)
        assert result is not None
        assert result["rating"] == expected_stars, (
            f"RatingPercent={pct} should map to {expected_stars} stars"
        )

    def test_no_rating_returns_none(self, tmp_path):
        """File with no rating anywhere → rating is None."""
        path = tmp_path / "no_rating.jpg"
        path.write_bytes(_inject_exif(_make_jpeg(), _base_exif()))
        result = ExifParser.extract_exif(str(path))
        if result is not None:
            assert result["rating"] is None


# ─── Dimensions ──────────────────────────────────────────────────────────────

class TestDimensions:

    def test_uses_img_size_not_exif_fields(self, tmp_path):
        """Width/height must come from img.size, not ExifImageWidth/PixelXDimension.

        ExifImageWidth (0xA002) is the original sensor dimension and can differ
        from the stored JPEG pixel size (e.g. Canon R5: 5464 sensor width vs
        5435 exported JPEG width).  ExifImageHeight is sometimes absent entirely.
        """
        buf = io.BytesIO()
        Image.new("RGB", (30, 20), color=0).save(buf, format="JPEG")
        exif_dict = {
            "0th": {piexif.ImageIFD.Make: b"TestCam"},
            "Exif": {
                piexif.ExifIFD.PixelXDimension: 999,   # wrong on purpose
                piexif.ExifIFD.PixelYDimension: 888,
            },
        }
        jpeg = _inject_exif(buf.getvalue(), exif_dict)
        path = tmp_path / "dims.jpg"
        path.write_bytes(jpeg)

        result = ExifParser.extract_exif(str(path))
        assert result is not None
        assert result["width"]  == 30, "width should come from img.size, not PixelXDimension"
        assert result["height"] == 20, "height should come from img.size, not PixelYDimension"

    def test_non_image_file_returns_none(self, tmp_path):
        """Non-JPEG file (wrong extension) must return None without raising."""
        path = tmp_path / "notanimage.txt"
        path.write_text("hello")
        result = ExifParser.extract_exif(str(path))
        assert result is None
