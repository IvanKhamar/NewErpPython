"""Web interface at /app/ (the Blazor app in .NET, the Vaadin views in Java).

The server renders HTML from Jinja2 templates, and HTMX makes it interactive: a button sends a request and swaps
in the HTML fragment that comes back, so there is no JavaScript build. Pages call the same application services as
the API, through the same dependencies.
"""

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from newerp.web import auth, home, orders, stock
from newerp.web.common import HERE, PageForbidden, Redirect, render


def add_web(app: FastAPI) -> None:
    app.mount("/app/static", StaticFiles(directory=HERE / "static"), name="web-static")
    for module in (auth, home, orders, stock):
        app.include_router(module.router)

    @app.exception_handler(Redirect)
    async def redirect(request: Request, exc: Redirect) -> Response:
        if "HX-Request" in request.headers:
            # A 303 would be followed inside HTMX and the login page swapped into a fragment; this loads it whole.
            return Response(headers={"HX-Redirect": exc.url})
        return RedirectResponse(exc.url, status_code=303)

    @app.exception_handler(PageForbidden)
    async def forbidden(request: Request, _: PageForbidden) -> Response:
        return render(request, "forbidden.html", status_code=403)
