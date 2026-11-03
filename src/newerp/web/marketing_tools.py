"""Marketing pages added in Python (items 57, 58): tracking codes, voucher themes and sending a voucher by e-mail.
Registered before the marketing module, so /app/marketing/vouchers/themes is not read as a voucher number."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from starlette.datastructures import FormData

from newerp.api.deps import Session
from newerp.api.marketing import Instagram, Klaviyo, Tracking, VoucherAdmin
from newerp.api.security import Policies
from newerp.application.marketing.integrations import KlaviyoSettingsDto
from newerp.application.marketing.tracking import TrackingCodeDto
from newerp.application.marketing.voucher_admin import VoucherThemeDto
from newerp.config import get_settings
from newerp.web.common import BUSINESS_ERRORS, WebUser, is_fragment, page_roles, render, undo

router = APIRouter(
    prefix="/app/marketing", include_in_schema=False, dependencies=[Depends(page_roles(*Policies.ORDERS))]
)
Field = Annotated[str, Form()]


def _text(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value.strip() if isinstance(value, str) else ""


async def _tracking(request: Request, service: Tracking, **messages: Any) -> HTMLResponse:
    template = "marketing/_tracking.html" if is_fragment(request) else "marketing/tracking.html"
    return render(request, template, codes=await service.codes(), **messages)


@router.get("/tracking")
async def tracking_page(request: Request, service: Tracking) -> HTMLResponse:
    return await _tracking(request, service)


@router.post("/tracking/{marketing_id}/{action}")
async def change_tracking(
    request: Request,
    marketing_id: int,
    action: Literal["save", "delete"],
    user: WebUser,
    service: Tracking,
    session: Session,
) -> HTMLResponse:
    form = await request.form()
    try:
        if action == "delete":
            await service.delete(marketing_id, user.user_id)
        else:
            item = TrackingCodeDto(
                name=_text(form, "name"), code=_text(form, "code"), description=_text(form, "description")
            )
            await service.save(marketing_id or None, item)
    except ValidationError:
        return await _tracking(request, service, error="Перевірте поля: назва до 32 знаків, код до 64.")
    except BUSINESS_ERRORS as error:
        return await _tracking(request, service, error=await undo(session, error))
    return await _tracking(request, service, saved="Збережено.")


async def _themes(request: Request, service: VoucherAdmin, **messages: Any) -> HTMLResponse:
    template = "marketing/_themes.html" if is_fragment(request) else "marketing/themes.html"
    return render(request, template, themes=await service.themes(), **messages)


@router.get("/vouchers/themes")
async def themes_page(request: Request, service: VoucherAdmin) -> HTMLResponse:
    return await _themes(request, service)


@router.post("/vouchers/themes/{theme_id}/{action}")
async def change_theme(
    request: Request,
    theme_id: int,
    action: Literal["save", "delete"],
    user: WebUser,
    service: VoucherAdmin,
    session: Session,
) -> HTMLResponse:
    form = await request.form()
    try:
        if action == "delete":
            await service.delete_theme(theme_id, user.user_id)
        else:
            theme = VoucherThemeDto(name=_text(form, "name"), image=_text(form, "image"))
            await service.save_theme(theme_id or None, theme)
    except ValidationError:
        return await _themes(request, service, error="Перевірте поля: назва до 32 знаків.")
    except BUSINESS_ERRORS as error:
        return await _themes(request, service, error=await undo(session, error))
    return await _themes(request, service, saved="Збережено.")


@router.post("/vouchers/{voucher_id}/email")
async def email_voucher(
    request: Request, voucher_id: int, user: WebUser, service: VoucherAdmin, session: Session, to: Field = ""
) -> HTMLResponse:
    try:
        result = await service.send_email(voucher_id, to or None, user.user_id)
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    return render(request, "_note.html", text=f"Лист надіслано на {result.to}.")


# ── Integrations: Klaviyo, the TikTok feed, Instagram (the admin only) ──

integrations = [Depends(page_roles(*Policies.INTEGRATIONS))]


async def _integrations(request: Request, klaviyo: Klaviyo, **messages: Any) -> HTMLResponse:
    template = "marketing/_integrations.html" if is_fragment(request) else "marketing/integrations.html"
    settings = get_settings()
    return render(
        request, template, klaviyo=await klaviyo.settings(), feed_enabled=settings.tiktok_feed.enabled,
        store_url=settings.customers.store_url, **messages,
    )  # fmt: skip


@router.get("/integrations", dependencies=integrations)
async def integrations_page(request: Request, klaviyo: Klaviyo) -> HTMLResponse:
    return await _integrations(request, klaviyo)


@router.post("/integrations/klaviyo", dependencies=integrations)
async def save_klaviyo(request: Request, klaviyo: Klaviyo, session: Session) -> HTMLResponse:
    form = await request.form()
    try:
        settings = KlaviyoSettingsDto(
            enabled=_text(form, "enabled") == "1",
            tracking=_text(form, "tracking") == "1",
            newsletter_list_id=_text(form, "newsletter_list_id"),
        )
        await klaviyo.save_settings(settings)
    except ValidationError:
        return await _integrations(request, klaviyo, error="Номер списку — до 64 знаків.")
    except BUSINESS_ERRORS as error:
        return await _integrations(request, klaviyo, error=await undo(session, error))
    return await _integrations(request, klaviyo, saved="Збережено.")


@router.post("/integrations/instagram", dependencies=integrations)
async def update_instagram(request: Request, instagram: Instagram, session: Session) -> HTMLResponse:
    try:
        result = await instagram.update()
    except BUSINESS_ERRORS as error:
        return render(request, "_error.html", error=await undo(session, error))
    if not result.configured:
        return render(request, "_error.html", error="Токена Instagram немає в налаштуваннях модуля вітрини.")
    return render(
        request, "_note.html", text=f"Нових дописів: {result.added}, картинок збережено: {result.pictures_saved}."
    )
