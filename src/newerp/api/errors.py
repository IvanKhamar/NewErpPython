"""Business exceptions → HTTP answers in the ProblemDetails format (like ErrorHandler.cs in .NET)."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm.exc import StaleDataError

from newerp.application.common import ConflictError, NotFoundError
from newerp.domain.common import DomainError

log = logging.getLogger(__name__)

# The "type" links ASP.NET Core puts into ProblemDetails, so both APIs answer the same.
_TYPES = {
    400: "https://tools.ietf.org/html/rfc9110#section-15.5.1",
    401: "https://tools.ietf.org/html/rfc9110#section-15.5.2",
    403: "https://tools.ietf.org/html/rfc9110#section-15.5.4",
    404: "https://tools.ietf.org/html/rfc9110#section-15.5.5",
    409: "https://tools.ietf.org/html/rfc9110#section-15.5.10",
    429: "https://tools.ietf.org/html/rfc6585#section-4",
    500: "https://tools.ietf.org/html/rfc9110#section-15.6.1",
}

BAD_BODY = "Не вдалося прочитати тіло запиту: перевірте, що це правильний JSON у кодуванні UTF-8."


def problem(status: int, title: str, detail: str | None = None, headers: dict[str, str] | None = None) -> JSONResponse:
    body: dict[str, object] = {"type": _TYPES.get(status, "about:blank"), "title": title, "status": status}
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=headers)


class NotAuthenticated(Exception):
    """No valid session: 401 with an empty body, like the cookie scheme in .NET."""


class Forbidden(Exception):
    """Signed in, but the role has no access: 403 with an empty body."""


def add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> Response:
        return problem(404, "Не знайдено", str(exc))

    @app.exception_handler(DomainError)
    async def domain(_: Request, exc: DomainError) -> Response:
        return problem(400, "Порушено бізнес-правило", str(exc))

    @app.exception_handler(ConflictError)
    @app.exception_handler(StaleDataError)
    async def conflict(_: Request, exc: Exception) -> Response:
        return problem(409, "Дані щойно змінив хтось інший, повторіть дію", str(exc))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> Response:
        # A request that cannot be read (broken JSON, wrong field type) is the client's error, not the server's.
        errors = exc.errors()
        if any(e.get("loc", ("",))[0] == "body" for e in errors):
            return problem(400, "Некоректний запит", BAD_BODY)
        where = ", ".join(".".join(str(part) for part in e.get("loc", ())) for e in errors)
        return problem(400, "Некоректний запит", f"Некоректні параметри запиту: {where}.")

    @app.exception_handler(NotAuthenticated)
    async def not_authenticated(_: Request, __: NotAuthenticated) -> Response:
        return Response(status_code=401)

    @app.exception_handler(Forbidden)
    async def forbidden(_: Request, __: Forbidden) -> Response:
        return Response(status_code=403)

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> Response:
        log.exception("Unhandled error", exc_info=exc)
        return problem(500, "An error occurred while processing your request.")
