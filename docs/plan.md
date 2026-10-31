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
| 2 | Orders: list, card, statuses, status change, new order, composition, customer risk | done (vouchers → 14) |
| 3 | Staff: managers' bonuses | done |
| 4 | Stock: consumables, warehouses, purchase orders, reorder, stocktaking | done |
| 5 | Production: models, BOMs, sewn stock, sewing plan | done (pre-order settle → 13) |
| 6 | Delivery: Nova Poshta waybills, reminders (stub + real client) | done (tracking task → Celery beat later) |
| 7 | International shipments: Nova Post International | done |
| 8 | Cashbox: Checkbox cashiers, shifts, receipts, auto close (Celery beat) | done |
| 9 | Payments: PrivatBank statement, LiqPay | done |
| 10 | SMS: SMSClub | done |
| 11 | Partners: reseller XML feeds | |
| 12 | Catalog: product sort, products, storefront stock, cross-sell | |
| 13 | Pre-orders (+ settle task) | |
| 14 | Marketing: birthdays, coupons, contacts export | |
| 15 | Returns, customers | |
| 16 | Reports, revenue feed | |
| 17 | Locations (warehouses) with Excel import/export | done in phase 4 |
| 18 | Competitor prices (+ daily check task) | |

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
