"""The remarketing module's settings (item 62): secrets never leave the server, scripts and lists stay read-only."""

import json

import pytest

from tests.seed import ADMIN, MANAGER, insert

HTMX = {"HX-Request": "true"}


@pytest.fixture(scope="module", autouse=True)
def settings(db):
    for key, value, serialized in (
        ("remarketing_status", "1", False),
        ("remarketing_facebook_id", "1234567890", False),
        ("remarketing_facebook_token", "EAAG-fake-token", False),
        ("remarketing_telegram_bot_id", "123:fake", False),
        ("remarketing_counter1", "<script>x()</script>", False),
        ("remarketing_feed_custom_sql", "SELECT 1", False),
        ("remarketing_ga4_send_status", json.dumps(["5", "22"]), True),
    ):
        insert(db, "dd_setting", store_id=0, code="remarketing", key=key, value=value, serialized=serialized)


def test_secrets_are_never_sent_and_scripts_never_changed(api, db):
    admin = api(ADMIN)
    response = admin.get("/marketing/remarketing/settings")
    assert "fake" not in response.text  # neither token reaches the browser
    groups = {g["name"]: {s["key"]: s for s in g["settings"]} for g in response.json()}
    token = groups["facebook"]["remarketing_facebook_token"]
    assert (token["kind"], token["value"], token["isSet"]) == ("secret", None, True)
    assert groups["general"]["remarketing_counter1"]["kind"] == "locked"  # a one-word key goes to "general"
    assert groups["ga4"]["remarketing_ga4_send_status"]["kind"] == "list"
    assert groups["general"]["remarketing_status"]["value"] == "1"

    saved = admin.put("/marketing/remarketing/settings", json={"remarketing_facebook_id": "999"})
    assert saved.status_code == 200
    with db.cursor() as cursor:
        cursor.execute("SELECT value FROM dd_setting WHERE `key` = 'remarketing_facebook_id'")
        assert cursor.fetchone() == ("999",)

    for change in (
        {"remarketing_facebook_token": "stolen"},
        {"remarketing_counter1": "<script>evil()</script>"},
        {"remarketing_feed_custom_sql": "DROP TABLE dd_order"},
        {"remarketing_ga4_send_status": "[]"},
        {"remarketing_unknown": "1"},
        {"remarketing_facebook_id": "1\n2"},
    ):
        assert admin.put("/marketing/remarketing/settings", json=change).status_code == 400, change
    with db.cursor() as cursor:
        cursor.execute("SELECT value FROM dd_setting WHERE `key` = 'remarketing_facebook_token'")
        assert cursor.fetchone() == ("EAAG-fake-token",)
    assert api(MANAGER).get("/marketing/remarketing/settings").status_code == 403


def test_web_remarketing_settings(api):
    admin = api(ADMIN)
    page = admin.get("/app/marketing/remarketing")
    assert page.status_code == 200 and "fake" not in page.text and "задано" in page.text
    saved = admin.post("/app/marketing/remarketing", data={"remarketing_facebook_id": "777"}, headers=HTMX)
    assert "Збережено." in saved.text and 'value="777"' in saved.text
