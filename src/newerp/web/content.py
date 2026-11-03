"""Storefront content and feedback pages (Python first): information pages, FAQ, blog, the home page blocks,
reviews and questions, as tabs of one page.

The simple lists share one form template: each kind names its fields and their types (text, html, int, bool, date,
links), the template draws them and _values reads them back, so a new kind needs no new HTML."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from starlette.datastructures import FormData

from newerp.api.catalog import Feedback, Pages
from newerp.api.deps import Session
from newerp.api.security import Policies
from newerp.application.catalog.feedback import FeedbackState, UpdateQuestionRequest, UpdateReviewRequest
from newerp.application.catalog.pages import (
    AboutBlockDto,
    AboutItemDto,
    BlogCategoryDto,
    BlogPostDto,
    FaqDto,
    InformationDto,
    NftBlockDto,
    NftImageDto,
    NftItemDto,
)
from newerp.web.common import BUSINESS_ERRORS, WebUser, is_fragment, number, page_roles, render, undo

router = APIRouter(
    prefix="/app/catalog/content", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))]
)
Field = Annotated[str, Form()]

TABS = (
    ("information", "Сторінки"),
    ("faq", "FAQ"),
    ("blog-categories", "Рубрики блогу"),
    ("blog-posts", "Блог"),
    ("about", "Про нас"),
    ("nft", "NFT"),
    ("reviews", "Відгуки"),
    ("questions", "Питання"),
)
Tab = Literal["information", "faq", "blog-categories", "blog-posts", "about", "nft", "reviews", "questions"]
ListKind = Literal["information", "faq", "blog-categories", "blog-posts"]

# (field, label, type) per kind; "title" is the field shown in the closed row.
FIELDS: dict[str, tuple[tuple[str, str, str], ...]] = {
    "information": (
        ("title", "Заголовок", "text"),
        ("description", "Текст (HTML)", "html"),
        ("seo_keyword", "SEO-адреса", "text"),
        ("meta_title", "Meta title", "text"),
        ("meta_description", "Meta description", "text"),
        ("meta_keyword", "Meta keywords", "text"),
        ("sort_order", "Порядок", "int"),
        ("bottom", "Посилання внизу сторінки", "bool"),
        ("enabled", "Увімкнена", "bool"),
    ),
    "faq": (
        ("question", "Питання", "text"),
        ("answer", "Відповідь (HTML)", "html"),
        ("author", "Автор", "text"),
        ("email", "E-mail", "text"),
        ("sort_order", "Порядок", "int"),
        ("enabled", "Показувати", "bool"),
    ),
    "blog-categories": (("name", "Назва", "text"), ("enabled", "Увімкнена", "bool")),
    "blog-posts": (
        ("name", "Заголовок", "text"),
        ("description", "Текст (HTML)", "html"),
        ("date_available", "Показувати з", "datetime"),
        ("category_ids", "Рубрики", "links"),
        ("image", "Картинка", "text"),
        ("big_image", "Велика картинка", "text"),
        ("image_alt", "Підпис картинки", "text"),
        ("seo_keyword", "SEO-адреса", "text"),
        ("meta_title", "Meta title", "text"),
        ("meta_description", "Meta description", "text"),
        ("top", "Угорі", "bool"),
        ("enabled", "Опублікований", "bool"),
    ),
}
TITLES = {"information": "title", "faq": "question", "blog-categories": "name", "blog-posts": "name"}
MODELS = {"information": InformationDto, "faq": FaqDto, "blog-categories": BlogCategoryDto, "blog-posts": BlogPostDto}


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def _values(kind: str, form: FormData) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, _, kind_of in FIELDS[kind]:
        if kind_of == "bool":
            values[name] = _text(form, name) == "1"
        elif kind_of == "int":
            values[name] = number(_text(form, name)) or 0
        elif kind_of == "datetime":
            raw = _text(form, name)
            values[name] = datetime.fromisoformat(raw) if raw else None
        elif kind_of == "links":
            values[name] = [number(str(v)) or 0 for v in form.getlist(name)]
        else:
            values[name] = _text(form, name)
    return values


async def _tab(
    request: Request, tab: str, pages: Pages, feedback: Feedback, state: str = "pending", **messages: Any
) -> HTMLResponse:
    context: dict[str, Any] = {"tab": tab, "tabs": TABS, "state": state, **messages}
    if tab in FIELDS:
        loaders = {
            "information": pages.information,
            "faq": pages.faqs,
            "blog-categories": pages.blog_categories,
            "blog-posts": pages.blog_posts,
        }
        context |= {
            "items": await loaders[tab](),
            "fields": FIELDS[tab],
            "title_field": TITLES[tab],
            "blank": MODELS[tab](),
            "rubrics": await pages.blog_categories() if tab == "blog-posts" else [],
        }
    elif tab == "about":
        context["block"] = await pages.about()
    elif tab == "nft":
        context["block"] = await pages.nft()
    elif tab == "reviews":
        context["result"] = await feedback.reviews(_state(state), 1, 100)
    else:
        context["result"] = await feedback.questions(_state(state), 1, 100)
    template = "content/_tab.html" if is_fragment(request) else "content/page.html"
    return render(request, template, **context)


def _state(value: str) -> FeedbackState:
    return value if value in ("all", "pending", "published") else "pending"  # type: ignore[return-value]


@router.get("")
async def content_page(
    request: Request, pages: Pages, feedback: Feedback, tab: Tab = "information", state: str = "pending"
) -> HTMLResponse:
    return await _tab(request, tab, pages, feedback, state)


@router.post("/{kind}/{item_id}/{action}")
async def change_item(
    request: Request,
    kind: ListKind,
    item_id: int,
    action: Literal["save", "delete"],
    user: WebUser,
    pages: Pages,
    feedback: Feedback,
    session: Session,
) -> HTMLResponse:
    """item_id 0 with save adds a new one."""
    try:
        if action == "delete":
            await pages.delete(kind, item_id, user.user_id)
        else:
            item = MODELS[kind](**_values(kind, await request.form()))
            savers = {
                "information": pages.save_information,
                "faq": pages.save_faq,
                "blog-categories": pages.save_blog_category,
                "blog-posts": pages.save_blog_post,
            }
            await savers[kind](item_id or None, item)
    except (ValidationError, ValueError) as error:
        message = str(error) if not isinstance(error, ValidationError) else "Перевірте поля."
        return await _tab(request, kind, pages, feedback, error=message)
    except BUSINESS_ERRORS as error:
        return await _tab(request, kind, pages, feedback, error=await undo(session, error))
    return await _tab(request, kind, pages, feedback, saved="Збережено.")


def _rows(form: FormData, prefix: str, names: tuple[str, ...]) -> list[dict[str, str]]:
    """Repeated fields prefix_name as table rows; a row with every field empty is left out."""
    columns = [[str(v) for v in form.getlist(f"{prefix}_{name}")] for name in names]
    rows = [dict(zip(names, cells, strict=True)) for cells in zip(*columns, strict=True)]
    return [row for row in rows if any(cell.strip() for cell in row.values())]


@router.post("/blocks/{block}")
async def save_block(
    request: Request, block: Literal["about", "nft"], pages: Pages, feedback: Feedback, session: Session
) -> HTMLResponse:
    form = await request.form()
    texts = ("heading_title", "subtitle", "description")
    try:
        if block == "about":
            items = [
                AboutItemDto(**{**row, "sort_order": number(row["sort_order"]) or 0})
                for row in _rows(form, "item", ("image_left", "image_right", "image_mobile", "sort_order"))
            ]
            await pages.save_about(
                AboutBlockDto(enabled=_text(form, "enabled") == "1", items=items, **{n: _text(form, n) for n in texts})
            )
        else:
            images = [
                NftImageDto(**{**row, "sort_order": number(row["sort_order"]) or 0})
                for row in _rows(form, "image", ("image", "link", "sort_order"))
            ]
            items = [
                NftItemDto(**{**row, "sort_order": number(row["sort_order"]) or 0})
                for row in _rows(form, "item", ("title", "subtitle", "description", "sort_order"))
            ]
            more = ("heading_form", "subtitle_form", "link", "text_link", "image")
            await pages.save_nft(
                NftBlockDto(
                    enabled=_text(form, "enabled") == "1",
                    images=images,
                    items=items,
                    **{n: _text(form, n) for n in (*texts, *more)},
                )
            )
    except ValidationError:
        return await _tab(request, block, pages, feedback, error="Перевірте поля.")
    except BUSINESS_ERRORS as error:
        return await _tab(request, block, pages, feedback, error=await undo(session, error))
    return await _tab(request, block, pages, feedback, saved="Збережено.")


@router.post("/{kind}/{item_id}/moderate/{action}")
async def moderate(
    request: Request,
    kind: Literal["reviews", "questions"],
    item_id: int,
    action: Literal["save", "delete"],
    user: WebUser,
    pages: Pages,
    feedback: Feedback,
    session: Session,
    state: Field = "pending",
) -> HTMLResponse:
    form = await request.form()
    try:
        if kind == "reviews" and action == "save":
            review = UpdateReviewRequest(
                author=_text(form, "author"),
                text=_text(form, "text"),
                rating=number(_text(form, "rating")) or 5,
                published=_text(form, "published") == "1",
            )
            await feedback.update_review(item_id, review, user.user_id)
        elif kind == "reviews":
            await feedback.delete_review(item_id, user.user_id)
        elif action == "save":
            answer = UpdateQuestionRequest(
                question=_text(form, "question"),
                answer=_text(form, "answer"),
                published=_text(form, "published") == "1",
            )
            await feedback.update_question(item_id, answer, user.user_id)
        else:
            await feedback.delete_question(item_id, user.user_id)
    except ValidationError:
        return await _tab(request, kind, pages, feedback, state, error="Перевірте поля.")
    except BUSINESS_ERRORS as error:
        return await _tab(request, kind, pages, feedback, state, error=await undo(session, error))
    return await _tab(request, kind, pages, feedback, state, saved="Збережено.")
