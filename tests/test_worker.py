from newerp import worker


def test_tasks_are_registered_and_nothing_is_scheduled_with_the_checkbox_stub():
    assert "cashbox.auto_close" in worker.app.tasks
    assert "cashbox-auto-close" not in worker.app.conf.beat_schedule


def test_pre_orders_are_settled_every_hour_by_default():
    assert "preorders.settle" in worker.app.tasks
    assert worker.app.conf.beat_schedule["preorders-settle"]["schedule"] == 60 * 60


def test_competitor_prices_are_checked_daily_at_seven():
    assert "competitors.check" in worker.app.tasks
    schedule = worker.app.conf.beat_schedule["competitors-check"]["schedule"]
    assert (schedule.hour, schedule.minute) == ({7}, {0})
    assert worker.daily_at("-") is None and worker.daily_at("7 ранку") is None
