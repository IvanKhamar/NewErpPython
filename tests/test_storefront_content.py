"""Storefront content (item 50): information pages, FAQ, blog, the about and nft blocks; feedback (item 51):
moderation of reviews and customer questions."""

import json
from datetime import datetime

import pytest

from tests import seed
from tests.seed import CATEGORY_PRODUCT_ID, MANAGER, STOREKEEPER

HTMX = {"HX-Request": "true"}


def test_information_pages_clean_html_keep_unique_addresses_and_delete(api):
    client = api(MANAGER)
    page = client.post("/catalog/information", json={
        "title": "Доставка", "description": '<h2>Умови</h2><iframe src="https://x"></iframe><p style="x">Текст</p>',
        "seoKeyword": "dostavka", "bottom": True,
    })  # fmt: skip
    assert page.status_code == 201, page.text
    page = page.json()
    assert (page["description"], page["bottom"], page["seoKeyword"]) == ("<h2>Умови</h2><p>Текст</p>", True, "dostavka")
    assert client.post("/catalog/information", json={"title": "Інша", "seoKeyword": "dostavka"}).status_code == 409
    assert (
        client.put(f"/catalog/information/{page['id']}", json=page | {"title": "Доставка й оплата"}).json()[
            "seoKeyword"
        ]
        == "dostavka"
    )  # its own address stays allowed
    assert client.post("/catalog/information", json={"title": " "}).status_code == 400
    assert client.delete(f"/catalog/content/information/{page['id']}").status_code == 204
    assert client.delete(f"/catalog/content/information/{page['id']}").status_code == 404
    # The address is free again.
    assert client.post("/catalog/information", json={"title": "Нова", "seoKeyword": "dostavka"}).status_code == 201


def test_faq_and_blog(api):
    client = api(MANAGER)
    faq = client.post("/catalog/faq", json={"question": "Як прати льон?", "answer": "<p>При 30°</p>",
                                            "author": "Олена", "email": "olena@example.com"}).json()  # fmt: skip
    assert (faq["question"], faq["answer"]) == ("Як прати льон?", "<p>При 30°</p>")
    assert client.post("/catalog/faq", json={"question": "Х", "email": "not-an-email"}).status_code == 400

    rubric = client.post("/catalog/blog/categories", json={"name": "Новини"}).json()
    post = client.post("/catalog/blog/posts", json={
        "name": "Нова колекція", "description": "<p>Льон</p><script>x</script>", "categoryIds": [rubric["id"]],
        "image": "catalog/blog/1.jpg", "seoKeyword": "nova-kolektsiia",
    })  # fmt: skip
    assert post.status_code == 201, post.text
    post = post.json()
    assert (post["description"], post["categoryIds"]) == ("<p>Льон</p>", [rubric["id"]])
    assert post["dateAvailable"] is not None  # empty means now
    assert client.get("/catalog/blog/categories").json()[0]["posts"] == 1
    assert client.post("/catalog/blog/posts", json={"name": "Х", "categoryIds": [9999]}).status_code == 400
    assert client.post("/catalog/blog/posts", json={"name": "Х", "image": "/etc/x.jpg"}).status_code == 400
    assert client.delete(f"/catalog/content/blog-posts/{post['id']}").status_code == 204
    assert client.delete(f"/catalog/content/blog-categories/{rubric['id']}").status_code == 204
    assert client.delete("/catalog/content/pages/1").status_code == 400  # not a kind


def test_home_page_blocks_keep_other_languages(api, db):
    other_language = {"1": {"heading_title": "About us", "subtitle": "", "description": "<p>EN</p>"}}
    seed.insert(db, "dd_setting", store_id=0, code="module_about", key="module_about_description",
                value=json.dumps(other_language), serialized=True)  # fmt: skip
    client = api(MANAGER)
    saved = client.put("/catalog/blocks/about", json={
        "enabled": True, "headingTitle": "Про нас", "description": "<p>Шиємо</p><img src=x onerror=y>",
        "items": [{"imageLeft": "catalog/about/l.jpg", "sortOrder": 1}],
    }).json()  # fmt: skip
    assert (saved["enabled"], saved["headingTitle"], saved["description"]) == (
        True,
        "Про нас",
        '<p>Шиємо</p><img src="x">',
    )
    assert saved["items"][0]["imageLeft"] == "catalog/about/l.jpg"
    with db.cursor() as cursor:
        cursor.execute(
            "SELECT value FROM dd_setting WHERE code = 'module_about' AND `key` = 'module_about_description'"
        )
        stored = json.loads(cursor.fetchone()[0])
    assert stored["1"]["heading_title"] == "About us"  # the English text stayed
    assert client.put("/catalog/blocks/about", json={"items": [{"imageLeft": "../x.jpg"}]}).status_code == 400

    nft = client.put("/catalog/blocks/nft", json={
        "enabled": True, "headingTitle": "NFT", "link": "/nft",
        "images": [{"image": "catalog/nft/1.png", "link": "/a"}],
        "items": [{"title": "Крок 1", "description": "<b>Купіть</b>"}],
    }).json()  # fmt: skip
    assert (nft["link"], nft["images"][0]["link"], nft["items"][0]["title"]) == ("/nft", "/a", "Крок 1")
    assert client.put("/catalog/blocks/nft", json={"link": "javascript:alert(1)"}).status_code == 400
    assert api(STOREKEEPER).get("/catalog/blocks/nft").status_code == 403


