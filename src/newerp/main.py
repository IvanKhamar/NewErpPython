"""Composition root of the API (like Program.cs): builds the FastAPI app and plugs in the routers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from newerp.api import auth, cashbox, delivery, health, locations, orders, payments, production, staff, stock, users
from newerp.api.errors import add_error_handlers
from newerp.api.security import add_security_middleware
from newerp.infrastructure.db import engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield  # the app serves requests here
    await engine.dispose()


app = FastAPI(title="NewErp API", version="0.1.0", lifespan=lifespan)
add_error_handlers(app)
add_security_middleware(app)

for module in (health, auth, users, orders, staff, stock, locations, production):
    app.include_router(module.router)
app.include_router(delivery.shipment_router)
app.include_router(delivery.delivery_router)
app.include_router(cashbox.router)
app.include_router(cashbox.order_router)
app.include_router(payments.router)
app.include_router(payments.order_router)
