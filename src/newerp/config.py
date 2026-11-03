"""App settings from env vars `NEWERP_*` and the git-ignored `.env` (like appsettings.json + user-secrets)."""

from decimal import Decimal
from functools import lru_cache

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class BonusSettings(BaseModel):
    """Managers' bonus: a percent of orders in the counted statuses."""

    percent: Decimal = Decimal(1)
    counted_status_ids: list[int] = [22, 20]


class StockSettings(BaseModel):
    # Reorder up to this many minimums: 2 means "bring the quantity up to 2 × minimum".
    reorder_target_factor: Decimal = Decimal(2)
    # Low-stock alerts: phones for the daily SMS (env NEWERP_STOCK__ALERT_PHONES as JSON) and when it goes ("-": never).
    alert_phones: list[str] = []
    alert_time: str = "08:00"


class ProductionSettings(BaseModel):
    # Statuses in which an order takes sewn pieces from stock: entering writes off, leaving returns.
    order_subtract_status_ids: list[int] = [18, 20, 21, 22, 23, 25]
    # Statuses of active orders the sewing plan is counted for (not yet ready to ship).
    plan_status_ids: list[int] = [1, 17, 18, 23, 24, 25]


class NovaPoshtaSender(BaseModel):
    """A sender (for example, a sole proprietor) with its own API key; the refs come from the API or the cabinet."""

    id: str = ""  # short id stored in erp_shipment_sender.sender_id; do not change after the first waybill
    name: str = ""
    api_key: SecretStr = SecretStr("")
    counterparty_ref: str = ""
    contact_ref: str = ""
    phone: str = ""  # 380XXXXXXXXX
    is_default: bool = False


class NovaPoshtaDropoffPoint(BaseModel):
    """The branch parcels are taken to (city and address refs)."""

    id: str = ""
    name: str = ""
    city_ref: str = ""
    address_ref: str = ""
    is_default: bool = False


class NovaPoshtaSettings(BaseModel):
    """API keys only in env vars or .env (NEWERP_NOVA_POSHTA__SENDERS as JSON), never in committed files."""

    api_key: SecretStr | None = None  # for tracking and the directory; else the default sender's key
    base_url: str = "https://api.novaposhta.ua/v2.0/json/"
    print_base_url: str = "https://my.novaposhta.ua/"
    cargo_description: str = "Vidro"
    # Local development only: without a key the stub "creates" test waybills starting with 00.
    fake_documents: bool = False
    senders: list[NovaPoshtaSender] = []
    dropoff_points: list[NovaPoshtaDropoffPoint] = []
    # How often the worker refreshes the statuses of open waybills; 0: never (the refresh endpoint still works).
    tracking_interval_minutes: int = 60

    @property
    def tracking_key(self) -> str | None:
        if self.api_key and self.api_key.get_secret_value().strip():
            return self.api_key.get_secret_value()
        sender = next((s for s in self.senders if s.is_default), None) or next(iter(self.senders), None)
        key = sender.api_key.get_secret_value() if sender else ""
        return key.strip() or None


class InternationalSender(BaseModel):
    """The shop's company as the sender. With a company TIN the API also needs the registration address: this one."""

    company_tin: str = ""
    company_name: str = ""
    name: str = ""
    phone: str = ""  # 380XXXXXXXXX
    email: str = ""
    city: str = ""
    street: str = ""
    building: str = ""
    flat: str = ""
    post_code: str = ""


class NovaPostInternationalSettings(BaseModel):
    api_key: SecretStr = SecretStr("")
    sandbox: bool = False  # True: api-stage.novapost.com (test waybills)
    base_url: str | None = None
    # Local development only: without a key the stub "creates" test waybills (numbers start with TEST).
    fake_documents: bool = False
    sender: InternationalSender = InternationalSender()

    @property
    def configured(self) -> bool:
        return bool(self.api_key.get_secret_value().strip())

    @property
    def url(self) -> str:
        if self.base_url and self.base_url.strip():
            return self.base_url.strip().rstrip("/")
        return "https://api-stage.novapost.com/v.1.0" if self.sandbox else "https://api.novapost.com/v.1.0"


class CheckboxCashier(BaseModel):
    """A cashier's Checkbox keys; cashier_id is dd_cashbox_user.user_id."""

    cashier_id: int = 0
    login: str = ""
    password: SecretStr = SecretStr("")
    license_key: SecretStr = SecretStr("")


