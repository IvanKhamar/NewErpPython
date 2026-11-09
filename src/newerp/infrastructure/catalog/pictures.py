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

    async def delete(self, path: str) -> None:
        """Removes a picture; one that is already gone is fine. A path that leaves the folder raises ValueError."""
        await asyncio.to_thread(self._remove, path)

    def _inside(self, path: str) -> Path:
        assert self._root is not None  # the service checks enabled first
        root = self._root.resolve()
        target = (root / path).resolve()
        if not target.is_relative_to(root):  # paths come from the service or the database; this is a second lock
            raise ValueError("A picture path outside the image folder.")
        return target

    def _remove(self, path: str) -> None:
        self._inside(path).unlink(missing_ok=True)

    def _write(self, path: str, content: bytes) -> None:
        target = self._inside(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with target.open("xb") as file:  # "x": fails if the file exists, so nothing is ever overwritten
                file.write(content)
        except FileExistsError:
            raise
        except OSError:
            target.unlink(missing_ok=True)  # no half-written picture
            raise