@pytest.fixture(scope="module")
def feedback(db):
    now = datetime(2026, 11, 1, 10)
    seed.insert(db, "dd_review", review_id=1, product_id=CATEGORY_PRODUCT_ID, customer_id=0, author="Ірина",
                text="Чудова сорочка", rating=5, status=False, date_added=now, date_modified=now)  # fmt: skip
    seed.insert(db, "dd_review", review_id=2, product_id=CATEGORY_PRODUCT_ID, customer_id=0, author="Петро",
                text="Нормально", rating=4, status=True, date_added=now, date_modified=now)  # fmt: skip
    seed.insert(db, "dd_question", question_id=1, product_id=CATEGORY_PRODUCT_ID, name="Оксана",
                telephone="+380671112233", question="Чи є розмір L?", date_added=now)  # fmt: skip


def test_reviews_are_moderated(api, feedback):
    client = api(MANAGER)
    pending = client.get("/catalog/reviews?state=pending").json()
    assert [(r["author"], r["productName"]) for r in pending["items"]] == [("Ірина", "Сорочка лляна оверсайз")]
    body = {"author": "Ірина", "text": "Чудова сорочка!", "rating": 5, "published": True}
    assert client.put("/catalog/reviews/1", json=body).status_code == 204
    assert client.get("/catalog/reviews?state=pending").json()["totalCount"] == 0
    assert client.put("/catalog/reviews/1", json=body | {"rating": 6}).status_code == 400
    assert client.put("/catalog/reviews/999", json=body).status_code == 404
    assert client.delete("/catalog/reviews/2").status_code == 204
    assert client.get("/catalog/reviews?state=everything").status_code == 400


def test_questions_are_answered_before_publishing(api, feedback):
    client = api(MANAGER)
    [question] = client.get("/catalog/questions?state=pending").json()["items"]
    assert (question["name"], question["question"], question["answer"]) == ("Оксана", "Чи є розмір L?", "")
    no_answer = {"question": question["question"], "answer": "", "published": True}
    assert client.put("/catalog/questions/1", json=no_answer).status_code == 400
    answered = no_answer | {"answer": "Так, з'явиться за тиждень."}
    assert client.put("/catalog/questions/1", json=answered).status_code == 204
    [published] = client.get("/catalog/questions?state=published").json()["items"]
    assert published["answer"] == "Так, з'явиться за тиждень."
    assert client.delete("/catalog/questions/1").status_code == 204
    assert client.delete("/catalog/questions/1").status_code == 404


def test_web_content_tabs(api, feedback):
    client = api(MANAGER)
    for tab in ("information", "faq", "blog-categories", "blog-posts", "about", "nft", "reviews", "questions"):
        assert client.get(f"/app/catalog/content?tab={tab}").status_code == 200, tab
    added = client.post(
        "/app/catalog/content/faq/0/save",
        headers=HTMX,
        data={"question": "Веб-питання", "answer": "<p>Так</p>", "enabled": "1"},
    )
    assert "Збережено." in added.text and "Веб-питання" in added.text
    refused = client.post("/app/catalog/content/information/0/save", data={"title": " "}, headers=HTMX)
    assert 'class="error"' in refused.text
    block = client.post(
        "/app/catalog/content/blocks/nft",
        headers=HTMX,
        data={
            "heading_title": "NFT веб",
            "link": "/nft",
            "image_image": ["catalog/n.png", ""],
            "image_link": ["", ""],
            "image_sort_order": ["1", ""],
        },
    )
    assert "Збережено." in block.text and 'value="catalog/n.png"' in block.text
    moderated = client.post(
        "/app/catalog/content/reviews/1/moderate/save",
        headers=HTMX,
        data={"author": "Петро", "text": "Добре", "rating": "4", "published": "1", "state": "all"},
    )
    assert "Збережено." in moderated.text
