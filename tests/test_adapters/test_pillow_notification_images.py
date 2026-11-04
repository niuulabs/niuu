"""PillowNotificationImageProcessor: decode, limits, upright size and thumbnails."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from volundr.adapters.outbound.pillow_notification_images import (
    PILLOW_FORMATS,
    PillowNotificationImageProcessor,
)
from volundr.domain.notifications import FetchedImage, NotificationImageError

EXIF_ORIENTATION = 0x0112
ROTATE_90_CW = 6


def _encode(image: Image.Image, fmt: str, **params) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **params)
    return buffer.getvalue()


def _processor(**overrides) -> PillowNotificationImageProcessor:
    values = {"thumbnail_max_side": 320, "thumbnail_jpeg_quality": 85}
    values.update(overrides)
    return PillowNotificationImageProcessor(**values)


def _fetched(content: bytes, content_type: str = "image/png") -> FetchedImage:
    return FetchedImage(content=content, content_type=content_type)


def _decode(content: bytes) -> Image.Image:
    return Image.open(io.BytesIO(content))


def test_only_the_accepted_formats_are_decoded():
    assert PILLOW_FORMATS == ("GIF", "JPEG", "PNG", "WEBP")


def test_opaque_png_keeps_the_original_and_gets_a_jpeg_thumbnail():
    content = _encode(Image.new("RGB", (1200, 800), (10, 120, 200)), "PNG")
    processed = _processor().process(_fetched(content))
    assert (processed.mime, processed.width, processed.height) == ("image/png", 1200, 800)
    assert processed.content == content
    assert processed.thumbnail_mime == "image/jpeg"
    assert (processed.thumbnail_width, processed.thumbnail_height) == (320, 213)
    thumb = _decode(processed.thumbnail)
    assert (thumb.format, thumb.size) == ("JPEG", (320, 213))


def test_transparent_png_gets_a_png_thumbnail():
    content = _encode(Image.new("RGBA", (400, 1000), (0, 0, 0, 0)), "PNG")
    processed = _processor().process(_fetched(content))
    assert processed.thumbnail_mime == "image/png"
    assert (processed.thumbnail_width, processed.thumbnail_height) == (128, 320)
    assert _decode(processed.thumbnail).mode == "RGBA"


def test_palette_gif_with_transparency_and_small_images_are_not_upscaled():
    image = Image.new("P", (50, 40))
    content = _encode(image, "GIF", transparency=0)
    processed = _processor().process(_fetched(content, "image/gif"))
    assert (processed.mime, processed.thumbnail_mime) == ("image/gif", "image/png")
    assert (processed.thumbnail_width, processed.thumbnail_height) == (50, 40)


def test_webp_and_jpeg_are_accepted_and_the_declared_type_is_not_trusted():
    webp = _encode(Image.new("RGB", (64, 64), "white"), "WEBP")
    assert _processor().process(_fetched(webp, "image/png")).mime == "image/webp"
    jpeg = _encode(Image.new("L", (64, 32)), "JPEG")
    processed = _processor(thumbnail_max_side=16).process(_fetched(jpeg, "image/png"))
    assert processed.mime == "image/jpeg"
    assert (processed.thumbnail_width, processed.thumbnail_height) == (16, 8)


def test_exif_orientation_is_applied_to_the_reported_size_and_thumbnail():
    image = Image.new("RGB", (600, 300), "red")
    exif = image.getexif()
    exif[EXIF_ORIENTATION] = ROTATE_90_CW
    content = _encode(image, "JPEG", exif=exif.tobytes())
    processed = _processor().process(_fetched(content, "image/jpeg"))
    assert (processed.width, processed.height) == (300, 600)
    assert (processed.thumbnail_width, processed.thumbnail_height) == (160, 320)
    assert processed.content == content


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"definitely not an image", "not a decodable"),
        (_encode(Image.new("RGB", (8, 8)), "BMP"), "not a decodable"),
        (_encode(Image.new("RGB", (8, 8)), "TIFF"), "not a decodable"),
        (_encode(Image.new("RGB", (200, 200)), "PNG")[:60], "not a decodable"),
    ],
)
def test_undecodable_or_unaccepted_bytes_are_refused(content, message):
    with pytest.raises(NotificationImageError, match=message):
        _processor().process(_fetched(content))


def test_pixel_and_byte_limits_are_enforced():
    content = _encode(Image.new("RGB", (100, 100)), "PNG")
    with pytest.raises(NotificationImageError, match="100x100, more than 9999 pixels"):
        _processor(max_pixels=9_999).process(_fetched(content))
    with pytest.raises(NotificationImageError, match="larger than 10 bytes"):
        _processor(max_bytes=10).process(_fetched(content))


def test_decompression_bombs_are_refused(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    content = _encode(Image.new("RGB", (100, 100)), "PNG")
    with pytest.raises(NotificationImageError, match="not a decodable"):
        _processor().process(_fetched(content))


def test_a_non_positive_thumbnail_side_is_refused():
    with pytest.raises(ValueError, match="thumbnail_max_side"):
        _processor(thumbnail_max_side=0)
