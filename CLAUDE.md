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
- Where a step needs the user (real access, keys, a decision), lead them through it one step at a time: what to do,
  where, and what they should see.

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
- Features: the goal is every feature of all four versions. The old PHP project
  (`F:\PROJECTS\VIDRO\Vidro_OpenCart_WebSite`, read only, never open its `config.php`) is probably still the fullest,
  so check it first when looking for what a module should do.
- Scope: this is an ERP. It covers the admin side, with pages a manager or another employee can open and really work
  in. The shop's storefront is out of scope.

## Staying in sync with .NET and Java

- Same features, database and API contract, so each web interface works with any of the three APIs.
- A feature built here first gets an entry in `docs/from-python.md` of both other repos: contract, security rules,
  tables and tests to port. Item 78 of `docs/plan.md` tracks the port.
- A schema change goes into `db/*.sql` of all three repos.

## Database

- Database-first, like the other versions.
- `dd_*` tables may be changed, structure included, when a feature needs it (decided 2026-11-06: `dd_` is only the
  prefix of the old project, and "never change them" was a leftover). Record the change in `db/*.sql`. No real shop
  runs on these tables.
- New own tables only with the `erp_` prefix, and they go into all repos.
- Tables and columns that are not in `db/schema.sql` are not invented silently: each reconstruction is marked as ours
  in `db/reconstructed.sql`.
- Two MySQL servers exist: Docker on port 3307 is the one this version uses and the fullest (2026-11-06: 153 tables);
  Windows `MySQL80` is used by .NET, Java and the dbhub MCP (86 tables). Moving them to Docker is planned. Say which
  server a database step is for. dbhub does not see the Docker one; read it with `docker compose exec`.
- Secrets only in the git-ignored `.env` (see `.env.example`) or env vars `NEWERP_*`.
- Only invented data; no real customer data.

## Permissions

They are in `F:\PROJECTS\CLAUDE.md` (outside this repo, for all projects): do not ask, do anything that does not go
against security or common sense.

## Security (the repository is public)

Secrets:

- Keys, tokens, passwords and connection strings live only in `.env` (local) or environment variables (server). Never
  in code, tests, README, commits, PR texts, logs or error messages. Never print them in command output; do not read
  `.env` values into the chat.
- Tests and examples use obviously fake values (`test-token`, `secret-key`).
- Check the diff for secrets before each commit; CI runs gitleaks. A secret that reached a commit counts as
  compromised: revoke or replace it in the service first, then clean the history.
- No personal data of real customers anywhere.

Code:

- SQL only through SQLAlchemy or bound parameters. Raw SQL built from strings only with numbers.
- A new endpoint requires a role. Anonymous access only on purpose, with a comment; if it serves data, a token protects
  it, compared with `hmac.compare_digest`.
- A 500 error gives no exception details; a `DomainError` text is a message for people, with no internals.
- Login: `LoginThrottle` limits failed tries (429), and an unknown login takes as long as a known one.
- Outgoing HTTP requests to addresses a user typed go only through `HttpPageFetcher` (SSRF protection).
- Jinja2: keep autoescape, no `|safe` on user data. Security headers and the CSP are in `api/security.py`; a new
  outside address (script, font, API) goes there first.
- Dependabot updates dependencies; an update that fixes a vulnerability comes first.

## Commands

```
docker compose up -d                       # MySQL (port 3307) + Redis
uv sync                                    # create .venv and install dependencies
uv run fastapi dev src/newerp/main.py      # run the API; /health, /docs
uv run pytest                              # tests
uv run ruff check . ; uv run lint-imports  # lint + layer check
```

Push only after all CI steps (`ruff check`, `ruff format --check`, `lint-imports`, `pytest`) pass locally, each
exit code checked: a failed CI run e-mails the user. Report problems in the chat instead.
