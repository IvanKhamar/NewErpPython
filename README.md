# NewErpPython

Python version of NewErp: an ERP for a sewing workshop and online clothing store. It works with the same MySQL
database and the same HTTP API contract as the [.NET](https://github.com/IvanKhamar/NewErp) and Java versions:
one system, three backends, one contract.

**Status:** the whole .NET API is ported (phases in [docs/plan.md](docs/plan.md)): 358 tests, CI with ruff,
import-linter, pytest, a Docker image build and gitleaks. The web interface at `/app/` covers every module, like the
Blazor app of the .NET version. What is still missing: the table at the end of the plan.

## What it does

| Area | Endpoints |
|---|---|
| Auth and users | `/auth` (cookie login, the same password hashes as ASP.NET Identity), `/users`, `/staff` |
| Orders | `/orders`: list, card, new order, status changes with handlers, managers, customer risk |
| Warehouse | `/stock`, `/locations` (Excel import and export) |
| Production | `/production`: models, cut and sewn operations, sewing plan and its consumables, transfers, storefront stock |
| Delivery | `/orders/{id}/shipment`, `/delivery`: Nova Poshta waybills, tracking, pickup reminders, international |
| Money | `/cashbox` (Checkbox fiscal receipts, shifts), `/payments` (PrivatBank, LiqPay) |
| Catalog | `/catalog/products`, `/catalog/sort` (sort by sales, pins), `/preorders` |
| Marketing | `/marketing` (birthday coupons, coupons with exclusions, contact export), gift vouchers, `/sms` |
| After-sales | `/returns`, `/customers` |
| Partners | `/partners` and their XML feed `/feeds/{code}.xml` |
| Reports | `/reports` (sales, products, statuses, UTM, taxes, product views, online, reward points, dashboard), revenue feed `/feeds/revenue.json` |
| Competitors | `/competitors`: product pages on competitors' sites, price checks, comparison |

The web interface at `/app/` (in Ukrainian, like the other versions) renders HTML on the server with Jinja2; HTMX
swaps in the fragments the server returns, so there is no JavaScript build. Pages call the same application services
as the API and use the same login cookie and roles; the menu shows each role only its modules.

- Orders: the list, new orders, and the order card with the status, the manager, payments with a LiqPay link, the
  fiscal cheque, the Nova Poshta waybill (or one abroad), a gift voucher and returns.
- Sales: returns, customers, delivery reminders, products and their sort by sales, pre-orders, birthdays, the contact
  export, gift vouchers, coupons, partners and their feeds, competitor prices, reports.
- Money: the cashbox (shifts, X and Z reports, cheques) and matching bank payments to orders.
- Warehouse and production: consumables, stocktaking with an Excel sheet, reorder and purchase orders; models with the
  cut and sewn operations, specifications (BOMs), product links and stocktaking, the sewing plan and the log.
- Admin: users with temporary passwords, the managers' bonuses.

Celery beat tasks: closing cashbox shifts, settling pre-orders, the daily competitor price check (07:00), the low-stock
SMS (08:00, with phones set) and the hourly Nova Poshta tracking refresh. Orders also go to Kafka as `OrderCreated` and
`OrderStatusChanged` events (topic `newerp.orders`, key = order id); `newerp.consumer` reads them. Without API keys every outside service uses a stub,
and without Kafka settings the events only go to the log, so local development never reaches a real service.

## Stack

Python 3.13 · uv · FastAPI · Jinja2 + HTMX · Pydantic v2 · SQLAlchemy 2 (async, aiomysql) · MySQL 8 · Celery + Redis · Kafka (aiokafka) · pytest · ruff · import-linter

## Structure

```
src/newerp/
  domain/          business rules in plain Python (no FastAPI, no database)
  application/     use cases and the interfaces they need
  infrastructure/  SQLAlchemy (db.py), Celery tasks, external clients
  api/             FastAPI routers and request/response schemas
  web/             HTML pages at /app/: routes, Jinja2 templates, static files (HTMX, Pico.css)
  config.py        settings from env vars NEWERP_* and .env
  main.py          API composition root (FastAPI app)
  worker.py        worker composition root (Celery app)
  consumer.py      Kafka reader of order events
tests/             pytest
tools/devseed.py   demo data for the local database
db/                the same SQL as the .NET repo: OpenCart tables, reconstructed tables, erp_* tables
```

Dependencies point inward only (`web → api → infrastructure → application → domain`); `uv run lint-imports` checks it.

| .NET | Java | Python here |
|---|---|---|
| .csproj, NuGet | pom.xml, Maven | pyproject.toml, uv |
| ASP.NET Core Minimal API | Spring Web MVC | FastAPI |
| Built-in DI | Spring DI | `Depends(...)` |
| Data annotations / FluentValidation | Bean Validation | Pydantic models |
| EF Core, `DbContext` | Spring Data JPA | SQLAlchemy 2, `AsyncSession` |
| `appsettings.json` + user-secrets | `application.yml` | pydantic-settings + `.env` |
| `BackgroundService` / Hangfire | `@Async` / `@Scheduled` | Celery |
| — | — | Kafka (aiokafka) for order events |
| Blazor WebAssembly + MudBlazor (`/app/`) | Vaadin | Jinja2 + HTMX (`/app/`) |
| Scalar (`/scalar`) | Swagger UI | Swagger UI (`/docs`) |
| xUnit + WebApplicationFactory | JUnit 5 + `@SpringBootTest` | pytest + `TestClient` |

## Run on Windows

1. Install [uv](https://docs.astral.sh/uv/) (it also installs Python itself):
   `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`, then `uv python install 3.13`.
2. Install Docker Desktop and start the infrastructure: `docker compose up -d`.
   MySQL listens on port 3307 (the local MySQL80 keeps 3306) and loads `db/*.sql` on the first start.
3. `copy .env.example .env` and change the values if needed.
4. `uv sync`, then `uv run fastapi dev src/newerp/main.py`. Open http://localhost:8000/health and /docs.

### Everything in Docker, with demo data

`docker compose up -d` also builds the API from the [Dockerfile](Dockerfile) (two stages: uv builds the virtual
environment, only the environment goes into the slim runtime image) and serves it on http://localhost:8000/app/
(the web interface) and http://localhost:8000/docs.
The database starts empty; fill it with invented data (a port of the .NET `DevSeed`, made with Faker):

```
uv run python tools/devseed.py   # only into an empty newerp_dev on localhost; about 400 orders and every module
```

Logins, all with the password `dev`: `admin` (Admin), `sklad` (Storekeeper), `manager1`…`manager4` (Manager;
`manager5` is disabled). The Celery worker still runs on the host.

```
uv run pytest                                              # tests (need the MySQL from docker compose)
uv run ruff check . ; uv run ruff format --check .         # lint and format check
uv run lint-imports                                        # layer check
uv run celery -A newerp.worker worker --beat --pool=solo   # worker with the scheduler (--pool=solo: Windows has no fork)
uv run python -m newerp.consumer                           # print order events (NEWERP_KAFKA__BOOTSTRAP_SERVERS set)
uv run python tools/check_live.py schema                   # real database vs db/reconstructed.sql (read-only)
uv run python tools/check_live.py integrations             # one read-only call to each service with a key
```

## Learning roadmap

The port itself is done with Claude Code, phase by phase. This list is the author's own learning plan for the stack.

- [ ] AsyncIO: event loop, coroutines, `await`, `asyncio.gather`, why `time.sleep` blocks everything
- [ ] FastAPI: path/query params, Pydantic models, dependencies, error handling (ProblemDetails like .NET)
- [ ] SQLAlchemy models for orders and stock (database-first, matching `db/*.sql`), 2–3 endpoints from the contract
- [ ] pytest: tests for own endpoints; Dockerfile for the API
- [ ] Celery: waybill or XML feed generation as a background task with retry
- [ ] Kafka: publish "order created" events (aiokafka)
- [ ] Django + DRF tutorial: compare with FastAPI
- [ ] Interview prep: tell the project story out loud in English
