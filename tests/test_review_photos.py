"""Item 83: customers' photos in reviews (dd_review_image), moderated one by one like in the old admin."""

import asyncio
from datetime import datetime

import pytest

from newerp.application.catalog.feedback import FeedbackService, is_review_photo
from newerp.application.common import NotFoundError
from newerp.config import get_settings
from newerp.infrastructure.catalog.pictures import LocalCatalogPictures
from tests import seed
from tests.seed import CATEGORY_PRODUCT_ID, MANAGER, STOREKEEPER

HTMX = {"HX-Request": "true"}
NOW = datetime(2026, 11, 9, 10)


# ═══ Which files may be deleted ═══


@pytest.mark.parametrize(
    ("path", "deleted"),
    [
        ("review_photos/a1b2.jpg", True),
        ("review_photos/2026/a1b2.jpg", True),
        ("catalog/products/a1b2.jpg", False),  # a row may point to a product picture: never delete that
        ("review_photos/../catalog/x.jpg", False),
        ("review_photos\\..\\catalog\\x.jpg", False),
        ("review_photos/", False),
        ("/etc/passwd", False),
    ],
)
def test_only_files_of_the_review_photo_folder_are_deleted(path, deleted):
    assert is_review_photo(path) is deleted


class FakeStore:
    def __init__(self, paths):
        self.paths = paths

    async def delete_review(self, review_id):
        return self.paths if review_id == 1 else None

    async def delete_review_image(self, review_id, image_id):
        return self.paths[0] if (review_id, image_id) == (1, 10) else None

    async def set_review_image_status(self, review_id, image_id, status):
        return (review_id, image_id) == (1, 10)


class FakeFolder:
    def __init__(self, enabled=True, fails=False):
        self.enabled = enabled
        self.fails = fails
        self.deleted = []

    async def delete(self, path):
        if self.fails:
            raise PermissionError(path)
        self.deleted.append(path)


class FixedClock:
    def now(self):
        return NOW


def test_deleting_a_review_deletes_its_photo_files_but_not_shared_pictures():
    folder = FakeFolder()
    service = FeedbackService(FakeStore(["review_photos/a.jpg", "catalog/x.jpg"]), FixedClock(), folder)

    asyncio.run(service.delete_review(1, user_id=1))

    assert folder.deleted == ["review_photos/a.jpg"]


def test_a_file_that_cannot_be_deleted_does_not_fail_the_request():
    service = FeedbackService(FakeStore(["review_photos/a.jpg"]), FixedClock(), FakeFolder(fails=True))

    asyncio.run(service.delete_review_image(1, 10, user_id=1))  # the row is gone; the file only leaves a warning


def test_without_the_picture_folder_only_the_rows_go():
    folder = FakeFolder(enabled=False)
    service = FeedbackService(FakeStore(["review_photos/a.jpg"]), FixedClock(), folder)

    asyncio.run(service.delete_review_image(1, 10, user_id=1))

    assert folder.deleted == []


def test_unknown_photo_or_review_is_not_found():
    service = FeedbackService(FakeStore([]), FixedClock(), FakeFolder())

    with pytest.raises(NotFoundError):
        asyncio.run(service.delete_review_image(1, 11, user_id=1))
    with pytest.raises(NotFoundError):
        asyncio.run(service.delete_review(2, user_id=1))
    asyncio.run(service.delete_review(1, user_id=1))  # a review without photos is still found


def test_local_folder_refuses_a_path_outside_it(tmp_path):
    folder = LocalCatalogPictures(str(tmp_path))

    with pytest.raises(ValueError):
        asyncio.run(folder.delete("../outside.jpg"))
    asyncio.run(folder.delete("review_photos/missing.jpg"))  # already gone is fine


# ═══ API and web page against MySQL ═══


