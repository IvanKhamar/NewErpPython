# Porting plan

Source of truth: the .NET version (`F:\PROJECTS\NewErp\NewErp`). It already holds the functionality of the old Vidro
OpenCart CRM (`F:\PROJECTS\VIDRO\Vidro_OpenCart_WebSite`), cleaned up and covered by tests. Each phase ports one .NET
module: endpoints with the same paths, JSON names, status codes and ProblemDetails titles, plus its integration tests.
Background workers (`*Worker.cs`) become Celery beat tasks.

Decided 2026-10-30: the user asked Claude to port the whole system (not only the skeleton).

## Phases

| # | Module (.NET) | Status |
|---|---|---|
| 1 | Foundation: ProblemDetails errors, cookie auth, roles, `/auth`, `/users` | done |
| 2 | Orders: list, card, statuses, status change, new order, composition, customer risk | done |
| 3 | Staff: managers' bonuses | done |
| 4 | Stock: consumables, warehouses, purchase orders, reorder, stocktaking | done |
| 5 | Production: models, BOMs, sewn stock, sewing plan | done |
| 6 | Delivery: Nova Poshta waybills, reminders (stub + real client) | done (+ hourly tracking task in Celery beat) |
| 7 | International shipments: Nova Post International | done |
| 8 | Cashbox: Checkbox cashiers, shifts, receipts, auto close (Celery beat) | done |
| 9 | Payments: PrivatBank statement, LiqPay | done |
| 10 | SMS: SMSClub | done |
| 11 | Partners: reseller XML feeds | done |
| 12 | Catalog: product sort, products, storefront stock, cross-sell | done (storefront stock in phase 5) |
| 13 | Pre-orders (+ settle task, Celery beat) | done |
| 14 | Marketing: birthdays, coupons, bulk SMS, contacts export, gift vouchers | done |
| 15 | Returns, customers | done |
| 16 | Reports, revenue feed | done |
| 17 | Locations (warehouses) with Excel import/export | done in phase 4 |
| 18 | Competitor prices (+ daily check task) | done |
| 19 | Python only: `OrderCreated` and `OrderStatusChanged` events to Kafka (aiokafka), a reader in `newerp.consumer` | done |

## What is still missing (found 2026-11-01)

Compared with the .NET and Java versions:

| # | Item | Where it exists | Status |
|---|---|---|---|
| 20 | Dockerfile for the API + `api` service in docker-compose, image build in CI | Java `Dockerfile` | done |
| 21 | Demo data: port of `DevDataSeeder` (Faker), logins `admin` / `sklad` / `manager1…5` | .NET `tools/NewErp.DevSeed` | done |
| 22a | Web interface at `/app/` (Jinja2 + HTMX): login, menu by role, own password, dashboard, orders (list, card with status and manager, new order) | .NET Blazor `/app/`, Java Vaadin `newerp-web` | done |
| 22b | Web: warehouse (items, stock by warehouse, stocktaking, reorder, suppliers, purchase orders) and production (models, sewing plan, log) | the same | planned |
| 22c | Web: delivery, cashbox, bank payments with matching, returns, customers | the same | planned |
| 22d | Web: catalog and sort, pre-orders, marketing, partners, competitors, reports, users and bonuses | the same | planned |

Compared with the old PHP CRM (missing in all three new versions; from the .NET README, "Обмеження" and "Далі"):

| # | Item | Status |
|---|---|---|
| 23 | Nova Poshta courier delivery to an address (now only branches, parcel lockers and international) | planned |
| 24 | Sale prices (`dd_product_special`, its `percent` column) applied in a new order | planned |
| 25 | Coupon admin with excluded categories and products (now only birthday coupons) | planned |
| 26 | Low-stock alerts (now only the "below minimum" filter and the reorder report) | planned |
| 27 | Consumables for sewing up to the minimum stock (the plan covers only active orders) | planned |
| 28 | Automatic bank payment matching when there is one confident candidate | planned |
| 29 | Check against the real database (`SHOW CREATE TABLE` for the reconstructed `dd_*` tables) and the real Nova Poshta, Checkbox, PrivatBank, LiqPay and SMSClub keys | planned (needs access) |
| 30 | Marketing integrations: remarketing pixels, UTM report | planned (needs data the system does not collect) |
| 31 | Reports without data in the system: who is online, product views, reward points, taxes | planned (needs data the system does not collect) |
| 32 | "Different customers from one IP" in the customer risk check | planned (manager orders have no customer IP) |

Not planned on purpose (they stay in OpenCart or were replaced): storefront features (colors and variants, FAQ,
product questions, blog, reviews), customer account editing, LiqPay e-mail invoices (replaced by the payment link).

## Conventions (decided once, used everywhere)

- JSON: camelCase (`ApiModel` base), `Decimal` serialized as a number, naive local datetimes like .NET `DateTime`.
- Errors: `NotFoundError` → 404 "Не знайдено", `DomainError` → 400 "Порушено бізнес-правило",
  `ConflictError` → 409, unreadable body/validation → 400 "Некоректний запит". Body is ProblemDetails.
- Auth: signed cookie `newerp.auth` (HttpOnly, SameSite=Strict, 8 h sliding), security stamp checked against
  `erp_user_login` on each request. Password hashes use the ASP.NET Identity v3 format, so the same users log in
  to all three backends.
- Layers like .NET: `application` holds services and `Protocol` interfaces (ports), `infrastructure` implements them
  with SQLAlchemy, `api` holds routers. Shared wiring (session, clock, order service) lives in `api/deps.py`;
  wiring used by one module only sits next to its router.
- Tests: pytest against a throwaway MySQL database created from `db/*.sql` (env `NEWERP_TEST_DB`, like .NET).