class CheckboxSettings(BaseModel):
    base_url: str = "https://api.checkbox.in.ua"
    client_name: str = "NewErp"
    client_version: str = "1.0"
    # Without keys the stub "fiscalizes" test receipts in memory (development and tests only).
    fake_receipts: bool = False
    cashiers: list[CheckboxCashier] = []
    # How often the background task checks open shifts (minutes); 0: never (POST /cashbox/shifts/auto-close).
    auto_close_interval_minutes: int = 15


class PrivatBankAccount(BaseModel):
    """An "Автоклієнт" account: its id and token from the PrivatBank business cabinet."""

    id: str = ""
    token: SecretStr = SecretStr("")


class PrivatBankSettings(BaseModel):
    base_url: str = "https://acp.privatbank.ua"
    accounts: list[PrivatBankAccount] = []

    @property
    def is_configured(self) -> bool:
        return any(a.id and a.token.get_secret_value() for a in self.accounts)


class MonobankSettings(BaseModel):
    """monobank acquiring (plata by mono): the merchant token only in .env (NEWERP_MONOBANK__TOKEN). Without it a stub
    makes test invoices that nobody can pay."""

    token: SecretStr | None = None
    base_url: str = "https://api.monobank.ua"
    redirect_url: str = ""  # where the customer returns after paying (the storefront)
    # How often unfinished invoices of the last check_days are asked about; 0: only by hand.
    check_interval_minutes: int = 30
    check_days: int = 3

    @property
    def is_configured(self) -> bool:
        return self.token is not None and bool(self.token.get_secret_value().strip())


class LiqPaySettings(BaseModel):
    base_url: str = "https://www.liqpay.ua"
    public_key: str = ""
    private_key: SecretStr = SecretStr("")

    @property
    def is_configured(self) -> bool:
        return bool(self.public_key and self.private_key.get_secret_value())


class SmsClubSettings(BaseModel):
    base_url: str = "https://im.smsclub.mobi"
    token: SecretStr = SecretStr("")  # without a token the stub is used
    sender: str = "Vidro.ua"
    only_phones: list[str] = []  # when set, SMS go only to these numbers (testing on real phones)


class PartnerFeedSettings(BaseModel):
    """The shop in the header of partners' XML feeds; store_url is the base of picture links."""

    shop_name: str = "Vidro"
    company: str = ""
    store_url: str = ""


class ProductSortSettings(BaseModel):
    """The storefront order by sales; the values are the old site's (see ProductSortOptions)."""

    counted_status_ids: list[int] = [17, 18, 21, 22, 23, 24, 25]
    excluded_user_ids: list[int] = [13]
    excluded_order_markers: list[str] = ["B2B"]
    excluded_comment_markers: list[str] = ["б2б", "магаз", "партнер"]
    color_filter_ids: list[int] = [156, 157, 158, 159]
    tag_filter_group_id: int = 7
    new_label_name: str = "Новинка"
    default_days: int = 90


class CustomerSettings(BaseModel):
    """Logging in as a customer opens the storefront: its address, and how long the one-time link works."""

    store_url: str = ""  # https://vidro.example/ (the storefront's base address)
    login_link_minutes: int = 5


class PreOrderSettings(BaseModel):
    # How often what was sold ahead is taken off the limits (minutes); 0: only by hand (POST /preorders/settle).
    settle_interval_minutes: int = 60


class MarketingSettings(BaseModel):
    birthday_discount_percent: Decimal = Decimal(20)
    birthday_coupon_days: int = 30
    pickup_city: str = "Львів"
    pickup_method_marker: str = "амовив"
    bulk_sms_delay_milliseconds: int = 300


class MailSettings(BaseModel):
    """SMTP for e-mails (gift vouchers). The password only in .env; without a host e-mails are not sent."""

    host: str = ""
    port: int = 587
    username: str = ""
    password: SecretStr = SecretStr("")
    from_address: str = ""
    starttls: bool = True

    @property
    def is_configured(self) -> bool:
        return bool(self.host.strip() and self.from_address.strip())


class VoucherSettings(BaseModel):
    # A voucher bought in an order is usable once the order is in one of these statuses.
    paid_status_ids: list[int] = [22, 20]
    sms_text: str = (
        "Вам подарунковий сертифікат Vidro на {amount} грн. Код: {code}. Назвіть його менеджеру або введіть на сайті."
    )


