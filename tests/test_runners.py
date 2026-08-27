from __future__ import annotations

from typing import Any

import pytest
from django.core.management import call_command

from django_exec_tool.models import Run, RunStatus
from django_exec_tool.runners.celery import CeleryRunner
from django_exec_tool.runners.thread import ThreadRunner

from .conftest import wait_finished

pytestmark = pytest.mark.django_db(transaction=True)


def test_thread_runner_executes_the_run(operator: Any) -> None:
    run = Run.objects.create(
        command="demo_slow", argv=["--seconds", "1"], status=RunStatus.PENDING, timeout=60
    )
    ThreadRunner().enqueue(run)
    assert wait_finished(run).status == RunStatus.SUCCEEDED


def test_celery_runner_dispatches_the_task(monkeypatch: Any, override_spec: Any) -> None:
    from django_exec_tool import tasks

    # Без объявленной очереди задача уходит в очередь по умолчанию.
    override_spec("demo_slow")
    sent: dict = {}

    class Result:
        id = "task-1"

    def fake_apply_async(args: list, **options: Any) -> Result:
        sent["args"] = args
        sent["options"] = options
        return Result()

    monkeypatch.setattr(tasks.execute_run, "apply_async", fake_apply_async)
    run = Run.objects.create(command="demo_slow", argv=[], status=RunStatus.PENDING)
    CeleryRunner().enqueue(run)
    run.refresh_from_db()
    assert sent["args"] == [str(run.pk)]
    assert sent["options"] == {}
    assert run.runner_ref == "celery:task-1"


def test_celery_runner_uses_the_declared_queue(monkeypatch: Any, override_spec: Any) -> None:
    from django_exec_tool import tasks

    override_spec("demo_slow", queue="ops")
    captured: dict = {}

    class Result:
        id = "task-2"

    monkeypatch.setattr(
        tasks.execute_run,
        "apply_async",
        lambda args, **options: (captured.update(options), Result())[1],
    )
    run = Run.objects.create(command="demo_slow", argv=[], status=RunStatus.PENDING)
    CeleryRunner().enqueue(run)
    assert captured == {"queue": "ops"}


def test_celery_task_delegates_to_the_executor(monkeypatch: Any) -> None:
    from django_exec_tool import tasks

    monkeypatch.setattr(tasks, "execute", lambda run_id: f"executed:{run_id}")
    assert tasks.execute_run("abc") == "executed:abc"


def test_reap_management_command(settings: Any, capsys: Any) -> None:
    from datetime import timedelta

    from django.utils import timezone

    settings.EXEC_TOOL = {**settings.EXEC_TOOL, "HEARTBEAT_INTERVAL": 1, "HEARTBEAT_MISS_FACTOR": 2}
    Run.objects.create(
        command="demo_slow",
        status=RunStatus.RUNNING,
        heartbeat_at=timezone.now() - timedelta(minutes=5),
    )
    call_command("exec_tool_reap")
    assert "Помечено потерянными: 1" in capsys.readouterr().out
