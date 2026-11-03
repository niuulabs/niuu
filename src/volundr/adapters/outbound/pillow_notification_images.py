"""Decoding notification images and building their thumbnails with Pillow.

The fetched bytes must decode (with Pillow restricted to the accepted formats) as
PNG, JPEG, GIF or WebP within ``MAX_IMAGE_BYTES`` and ``MAX_IMAGE_PIXELS``. Forge
stores the original bytes unchanged with the decoded type, and reports the upright
size (EXIF orientation applied). The thumbnail is the first frame, upright, with its
long side bounded: PNG when the image has transparency, JPEG otherwise.
"""

from __future__ import annotations

import io

from PIL import Image, ImageOps

from niuu.domain.notifications import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    NOTIFICATION_IMAGE_MIME_TYPES,
)
from volundr.domain.notification_ports import NotificationImageProcessor
from volundr.domain.notifications import (
    FetchedImage,
    NotificationImageError,
    ProcessedNotificationImage,
)

Image.init()
#: Pillow format names for the accepted types; decoding is restricted to these.
PILLOW_FORMATS: tuple[str, ...] = tuple(
    sorted(name for name, mime in Image.MIME.items() if mime in NOTIFICATION_IMAGE_MIME_TYPES)
)
_ALPHA_MODES = frozenset({"RGBA", "LA", "PA"})
_PNG = ("PNG", "image/png")
_JPEG = ("JPEG", "image/jpeg")


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in _ALPHA_MODES or (image.mode == "P" and "transparency" in image.info)


class PillowNotificationImageProcessor(NotificationImageProcessor):
    """Decodes an image and builds its thumbnail (see the module docstring)."""

    def __init__(
        self,
        *,
        thumbnail_max_side: int,
        thumbnail_jpeg_quality: int,
        max_bytes: int = MAX_IMAGE_BYTES,
        max_pixels: int = MAX_IMAGE_PIXELS,
    ) -> None:
        if thumbnail_max_side < 1:
            raise ValueError("thumbnail_max_side must be at least 1")
        self._thumbnail_side = thumbnail_max_side
        self._jpeg_quality = thumbnail_jpeg_quality
        self._max_bytes = max_bytes
        self._max_pixels = max_pixels

    def process(self, fetched: FetchedImage) -> ProcessedNotificationImage:
        if len(fetched.content) > self._max_bytes:
            raise NotificationImageError(f"the image is larger than {self._max_bytes} bytes")
        try:
            with Image.open(io.BytesIO(fetched.content), formats=PILLOW_FORMATS) as source:
                mime = Image.MIME.get(source.format or "")
                if mime not in NOTIFICATION_IMAGE_MIME_TYPES:
                    raise NotificationImageError(f"{source.format} images are not accepted")
                width, height = source.size
                if width * height > self._max_pixels:
                    raise NotificationImageError(
                        f"the image is {width}x{height}, more than {self._max_pixels} pixels"
                    )
                upright = ImageOps.exif_transpose(source)
        except NotificationImageError:
            raise
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise NotificationImageError(
                f"the bytes are not a decodable {'/'.join(PILLOW_FORMATS)} image"
            ) from exc
        thumbnail, (thumbnail_format, thumbnail_mime) = self._thumbnail(upright)
        return ProcessedNotificationImage(
            mime=str(mime),
            width=upright.width,
            height=upright.height,
            content=fetched.content,
            thumbnail_mime=thumbnail_mime,
            thumbnail_width=thumbnail.width,
            thumbnail_height=thumbnail.height,
            thumbnail=self._encode(thumbnail, thumbnail_format),
        )

    def _thumbnail(self, image: Image.Image) -> tuple[Image.Image, tuple[str, str]]:
        alpha = _has_alpha(image)
        thumbnail = image.convert("RGBA" if alpha else "RGB")
        thumbnail.thumbnail((self._thumbnail_side, self._thumbnail_side))
        return thumbnail, _PNG if alpha else _JPEG

    def _encode(self, image: Image.Image, image_format: str) -> bytes:
        buffer = io.BytesIO()
        if image_format == _JPEG[0]:
            image.save(buffer, format=image_format, quality=self._jpeg_quality, optimize=True)
        else:
            image.save(buffer, format=image_format, optimize=True)
        return buffer.getvalue()