class TikTokFeedSettings(BaseModel):
    """The product feed TikTok Shop and Ads read (a catalog by URL). The token only in .env; without it the feed is
    off. Links and pictures use the storefront address (NEWERP_CUSTOMERS__STORE_URL)."""

    token: SecretStr = SecretStr("")
    brand: str = "Vidro"

    @property
    def enabled(self) -> bool:
        return bool(self.token.get_secret_value().strip())


class AdminToolsSettings(BaseModel):
    """Files of the storefront the admin tools read, when the ERP runs where it can reach them (a shared volume):
    OpenCart's error log and its folder of files customers uploaded. Empty: that tool is off."""

    error_log: str = ""  # /var/www/storage/logs/error.log
    upload_dir: str = ""  # /var/www/storage/upload


class InstagramSettings(BaseModel):
    """The storefront's Instagram block. The access token stays where the old module keeps it (dd_setting), because
    it has to be refreshed and saved every month. image_dir: the storefront's image folder, when the ERP can write
    there; without it only the records are saved."""

    image_dir: str = ""
    update_interval_hours: int = 6  # 0: only by hand


class RevenueFeedSettings(BaseModel):
    """The revenue feed for an outside dashboard. The token goes only into .env or NEWERP_REVENUE_FEED__TOKEN;
    without it the feed is off."""

    token: SecretStr = SecretStr("")  # what the dashboard sends in the X-Feed-Token header (or ?token=)
    max_days: int = 366  # the longest daily series one request can ask for

    @property
    def enabled(self) -> bool:
        return bool(self.token.get_secret_value().strip())


class CompetitorSettings(BaseModel):
    # When to read competitor prices every day, "HH:mm" in the server's time; "-": do not check by itself.
    check_time: str = "07:00"


class KafkaSettings(BaseModel):
    """Where order events go. Without bootstrap servers the events are only logged (local development, tests)."""

    bootstrap_servers: str = ""  # "localhost:9092" for the Kafka from docker-compose
    topic: str = "newerp.orders"

    @property
    def enabled(self) -> bool:
        return bool(self.bootstrap_servers.strip())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NEWERP_", env_file=".env", extra="ignore", env_nested_delimiter="__")

    # Development, Testing or Production (like ASPNETCORE_ENVIRONMENT).
    environment: str = "Development"

    db_host: str = "localhost"
    db_port: int = 3307
    db_name: str = "newerp_dev"
    db_user: str = "newerp"
    db_password: SecretStr = SecretStr("")

    redis_url: str = "redis://localhost:6379/0"

    # Key that signs the auth cookie. Without it a random key is made at start, so sessions end on restart.
    auth_secret: SecretStr | None = None

    # Language of the OpenCart lookups (dd_order_status etc.). The real database has every name with language_id 3.
    language_id: int = 3

    # Module settings, like the sections of appsettings.json. Env example: NEWERP_BONUS__PERCENT=2.
    bonus: BonusSettings = BonusSettings()
    stock: StockSettings = StockSettings()
    production: ProductionSettings = ProductionSettings()
    nova_poshta: NovaPoshtaSettings = NovaPoshtaSettings()
    nova_post_international: NovaPostInternationalSettings = NovaPostInternationalSettings()
    checkbox: CheckboxSettings = CheckboxSettings()
    privat_bank: PrivatBankSettings = PrivatBankSettings()
    liq_pay: LiqPaySettings = LiqPaySettings()
    monobank: MonobankSettings = MonobankSettings()
    sms_club: SmsClubSettings = SmsClubSettings()
    partner_feed: PartnerFeedSettings = PartnerFeedSettings()
    product_sort: ProductSortSettings = ProductSortSettings()
    pre_orders: PreOrderSettings = PreOrderSettings()
    customers: CustomerSettings = CustomerSettings()
    marketing: MarketingSettings = MarketingSettings()
    vouchers: VoucherSettings = VoucherSettings()
    mail: MailSettings = MailSettings()
    revenue_feed: RevenueFeedSettings = RevenueFeedSettings()
    tiktok_feed: TikTokFeedSettings = TikTokFeedSettings()
    instagram: InstagramSettings = InstagramSettings()
    admin_tools: AdminToolsSettings = AdminToolsSettings()
    competitors: CompetitorSettings = CompetitorSettings()
    kafka: KafkaSettings = KafkaSettings()

    @property
    def is_development(self) -> bool:
        return self.environment in ("Development", "Testing")

    @property
    def database_url(self) -> URL:
        return URL.create(
            "mysql+aiomysql",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
            query={"charset": "utf8mb4"},
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
