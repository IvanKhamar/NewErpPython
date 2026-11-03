"""Administration (items 63–65): store settings without secrets, OpenCart user groups, API users and keys."""

import gzip
import json
from datetime import datetime
from decimal import Decimal

import pytest

from newerp.config import get_settings
from newerp.infrastructure.admin_files import sql_literal
from tests.seed import ADMIN, MANAGER, insert

HTMX = {"HX-Request": "true"}
OWN_GROUP = 1  # the seed's users are in group 1


@pytest.fixture(scope="module", autouse=True)
def data(db):
    for key, value in (
        ("config_name", "Vidro"),
        ("config_email", "shop@example.com"),
        ("config_mail_smtp_password", "fake-smtp-password"),
        ("config_encryption", "fake-encryption-key"),
        ("config_google_analytics", "<script>ga()</script>"),
        ("config_file_ext_allowed", "jpg\npng"),
    ):
        insert(db, "dd_setting", store_id=0, code="config", key=key, value=value, serialized=False)
    full = {"access": ["user/user_permission", "catalog/product"], "modify": ["user/user_permission"]}
    insert(db, "dd_user_group", user_group_id=OWN_GROUP, name="Адміністратор", permission=json.dumps(full))
    insert(db, "dd_user_group", user_group_id=2, name="Менеджер", permission=json.dumps({"access": ["sale/order"]}))


def test_store_settings_hide_secrets_and_lock_code(api, db):
    admin = api(ADMIN)
    response = admin.get("/admin/settings")
    assert "fake-" not in response.text
    settings = {s["key"]: s for g in response.json() for s in g["settings"]}
    assert (settings["config_mail_smtp_password"]["kind"], settings["config_encryption"]["kind"]) == (
        "secret",
        "secret",
    )
    assert settings["config_google_analytics"]["kind"] == "locked"
    assert settings["config_file_ext_allowed"]["kind"] == "locked"

    assert admin.put("/admin/settings", json={"config_name": "Vidro Store"}).status_code == 200
    for change in (
        {"config_encryption": "x"},
        {"config_google_analytics": "<script>"},
        {"config_file_ext_allowed": "php"},
    ):
        assert admin.put("/admin/settings", json=change).status_code == 400
    assert api(MANAGER).get("/admin/settings").status_code == 403


def test_user_groups_keep_the_admins_own_access(api):
    admin = api(ADMIN)
    groups = {g["name"]: g for g in admin.get("/admin/user-groups").json()}
    own = groups["Адміністратор"]
    assert own["users"] >= 1 and "catalog/product" in own["access"]
    assert "sale/order" in admin.get("/admin/user-groups/routes").json()

    locked_out = own | {"modify": []}
    refused = admin.put(f"/admin/user-groups/{OWN_GROUP}", json=locked_out)
    assert refused.status_code == 400 and "user/user_permission" in refused.json()["detail"]

    created = admin.post("/admin/user-groups", json={"name": "Склад", "access": ["stock/item", " "], "modify": []})
    assert created.status_code == 201
    created = created.json()
    assert created["access"] == ["stock/item"]
    assert admin.post("/admin/user-groups", json={"name": "X", "access": ["../../etc"]}).status_code == 400
    assert admin.delete(f"/admin/user-groups/{OWN_GROUP}").status_code == 400  # it has users
    assert admin.delete(f"/admin/user-groups/{created['id']}").status_code == 204


def test_api_keys_are_shown_once_and_end_sessions(api, db):
    admin = api(ADMIN)
    user = admin.post("/admin/api-users", json={"username": "Default", "ips": ["10.0.0.1", "10.0.0.1", " "]})
    assert user.status_code == 201
    user = user.json()
    assert (user["hasKey"], user["ips"]) == (False, ["10.0.0.1"])
    insert(db, "dd_api_session", api_id=user["id"], session_id="s1", ip="10.0.0.1", date_added="2026-11-01",
           date_modified="2026-11-01")  # fmt: skip

    key = admin.post(f"/admin/api-users/{user['id']}/key").json()["key"]
    assert len(key) == 256
    listed = next(u for u in admin.get("/admin/api-users").json() if u["id"] == user["id"])
    assert (listed["hasKey"], listed["sessions"]) == (True, 0)  # the old sessions ended
    assert key not in admin.get("/admin/api-users").text  # never shown again
    with db.cursor() as cursor:
        cursor.execute("SELECT `key` FROM dd_api WHERE api_id = %s", (user["id"],))
        assert cursor.fetchone() == (key,)  # stored as OpenCart compares it

    assert admin.post("/admin/api-users", json={"username": "Default"}).status_code == 409
    assert admin.post("/admin/api-users", json={"username": "X", "ips": ["not an ip"]}).status_code == 400
    assert admin.delete(f"/admin/api-users/{user['id']}").status_code == 204
    assert api(MANAGER).post("/admin/api-users", json={"username": "Y"}).status_code == 403


