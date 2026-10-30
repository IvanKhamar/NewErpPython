"""File downloads and raw-body uploads shared by the routers."""

from fastapi import Request, Response

from newerp.application.common import SystemClock
from newerp.domain.common import DomainError

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def xlsx_file(content: bytes, name: str) -> Response:
    """Like Results.File in .NET: "name-2026-10-30.xlsx" as an attachment."""
    filename = f"{name}-{SystemClock().now():%Y-%m-%d}.xlsx"
    return Response(content, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


async def read_upload(request: Request, max_bytes: int) -> bytes:
    """
    The file comes as the raw request body (application/octet-stream), not a form: no anti-forgery form is needed,
    and a browser on another site cannot send such a request without CORS permission.
    """
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise DomainError("Файл більший за 5 МБ.")
    content = bytearray()
    async for chunk in request.stream():
        content += chunk
        if len(content) > max_bytes:
            raise DomainError("Файл більший за 5 МБ.")
    return bytes(content)
