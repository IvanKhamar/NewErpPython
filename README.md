# NewErpPython

Python version of NewErp: an ERP for a sewing workshop and online clothing store. It works with the same MySQL
database and the same HTTP API contract as the [.NET](https://github.com/IvanKhamar/NewErp) and Java versions:
one system, three backends, one contract.

**Status:** the whole .NET system is ported (phases in [docs/plan.md](docs/plan.md)): 262 tests, CI with ruff,
import-linter, pytest and gitleaks.

## What it does

| Area | Endpoints |
|---|---|
| Auth and users | `/auth` (cookie login, the same password hashes as ASP.NET Identity), `/users`, `/staff` |
| Orders | `/orders`: list, card, new order, status changes with handlers, managers, customer risk |
| Warehouse | `/stock`, `/locations` (Excel import and export) |
| Production | `/production`: sewing orders, transfers, storefront stock |
| Delivery | `/orders/{id}/shipment`, `/delivery`: Nova Poshta waybills, tracking, pickup reminders, international |
| Money | `/cashbox` (Checkbox fiscal receipts, shifts), `/payments` (PrivatBank, LiqPay) |
| Catalog | `/catalog/products`, `/catalog/sort` (sort by sales, pins), `/preorders` |
| Marketing | `/marketing` (birthday coupons, contact export), gift vouchers, `/sms` |
| After-sales | `/returns`, `/customers` |
| Partners | `/partners` and their XML feed `/feeds/{code}.xml` |
| Reports | `/reports` (sales, products, statuses, dashboard), revenue feed `/feeds/revenue.json` |
| Competitors | `/competitors`: product pages on competitors' sites, price checks, comparison |

Celery beat tasks: closing cashbox shifts, settling pre-orders, the daily competitor price check (07:00) and the
hourly Nova Poshta tracking refresh. Without API keys every outside service uses a stub, so local development
never reaches a real service.

## Stack

Python 3.13 · uv · FastAPI · Pydantic v2 · SQLAlchemy 2 (async, aiomysql) · MySQL 8 · Celery + Redis · pytest · ruff · import-linter

## Structure

```
src/newerp/
  domain/          business rules in plain Python (no FastAPI, no database)
  application/     use cases and the interfaces they need
  infrastructure/  SQLAlchemy (db.py), Celery tasks, external clients
  api/             FastAPI routers and request/response schemas
  config.py        settings from env vars NEWERP_* and .env
  main.py          API composition root (FastAPI app)
  worker.py        worker composition root (Celery app)
tests/             pytest
db/                the same SQL as the .NET repo: OpenCart tables, reconstructed tables, erp_* tables
```

Dependencies point inward only (`api → infrastructure → application → domain`); `uv run lint-imports` checks it.

| .NET | Java | Python here |
|---|---|---|
| .csproj, NuGet | pom.xml, Maven | pyproject.toml, uv |
| ASP.NET Core Minimal API | Spring Web MVC | FastAPI |
| Built-in DI | Spring DI | `Depends(...)` |
| Data annotations / FluentValidation | Bean Validation | Pydantic models |
| EF Core, `DbContext` | Spring Data JPA | SQLAlchemy 2, `AsyncSession` |
| `appsettings.json` + user-secrets | `application.yml` | pydantic-settings + `.env` |
| `BackgroundService` / Hangfire | `@Async` / `@Scheduled` | Celery |
| Scalar (`/scalar`) | Swagger UI | Swagger UI (`/docs`) |
| xUnit + WebApplicationFactory | JUnit 5 + `@SpringBootTest` | pytest + `TestClient` |

## Run on Windows

1. Install [uv](https://docs.astral.sh/uv/) (it also installs Python itself):
   `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`, then `uv python install 3.13`.
2. Install Docker Desktop and start the infrastructure: `docker compose up -d`.
   MySQL listens on port 3307 (the local MySQL80 keeps 3306) and loads `db/*.sql` on the first start.
3. `copy .env.example .env` and change the values if needed.
4. `uv sync`, then `uv run fastapi dev src/newerp/main.py`. Open http://localhost:8000/health and /docs.

```
uv run pytest                                              # tests (need the MySQL from docker compose)
uv run ruff check . ; uv run ruff format --check .         # lint and format check
uv run lint-imports                                        # layer check
uv run celery -A newerp.worker worker --beat --pool=solo   # worker with the scheduler (--pool=solo: Windows has no fork)
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