def test_error_log_masks_secrets_and_can_be_cleared(api, tmp_path, monkeypatch):
    admin = api(ADMIN)
    assert admin.get("/admin/error-log").json()["enabled"] is False
    log = tmp_path / "error.log"
    log.write_text("PHP Notice: x\nSQL: UPDATE user SET password='fake-hash' WHERE 1\ncurl token=fake-token&a=1\n")
    monkeypatch.setattr(get_settings().admin_tools, "error_log", str(log))

    view = admin.get("/admin/error-log").json()
    assert view["enabled"] is True and "PHP Notice" in view["text"]
    assert "fake-hash" not in view["text"] and "fake-token" not in view["text"] and "token=***" in view["text"]
    assert "fake-" not in admin.get("/admin/error-log/download").text
    assert admin.delete("/admin/error-log").status_code == 204
    assert log.read_text() == ""
    assert api(MANAGER).get("/admin/error-log").status_code == 403


def test_backup_is_sql_of_the_shops_own_tables(api):
    admin = api(ADMIN)
    tables = admin.get("/admin/backup/tables").json()
    assert "dd_order" in tables and all(t.startswith(("dd_", "erp_")) for t in tables)

    response = admin.get("/admin/backup.sql.gz?tables=dd_order_status&tables=dd_user_group")
    assert response.status_code == 200 and response.headers["content-type"] == "application/gzip"
    sql = gzip.decompress(response.content).decode()
    assert "CREATE TABLE `dd_order_status`" in sql and "INSERT INTO `dd_order_status`" in sql
    assert "'Адміністратор'" in sql  # the user group's name, quoted
    assert admin.get("/admin/backup.sql.gz?tables=mysql.user").status_code == 400
    assert admin.get("/admin/backup.sql.gz?tables=information_schema").status_code == 400


def test_sql_literals_escape_what_could_break_out():
    # A quote, a backslash and a line break: each escaped, so the value cannot end the string early.
    assert sql_literal("it's\\ \n") == r"'it\'s\\ \n'"
    assert (sql_literal(None), sql_literal(Decimal("1.50")), sql_literal(b"\x01")) == ("NULL", "1.50", "X'01'")
    assert sql_literal(datetime(2026, 11, 3, 10, 5)) == "'2026-11-03 10:05:00'"


def test_uploads_stay_inside_their_folder(api, db, tmp_path, monkeypatch):
    insert(db, "dd_upload", upload_id=1, name="макет.pdf", filename="макет.pdf.abc123", code="c1",
           date_added="2026-11-01")  # fmt: skip
    insert(db, "dd_upload", upload_id=2, name="x", filename="../../config.php", code="c2", date_added="2026-11-01")
    admin = api(ADMIN)
    assert admin.get("/admin/uploads/1/file").status_code == 400  # the folder is not connected
    folder = tmp_path / "upload"
    folder.mkdir()
    (folder / "макет.pdf.abc123").write_bytes(b"%PDF-1.4")
    (tmp_path / "config.php").write_text("<?php secret")
    monkeypatch.setattr(get_settings().admin_tools, "upload_dir", str(folder))

    listed = {u["id"]: u for u in admin.get("/admin/uploads").json()}
    assert (listed[1]["size"], listed[2]["size"]) == (8, None)
    file = admin.get("/admin/uploads/1/file")
    assert file.content == b"%PDF-1.4" and file.headers["x-content-type-options"] == "nosniff"
    assert admin.get("/admin/uploads/2/file").status_code == 400  # outside the folder
    assert admin.delete("/admin/uploads/1").status_code == 204
    assert not (folder / "макет.pdf.abc123").exists()
    assert admin.get("/admin/uploads/1/file").status_code == 404


def test_web_admin_tabs(api):
    admin = api(ADMIN)
    for tab in ("settings", "groups", "api", "log", "backup", "uploads"):
        page = admin.get(f"/app/admin?tab={tab}")
        assert page.status_code == 200 and "fake-" not in page.text, tab
    assert api(MANAGER).get("/app/admin").status_code == 403
    saved = admin.post("/app/admin/settings", data={"config_name": "Vidro Web"}, headers=HTMX)
    assert "Збережено." in saved.text and 'value="Vidro Web"' in saved.text
    user = admin.post(
        "/app/admin/api/0/save", data={"username": "Web", "enabled": "1", "ips": "10.0.0.9"}, headers=HTMX
    )
    assert 'value="Web"' in user.text
    api_id = next(u["id"] for u in admin.get("/admin/api-users").json() if u["username"] == "Web")
    key = admin.post(f"/app/admin/api/{api_id}/key", headers=HTMX)
    assert "більше його не буде показано" in key.text
