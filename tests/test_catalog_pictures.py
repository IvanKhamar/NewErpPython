"""Picture upload (75): the type comes from the content, the name from the server, and only inside the folder."""

import logging

import pytest

from newerp.application.catalog.pictures import MAX_PICTURE_BYTES
from newerp.config import get_settings
from newerp.domain.catalog import picture_extension
from tests.seed import MANAGER, STOREKEEPER

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
HTMX = {"HX-Request": "true"}


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings().pictures, "image_dir", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize(
    ("content", "extension"),
    [
        (JPEG, "jpg"),
        (b"\x89PNG\r\n\x1a\n...", "png"),
        (b"GIF89a...", "gif"),
        (b"RIFF1234WEBPVP8 ", "webp"),
        (b"<?php system($_GET['c']);", None),
        (b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>", None),  # scripts run inside an SVG
        (b"MZ\x90\x00", None),
        (b"RIFF1234WAVEfmt ", None),
        (b"", None),
    ],
)
def test_the_type_comes_from_the_first_bytes(content, extension):
    assert picture_extension(content) == extension


def test_the_server_makes_the_name_whatever_the_browser_says(api, folder):
    client = api(MANAGER)
    # The name and the type sent by the browser lie; neither is used.
    response = client.post("/catalog/pictures/products", files={"file": ("../../shell.php", JPEG, "text/x-php")})
    assert response.status_code == 201, response.text
    path = response.json()["path"]
    assert path.startswith("catalog/products/") and path.endswith(".jpg") and "shell" not in path
    assert (folder / path).read_bytes() == JPEG
    assert [p.name for p in folder.rglob("*.php")] == []

    second = client.post("/catalog/pictures/products", files={"file": ("a.jpg", JPEG, "image/jpeg")}).json()["path"]
    assert second != path  # the same file twice never overwrites


def test_refused_uploads_leave_nothing(api, folder):
    client = api(MANAGER)
    refused = [
        ("products", b"<?php echo 1;"),
        ("products", JPEG + b"0" * MAX_PICTURE_BYTES),
        ("../../etc", JPEG),
        ("everything", JPEG),
    ]
    for kind, content in refused:
        response = client.post(f"/catalog/pictures/{kind}", files={"file": ("a.jpg", content, "image/jpeg")})
        assert response.status_code in (400, 404), (kind, response.status_code)
    assert list(folder.rglob("*.*")) == []


def test_it_is_off_without_the_folder_and_closed_to_other_roles(api, monkeypatch):
    monkeypatch.setattr(get_settings().pictures, "image_dir", "")
    file = {"file": ("a.jpg", JPEG, "image/jpeg")}
    response = api(MANAGER).post("/catalog/pictures/products", files=file)
    assert response.status_code == 400
    assert api(STOREKEEPER).post("/catalog/pictures/products", files=file).status_code == 403


def test_the_web_field_gets_the_new_path(api, folder):
    client = api(MANAGER)
    file = {"file": ("a.jpg", JPEG, "image/jpeg")}
    single = client.post("/app/catalog/pictures?kind=banners&field=image_image", files=file, headers=HTMX)
    assert 'name="image_image" value="catalog/banners/' in single.text

    bound = client.post("/app/catalog/pictures?kind=vouchers&field=image&form_id=th-3", files=file, headers=HTMX)
    assert 'form="th-3"' in bound.text

    many = client.post("/app/catalog/pictures?kind=products&field=images&multi=true", files=file,
                       data={"images": "catalog/old.jpg"}, headers=HTMX)  # fmt: skip
    assert "catalog/old.jpg\ncatalog/products/" in many.text and "<textarea" in many.text


def test_a_refused_web_upload_keeps_the_field(api, folder):
    client = api(MANAGER)
    file = {"file": ("a.jpg", b"not a picture", "image/jpeg")}
    response = client.post("/app/catalog/pictures?kind=products&field=image", files=file, headers=HTMX)
    assert response.headers["HX-Reswap"] == "beforeend"  # the message is added, the typed path stays
    assert 'class="error"' in response.text
    missing = client.post("/app/catalog/pictures?kind=products&field=image", headers=HTMX)
    assert 'class="error"' in missing.text


def test_the_forms_offer_the_upload(api):
    client = api(MANAGER)
    assert (
        'hx-post="/app/catalog/pictures?kind=banners&amp;field=image_image"' in client.get("/app/catalog/banners").text
    )
    assert "kind=vouchers" in client.get("/app/marketing/vouchers/themes").text


def test_audit_lines_really_are_written(api, folder, caplog):
    # No caplog.at_level here on purpose: the level must come from the app. It used to drop every audit.info line.
    audit = logging.getLogger("newerp.audit")
    assert audit.isEnabledFor(logging.INFO) and audit.handlers
    api(MANAGER).post("/catalog/pictures/banners", files={"file": ("a.jpg", JPEG, "image/jpeg")})
    assert "picture uploaded: catalog/banners/" in caplog.text
