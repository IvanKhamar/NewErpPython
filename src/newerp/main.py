"""Composition root of the API (like Program.cs): builds the FastAPI app and plugs in the routers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from newerp.api import health
from newerp.infrastructure.db import engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield  # the app serves requests here
    await engine.dispose()


app = FastAPI(title="NewErp API", version="0.1.0", lifespan=lifespan)
app.include_router(health.router)
