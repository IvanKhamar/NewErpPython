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
| 22b | Web: warehouse (items, stock by warehouse, stocktaking, reorder, suppliers, purchase orders) and production (models with the cut and sewn operations, sewn by warehouse, sewing plan, log) | the same | done |
| 22b+ | Web: the production specifications (BOMs), product links and the model stocktaking | the same | done |
| 22c | Web: delivery (reminders, the waybill in the order card), cashbox, bank payments with matching, LiqPay links, returns, customers | the same | done |
| 22c+ | Web: waybills abroad (Nova Post International) | the same | done |
| 22d | Web: catalog and sort, pre-orders, marketing (birthdays, contact export, gift vouchers), partners, competitors, reports, users and bonuses | the same | done |

Compared with the old PHP CRM (missing in all three new versions; from the .NET README, "Обмеження" and "Далі"):

| # | Item | Status |
|---|---|---|
| 23 | Nova Poshta courier delivery to an address: `toDoor`, `streetRef`, `building`, `flat` in a new waybill, `GET /delivery/streets` (Python first: the .NET and Java versions do not have it yet) | done |
| 24 | Sale prices (`dd_product_special`, its `percent` column) in the product search of a new order: `price` is the sale price, `regularPrice` the old one (Python first; the table is added to `db/reconstructed.sql`, the .NET and Java copies need it too) | done |
| 25 | Coupon admin `/marketing/coupons` and `/app/marketing/coupons`: products and categories a coupon works on and never works on (the old site's `*_exclude` tables), usage history; a used coupon is only turned off (Python first; the tables are in `db/reconstructed.sql`) | done |
| 26 | Low-stock alerts: a daily SMS (Celery beat, `NEWERP_STOCK__ALERT_PHONES`, 08:00) about consumables that fell below the minimum since the last run (from the movement log, so no repeats), `POST /stock/alerts/low-stock`, and a "Мало на складі" panel on the home page (Python first) | done |
| 27 | Consumables for the sewing plan: `GET /production/plan/materials` (ported from .NET, it was missing here) with `scope=minimum` (sewing every model up to its minimal stock, spread by size shares; Python first) and `scope=all`, page `/app/production/plan/materials` | done |
| 28 | Automatic bank payment matching: after each import and through `POST /payments/bank/auto-match` (button on `/app/payments`), a payment is linked when exactly one order is confident (its number or phone in the purpose, the sum fits, no other payment); `autoLinked` in the import result (Python first) | done |
| 29 | Check against the real database and the real keys: `tools/check_live.py schema` compares every table and column of `db/reconstructed.sql` with the database (information_schema), `integrations` makes one read-only call to Nova Poshta, PrivatBank and LiqPay (Checkbox and SMSClub have none, so only "configured" is shown). Read-only, keys never printed | tool done; the owner runs it with the real access |
| 30 | UTM report `GET /reports/utm` (period, group, source/medium/campaign, by source; the "Звідки (UTM)" tab) and `GET /orders/{id}/remarketing` (tags and sent pixel events, a section of the order card) from the storefront module's `dd_remarketing_orders` (reconstructed). The pixels themselves stay in the storefront (Python first) | done |
| 31 | Reports on what the storefront records: `GET /reports/online` (dd_customer_online), `/reports/product-views` (dd_product.viewed against pieces sold), `/reports/rewards` (dd_customer_reward balances), `/reports/taxes` (tax lines of dd_order_total by period); tabs on `/app/reports` (Python first) | done |
| 32 | "Different customers from one IP" in the customer risk check: the IPs of the customer's earlier site orders (a manual order has none), 3+ different customers on one → `sharedIp` with up to 10 of them, shown under the risk (Python first) | done |

Security (found in the review of 2026-11-02; HSTS was added then):

| # | Item | Status |
|---|---|---|
| 33 | The login throttle keeps its counters in Redis outside development (`RedisLoginThrottle`), so all workers and containers count together; uvicorn takes the client address from X-Forwarded-For only from `FORWARDED_ALLOW_IPS` (`NEWERP_TRUSTED_PROXIES` in docker-compose) | done |
| 34 | Login CSRF: the login page puts a random token into a SameSite=Strict cookie and the form; a login POST without both, equal, is refused (double submit) | done |

Compared with the old PHP admin directly (2026-11-03, every custom `admin/controller/*` action and the admin menu;
missing in all three new versions, so every item is Python first):

| # | Item | Old admin | Status |
|---|---|---|---|
| 35 | Full order edit: customer, phone, e-mail, address, shipping and payment method (`GET`/`PUT /orders/{id}/customer`, the "Редагувати" section of the card); the rules of a new order; the audit log gets the changed field names, not the values | `sale/order` edit | done |
| 36 | Status-change checks: before "Чекає на відправку" (21) and "Відправлено" (22) the order must have a waybill and its SMS; problems come back as warnings | `sale/order` checkShipping | done (`GET /orders/{id}/shipping-check`; the card asks to confirm) |
| 37 | Shipping discrepancies report for the last N days (status and waybill state do not match) | `sale/order` getDiscrepancies | done (`GET /orders/discrepancies`, under the list) |
| 38 | Planned shipping date of an order | `sale/order` updateDateShipping | done (`PUT /orders/{id}/shipping-date`; `dd_order.date_shipping` in `db/reconstructed.sql`) |
| 39 | Printing: the invoice with its invoice number, and the shipping list | `sale/order` invoice, shipping, createInvoiceNo | done (`POST /orders/{id}/invoice`, `/app/orders/print/invoice|shipping?id=…`) |
| 40 | Reward points and partner commission of an order: add and take back | `sale/order` addReward, addCommission | done (`/orders/{id}/credits`, `reward`, `commission`; `dd_customer_transaction` reconstructed) |
| 41 | Shipping cost from Nova Poshta in a new order | `sale/quick_order` getShippingCost | done (`POST /orders/new/shipping-cost`; weight times quantity, the old site counted each product once) |
| 42 | Order delete: admin only, the status handlers give the stock back first, logged | `sale/order` delete | done (`DELETE /orders/{id}`; refused with a waybill, cheque, bank payment, return or gift voucher) |
| 43 | Product editing: create, edit (name, description, price, model, SKU, status, images, options and sizes, attributes, SEO), copy, enable and disable | `catalog/product` | done (`POST /catalog/products`, `GET /{id}/edit`, `PUT /{id}`, `POST /{id}/copy`; pages `/app/catalog/products/new` and `/{id}/edit`. Attributes, options other than sizes and downloads come with 49; picture upload is paths only for now) |
| 44 | Quick inline edit in the product list, and CSV import of products and prices | `catalog/product` quickUpdate, importProducts | done (price edit in the list with the row's version; `POST /catalog/products/import/preview` and `/import`, admin only: UTF-8 CSV by model or product_id, updates only, no stock and no picture downloads, which the old importer fetched from any URL) |
| 45 | Remove the "New" label by SKU after printing | `catalog/product` removeNewByPrint | done (`POST /catalog/products/remove-new`) |
| 46 | Mass price control (by category, manufacturer, percent or sum), with a preview before saving | `extension/module/price_control` | done (`POST /catalog/prices/preview` and `/apply`, admin only; price or sale percent by category, page `/app/catalog/prices`) |
| 47 | Label dictionary and search presets | `catalog/label`, `catalog/search_preset` | done (`/catalog/labels`, `/catalog/search-presets`; colours only #rrggbb, preset addresses only the shop or https; page `/app/catalog/labels`) |
| 48 | Categories: create, edit, delete, rebuild the paths | `catalog/category` | done (`/catalog/categories` with the tree path rebuilt on every save and `POST /catalog/categories/repair`; no loops; delete is admin only and refused with subcategories) |
| 49 | Manufacturers, filters and filter groups, attributes and attribute groups, options, downloads | `catalog/manufacturer`, `filter`, `attribute`, `attribute_group`, `option`, `download` | done (`/catalog/references/{filters|attributes|options}` with values, `/catalog/manufacturers`, `/catalog/downloads` (records only; the files stay in the storefront's storage), product attributes and downloads in the product form; anything products use cannot be deleted; page `/app/catalog/references`) |
| 50 | Content: information pages, FAQ, blog (categories and posts), the "Про нас" and NFT blocks | `catalog/information`, `catalog/faq`, `blog/*`, `extension/module/about`, `nft` | done (`/catalog/information`, `/catalog/faq`, `/catalog/blog/categories|posts`, `/catalog/blocks/about|nft` (module settings in dd_setting, other languages kept), `DELETE /catalog/content/{kind}/{id}`; page `/app/catalog/content`) |
| 51 | Reviews and product questions: moderation and answers | `catalog/review`, `catalog/question` | done (`/catalog/reviews`, `/catalog/questions` with pending/published; a question is published only with an answer) |
| 52 | Maintenance: rebuild the filters and size filters, recount pre-order quantities | `tool/console` | done (`POST /catalog/prices/size-filters`, admin only: every product gets exactly the size filters of its sizes; the pre-order recount was already `POST /preorders/settle` and its Celery task) |
| 53 | Customer card: edit, unlock, log in as the customer, balance transactions, reward points, IP list, history | `customer/customer` | done (`GET /customers/{id}/edit`, `PUT /customers/{id}` with addresses (no password), `/account`, `/transactions`, `/rewards`, `/unlock`; `POST /customers/{id}/login-link`, admin only: a one-time storefront link that a Celery task expires after `NEWERP_CUSTOMERS__LOGIN_LINK_MINUTES`) |
| 54 | Customer groups, customer approval, custom fields | `customer/customer_group`, `customer_approval`, `custom_field` | done (`/customers/groups`, `/customers/approvals`, `/customers/custom-fields`; groups and fields admin only; page `/app/customers/settings`) |
| 55 | monobank: payment check, payment list, matching with orders | `extension/payment/mono` | done (`POST /orders/{id}/mono-link`, `GET /orders/{id}/mono-invoices`, `POST /payments/monobank/check` and its Celery task; token in `NEWERP_MONOBANK__TOKEN`, test invoices without it; TLS checks and a timeout, which the old module turned off) |
| 56 | PrivatBank business cards: their payments next to the statement | `sale/privatbank` checkBusinessCards | done (already in phase 9: the business cards are more accounts in `NEWERP_PRIVAT_BANK__ACCOUNTS`, read with the same statement API; the old code had their tokens written into it) |
| 57 | Marketing tracking codes | `marketing/marketing` | done (`/marketing/tracking` with clicks, orders and their sum, the storefront link; page `/app/marketing/tracking`) |
| 58 | Gift vouchers: sending by e-mail, editing the voucher themes | `sale/voucher` send, `sale/voucher_theme` | done (`POST /marketing/vouchers/{id}/email` through SMTP from `NEWERP_MAIL__*`, `/marketing/vouchers/themes`; page `/app/marketing/vouchers/themes`) |
| 59 | Klaviyo: customer and subscriber exports, events | `extension/module/klaviyo` | done (`GET /marketing/klaviyo/export/{customers|subscribers|unsubscribers}.csv` and the module's switches, admin only; its API keys stay in the storefront module) |
| 60 | TikTok Business: catalog sync | `extension/module/tiktok_business` | done as a catalog feed: `GET /feeds/tiktok.csv` with `NEWERP_TIKTOK_FEED__TOKEN` (TikTok reads a catalog by URL; the storefront pixel stays in the module) |
| 61 | Instagram: image update | `extension/module/instagram` | done (`POST /marketing/instagram/update` and a Celery task: the token refreshed after 30 days, new posts into dd_instagram, pictures only from Instagram's CDN when `NEWERP_INSTAGRAM__IMAGE_DIR` is set) |
| 62 | Remarketing module settings (pixels, feeds) | `extension/module/remarketing` | done (`/marketing/remarketing/settings`, admin only: the module's 184 settings by group; tokens, passwords and the Telegram bot only as "set" or "not set", JavaScript counters, custom SQL and lists read-only; page `/app/marketing/remarketing`) |
| 63 | Settings page: the store settings in `dd_setting` | `setting/setting` | |
| 64 | User groups with per-page permissions | `user/user_permission` | |
| 65 | API users and keys | `user/api` | |
| 66 | Error log viewer: view, download, clear | `tool/log` | |
| 67 | Database backup download | `tool/backup` | |
| 68 | Uploaded files: list, download, delete | `tool/upload` | |
| 69 | Dictionaries: order statuses, return statuses, reasons and actions, stock statuses | `localisation/*` | |
| 70 | Banners and SEO URLs | `design/banner`, `design/seo_url` | |
| 71 | Standard reports: customer activity, searches, orders and transactions; products purchased; sales by order, coupon, return and shipping; marketing; statistics | `extension/report/*`, `report/statistics` | |

Security rules for items 35–71 (the old admin had none of them; each item is done only with its rules and tests):

- Every page and endpoint has a role policy, deny by default; every input is validated by a Pydantic model on the server.
- Rich text (descriptions, information pages, blog, FAQ, answers) is cleaned with an allowlist before saving (XSS).
- Uploads (CSV import, images): size limit, the type checked by content, the name made by the server, stored outside
  the static folder, never run.
- Secrets (monobank, Klaviyo, TikTok, business card tokens) only in `.env` / `NEWERP_*`; the settings page never shows
  or edits a secret.
- Money and prices (rewards, transactions, commission, mass prices, order delete) are logged: who, when, old and new.
- "Log in as the customer": admins only, a one-time signed token with a short life, logged.
- API keys are stored as hashes, shown once, can be revoked.
- Backup is download only, for admins; a restore stays a command-line step. The log viewer hides secrets.
- Per-page permissions: an admin cannot take away their own admin access.

Each item (or a small group) starts with a plan the user approves, like the earlier phases.

Not planned on purpose (they stay in OpenCart or were replaced): Ukrposhta, WayForPay and Justin (closed in 2022),
LiqPay e-mail invoices (replaced by the payment link), OpenCart engine settings (extensions, marketplace, themes,
layouts, translations, countries, zones, taxes, currencies, units), subscriptions (`recurring`).

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
