from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from django_exec_tool.models import (
    CommandState,
    EnqueueLock,
    Run,
    RunOutputChunk,
    RunStatus,
    StopMode,
)
from django_exec_tool.registry import registry
from django_exec_tool.services import (
    LaunchRejected,
    launch,
    reap_lost_runs,
    request_stop,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def quiet_runner(monkeypatch: Any, settings: Any) -> list:
    """Раннер-заглушка: тесты допуска не должны плодить процессы."""
    started: list = []

    class DummyRunner:
        def enqueue(self, run: Run) -> None:
            started.append(run)

    settings.EXEC_TOOL = {**settings.EXEC_TOOL, "RUNNER": DummyRunner}
    return started


def make_run(**kwargs: Any) -> Run:
    defaults = {"command": "demo_slow", "status": RunStatus.RUNNING}
    defaults.update(kwargs)
    return Run.objects.create(**defaults)


def test_str_representations() -> None:
    run = make_run()
    assert "demo_slow" in str(run)
    assert str(CommandState(name="x", enabled=True)).endswith("(enabled)")
    assert str(CommandState(name="x", enabled=False)).endswith("(disabled)")
    assert str(EnqueueLock(key="enqueue")) == "enqueue"
    chunk = RunOutputChunk.objects.create(run=run, seq=1, text="a")
    assert str(chunk).endswith("#1")


def test_status_helpers() -> None:
    assert set(RunStatus.active()) == {"pending", "running"}
    assert "unknown" in RunStatus.final()


def test_duration_and_command_line() -> None:
    run = make_run(argv=["--seconds", "1"])
    assert run.duration is None
    run.started_at = timezone.now() - timedelta(seconds=5)
    assert run.duration is not None and run.duration.total_seconds() >= 5
    run.finished_at = run.started_at + timedelta(seconds=2)
    assert run.duration == timedelta(seconds=2)
    assert run.command_line == "manage.py demo_slow --seconds 1"
    assert run.status_display == "Выполняется"


def test_spec_lookup_survives_unregistered_command() -> None:
    run = make_run(command="gone_forever")
    assert run.spec_or_none() is None
    assert make_run(command="demo_slow").spec_or_none() is not None
    assert make_run(command="demo_slow").spec().name == "demo_slow"


def test_launch_requires_permission(outsider: Any) -> None:
    with pytest.raises(PermissionDenied):
        launch(outsider, registry.get("demo_slow"), {"seconds": 1})


def test_launch_creates_run_and_enqueues(
    operator: Any, quiet_runner: list, django_capture_on_commit_callbacks: Any
) -> None:
    # Раннер дёргается через on_commit, поэтому колбэки надо прогнать вручную.
    with django_capture_on_commit_callbacks(execute=True):
        run = launch(operator, registry.get("demo_slow"), {"seconds": 1}, reason="тикет-1")
    assert run.status == RunStatus.PENDING
    assert run.argv == ["--seconds", "1"]
    assert run.requested_by_repr == "operator"
    assert run.reason == "тикет-1"
    assert quiet_runner == [run]
    assert EnqueueLock.objects.count() == 1


def test_disabled_command_is_rejected(operator: Any, quiet_runner: list) -> None:
    CommandState.objects.create(name="demo_slow", enabled=False)
    with pytest.raises(LaunchRejected):
        launch(operator, registry.get("demo_slow"), {"seconds": 1})


def test_max_parallel_per_command(operator: Any, quiet_runner: list) -> None:
    launch(operator, registry.get("demo_slow"), {"seconds": 1})
    with pytest.raises(LaunchRejected):
        launch(operator, registry.get("demo_slow"), {"seconds": 1})


def test_global_parallel_limit(operator: Any, quiet_runner: list, settings: Any) -> None:
    settings.EXEC_TOOL = {**settings.EXEC_TOOL, "MAX_PARALLEL_RUNS": 1}
    launch(operator, registry.get("demo_slow"), {"seconds": 1})
    with pytest.raises(LaunchRejected):
        launch(operator, registry.get("demo_report"), {"target": "a"})


def test_lock_key_blocks_other_commands(operator: Any, quiet_runner: list) -> None:
    make_run(command="other_command", lock_key="orders")
    with pytest.raises(LaunchRejected):
        launch(operator, registry.get("demo_report"), {"target": "orders"})


def test_request_stop_marks_pending_run_canceled(operator: Any) -> None:
    run = make_run(status=RunStatus.PENDING)
    run = request_stop(run, operator)
    assert run.status == RunStatus.CANCELED
    assert run.stop_requested_at is not None


def test_request_stop_records_intent_for_running_run(operator: Any) -> None:
    run = make_run()
    run = request_stop(run, operator, StopMode.HARD)
    assert run.status == RunStatus.RUNNING
    assert run.stop_mode == StopMode.HARD
    assert run.stop_requested_by == operator


def test_request_stop_on_finished_run(operator: Any) -> None:
    run = make_run(status=RunStatus.SUCCEEDED)
    with pytest.raises(LaunchRejected):
        request_stop(run, operator)


def test_request_stop_requires_permission(outsider: Any) -> None:
    run = make_run()
    with pytest.raises(PermissionDenied):
        request_stop(run, outsider)


def test_reap_marks_stale_runs_unknown(settings: Any) -> None:
    settings.EXEC_TOOL = {**settings.EXEC_TOOL, "HEARTBEAT_INTERVAL": 1, "HEARTBEAT_MISS_FACTOR": 2}
    fresh = make_run(heartbeat_at=timezone.now())
    lost = make_run(heartbeat_at=timezone.now() - timedelta(seconds=30))
    assert reap_lost_runs() == 1
    fresh.refresh_from_db()
    lost.refresh_from_db()
    assert fresh.status == RunStatus.RUNNING
    # Именно unknown: доработала команда или нет — мы не знаем.
    assert lost.status == RunStatus.UNKNOWN
    assert lost.error


def test_stop_requested_flag() -> None:
    run = make_run()
    assert run.stop_requested is False
    run.stop_requested_at = timezone.now()
    assert run.stop_requested is True