@pytest.fixture(scope="module")
def reviews(db):
    for review_id, published in ((1, False), (2, True), (3, False)):
        seed.insert(db, "dd_review", review_id=review_id, product_id=CATEGORY_PRODUCT_ID, customer_id=0,
                    author="Ірина", text="Гарна", rating=5, status=published,
                    date_added=NOW, date_modified=NOW)  # fmt: skip
    for image_id, review_id, path, order, status in (
        (10, 1, "review_photos/r1-b.jpg", 1, 0),
        (11, 1, "review_photos/r1-a.jpg", 0, 1),
        (20, 2, "review_photos/r2.jpg", 0, 2),
        (30, 3, "review_photos/r3.jpg", 0, 0),
        (31, 3, "catalog/products/shared.jpg", 1, 1),
    ):
        seed.insert(db, "dd_review_image", image_id=image_id, review_id=review_id, image=path, sort_order=order,
                    status=status, date_added=NOW)  # fmt: skip
    db.commit()


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings().pictures, "image_dir", str(tmp_path))
    for path in ("review_photos/r1-b.jpg", "review_photos/r3.jpg", "catalog/products/shared.jpg"):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(b"jpeg")
    return tmp_path


def photos(client, review_id):
    [review] = [r for r in client.get("/catalog/reviews?state=all").json()["items"] if r["id"] == review_id]
    return [(p["id"], p["path"], p["status"]) for p in review["images"]]


def test_reviews_list_their_photos_in_the_storefront_order(api, reviews):
    client = api(MANAGER)

    assert photos(client, 1) == [(11, "review_photos/r1-a.jpg", "published"), (10, "review_photos/r1-b.jpg", "pending")]
    assert photos(client, 2) == [(20, "review_photos/r2.jpg", "rejected")]


def test_a_photo_is_published_rejected_and_deleted_with_its_file(api, reviews, folder):
    client = api(MANAGER)

    assert client.put("/catalog/reviews/1/images/10", json={"status": "published"}).status_code == 204
    assert ("review_photos/r1-b.jpg", "published") in [(p, s) for _, p, s in photos(client, 1)]
    assert client.put("/catalog/reviews/1/images/10", json={"status": "hidden"}).status_code == 400
    assert client.put("/catalog/reviews/2/images/10", json={"status": "rejected"}).status_code == 404  # other review

    assert client.delete("/catalog/reviews/1/images/10").status_code == 204
    assert not (folder / "review_photos/r1-b.jpg").exists()
    assert [i for i, _, _ in photos(client, 1)] == [11]
    assert client.delete("/catalog/reviews/1/images/10").status_code == 404


def test_deleting_a_review_takes_its_photos_but_never_a_shared_picture(api, reviews, folder, db):
    assert api(MANAGER).delete("/catalog/reviews/3").status_code == 204

    with db.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM dd_review_image WHERE review_id = 3")
        assert cursor.fetchone()[0] == 0
    assert not (folder / "review_photos/r3.jpg").exists()
    assert (folder / "catalog/products/shared.jpg").exists()


def test_photos_are_closed_to_other_roles(api, reviews):
    assert api(STOREKEEPER).put("/catalog/reviews/1/images/11", json={"status": "rejected"}).status_code == 403
    assert api(STOREKEEPER).delete("/catalog/reviews/1/images/11").status_code == 403


def test_web_page_shows_photos_from_the_storefront_and_moderates_them(api, reviews, monkeypatch):
    monkeypatch.setattr(get_settings().customers, "store_url", "https://shop.test/")
    client = api(MANAGER)

    page = client.get("/app/catalog/content?tab=reviews&state=all")
    assert 'src="https://shop.test/image/review_photos/r2.jpg"' in page.text

    rejected = client.post("/app/catalog/content/reviews/1/images/11/rejected", data={"state": "all"}, headers=HTMX)
    assert "Збережено." in rejected.text
    assert (11, "review_photos/r1-a.jpg", "rejected") in photos(client, 1)

    missing = client.post("/app/catalog/content/reviews/1/images/99/delete", data={"state": "all"}, headers=HTMX)
    assert 'class="error"' in missing.text


def test_web_page_lists_paths_without_an_https_storefront(api, reviews, monkeypatch):
    monkeypatch.setattr(get_settings().customers, "store_url", "http://shop.test/")

    page = api(MANAGER).get("/app/catalog/content?tab=reviews&state=all")

    assert "<code>review_photos/r2.jpg</code>" in page.text
    assert 'src="http://shop.test' not in page.text  # the CSP would block it anyway
