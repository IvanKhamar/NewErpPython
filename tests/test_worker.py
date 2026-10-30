from newerp import worker


def test_tasks_are_registered_and_nothing_is_scheduled_with_the_checkbox_stub():
    assert "cashbox.auto_close" in worker.app.tasks
    assert "cashbox-auto-close" not in worker.app.conf.beat_schedule
