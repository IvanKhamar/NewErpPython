# NewErpPython

Python rewrite of NewErp (.NET: `F:\PROJECTS\NewErp\NewErp`, Java: `F:\PROJECTS\NewErpJava\NewErpJava`).
Same business, same MySQL database `newerp_dev`, same HTTP API contract. The goal is a learning project and a
portfolio piece for a Python backend job: FastAPI, AsyncIO, SQLAlchemy, Celery, Kafka, Docker, pytest.

## Working with the user

Since 2026-10-30 Claude ports the whole system from the .NET version (the user asked for it); the phases and
their status are in `docs/plan.md`. The user must still be able to explain the code in an interview, so:

- Keep the code plain and idiomatic; explain each new Python concept in 1–2 sentences and map it to C#/.NET
  (in commit messages or when asked).
- When the user writes code themselves, review it: name the problem and the hint first, show the fix when asked.

## Stack

Python 3.13, uv, FastAPI, Pydantic v2 + pydantic-settings, SQLAlchemy 2 (async) + aiomysql, Celery + Redis,
pytest, ruff, import-linter. Later: aiokafka.

## Structure and code rules

- `src/newerp/` layers: `api → infrastructure → application → domain`. Dependencies point inward only;
  `uv run lint-imports` checks it (import-linter, like ArchUnit in the Java version).
- `main.py` (FastAPI app) and `worker.py` (Celery app) are the composition roots, like `Program.cs`.
- Domain is plain Python: no FastAPI, no SQLAlchemy.
- Keep the API contract identical to the .NET version: paths, JSON field names, status codes, ProblemDetails
  titles. When in doubt, read the matching .NET endpoint.

## Database

- Database-first, like the other versions. `db/*.sql` are copies from the .NET repo; keep them identical.
- Never change `dd_*` tables from code. Own tables only with the `erp_` prefix, and they go into all repos.
- Secrets only in the git-ignored `.env` (see `.env.example`) or env vars `NEWERP_*`.
- Only invented data; no real customer data.

## Commands

```
docker compose up -d                       # MySQL (port 3307) + Redis
uv sync                                    # create .venv and install dependencies
uv run fastapi dev src/newerp/main.py      # run the API; /health, /docs
uv run pytest                              # tests
uv run ruff check . ; uv run lint-imports  # lint + layer check
```
