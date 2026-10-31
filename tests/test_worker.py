"""Celery tasks and beat schedule. The schedule is built from explicit settings, so a local .env does not change it."""

from newerp import worker
from newerp.config import CheckboxSettings, CompetitorSettings, NovaPoshtaSettings, Settings


def schedule(**sections) -> dict[str, dict]:
    defaults = {"checkbox": CheckboxSettings(), "nova_poshta": NovaPoshtaSettings()}
    return worker.beat_schedule_for(Settings(_env_file=None, **(defaults | sections)))


def test_tasks_are_registered():
    for name in ("cashbox.auto_close", "preorders.settle", "competitors.check", "delivery.refresh_tracking"):
        assert name in worker.app.tasks


def test_with_stubs_cashbox_and_tracking_are_not_scheduled():
    assert {"cashbox-auto-close", "delivery-refresh-tracking"}.isdisjoint(schedule())


def test_pre_orders_are_settled_every_hour_by_default():
    assert schedule()["preorders-settle"]["schedule"] == 60 * 60


def test_competitor_prices_are_checked_daily_at_seven():
    check = schedule()["competitors-check"]["schedule"]
    assert (check.hour, check.minute) == ({7}, {0})
    assert "competitors-check" not in schedule(competitors=CompetitorSettings(check_time="-"))
    assert worker.daily_at("7 ранку") is None


def test_tracking_is_refreshed_every_hour_with_a_nova_poshta_key():
    with_key = NovaPoshtaSettings(api_key="fake-test-key")

    assert schedule(nova_poshta=with_key)["delivery-refresh-tracking"]["schedule"] == 60 * 60
    turned_off = with_key.model_copy(update={"tracking_interval_minutes": 0})
    assert "delivery-refresh-tracking" not in schedule(nova_poshta=turned_off)
