"""The storefront's image folder, when the ERP runs where it can write it (a shared volume)."""

import asyncio
from pathlib import Path


class LocalCatalogPictures:
    def __init__(self, image_dir: str) -> None:
        self._root = Path(image_dir) if image_dir.strip() else None

    @property
    def enabled(self) -> bool:
        return self._root is not None and self._root.is_dir()

    async def save(self, path: str, content: bytes) -> None:
        # File work blocks, so it runs in a thread (like Task.Run), and the event loop keeps serving other requests.
        await asyncio.to_thread(self._write, path, content)

    def _write(self, path: str, content: bytes) -> None:
        assert self._root is not None  # the service checks enabled first
        root = self._root.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root):  # the service makes the path; this is a second lock
            raise ValueError("A picture path outside the image folder.")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as file:  # "x": fails if the file exists, so nothing is ever overwritten
                file.write(content)
        except FileExistsError:
            raise
        except OSError:
            target.unlink(missing_ok=True)  # no half-written picture
            raise
