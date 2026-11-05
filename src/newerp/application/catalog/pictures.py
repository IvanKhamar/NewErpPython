"""Uploading pictures to the storefront's image folder (the form fields keep their path, like the old admin).

The browser's file name and type are not used at all: the type comes from the file's first bytes, and the server
makes the name. So an uploaded script can't be stored under a name the web server would run.
"""

import logging
import secrets
from typing import Protocol

from newerp.application.common import Dto
from newerp.domain.catalog import image_path, picture_extension
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")

MAX_PICTURE_BYTES = 5 * 1024 * 1024
# What a picture is for, and its folder below catalog/. Nothing from the request goes into the path but this choice.
PICTURE_FOLDERS = {"products": "products", "categories": "categories", "banners": "banners", "vouchers": "vouchers"}


class PictureDto(Dto):
    path: str


class PictureFolder(Protocol):
    @property
    def enabled(self) -> bool: ...

    async def save(self, path: str, content: bytes) -> None: ...


class PictureService:
    def __init__(self, folder: PictureFolder) -> None:
        self._folder = folder

    async def upload(self, kind: str, content: bytes, user_id: int) -> PictureDto:
        folder = PICTURE_FOLDERS.get(kind)
        if folder is None:
            raise DomainError("Невідомий вид картинки.")
        if not self._folder.enabled:
            raise DomainError("Папку картинок вітрини не налаштовано: NEWERP_PICTURES__IMAGE_DIR.")
        if len(content) > MAX_PICTURE_BYTES:
            raise DomainError(f"Картинка завелика: не більше {MAX_PICTURE_BYTES // (1024 * 1024)} МБ.")
        extension = picture_extension(content)
        if extension is None:
            raise DomainError("Це не картинка: приймаються лише JPEG, PNG, WebP і GIF.")
        path = image_path(f"catalog/{folder}/{secrets.token_hex(8)}.{extension}")
        await self._folder.save(path, content)
        audit.info("picture uploaded: %s, %s bytes, by user %s", path, len(content), user_id)
        return PictureDto(path=path)
