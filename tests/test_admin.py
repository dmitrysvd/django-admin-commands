from __future__ import annotations

from typing import Any, cast

import pytest
from django.contrib import admin as django_admin
from django.contrib.auth.models import User
from django.test import RequestFactory
from django.urls import reverse

from django_admin_commands.models import CommandState, Run, RunOutputChunk, RunStatus, StopMode
from django_admin_commands.registry import registry

pytestmark = pytest.mark.django_db


@pytest.fixture
def quiet_runner(settings: Any) -> list:
    started: list = []

    class DummyRunner:
        def enqueue(self, run: Run) -> None:
            started.append(run)

    settings.ADMIN_COMMANDS = {**settings.ADMIN_COMMANDS, "RUNNER": DummyRunner}
    return started


@pytest.fixture
def client_operator(client: Any, operator: User) -> Any:
    client.force_login(operator)
    return client


def make_run(**kwargs: Any) -> Run:
    defaults = {"command": "demo_slow", "status": RunStatus.RUNNING, "argv": []}
    defaults.update(kwargs)
    return Run.objects.create(**defaults)


def test_command_list_is_rendered(client_operator: Any) -> None:
    response = client_operator.get(reverse("admin:admin_commands_list"))
    assert response.status_code == 200
    assert b"demo_slow" in response.content


def test_command_list_denied_without_permission(client: Any, outsider: User) -> None:
    client.force_login(outsider)
    response = client.get(reverse("admin:admin_commands_list"))
    assert response.status_code == 403


def test_launch_form_is_rendered(client_operator: Any) -> None:
    response = client_operator.get(reverse("admin:admin_commands_launch", args=["demo_slow"]))
    assert response.status_code == 200
    assert b"arg_seconds" in response.content


def test_launch_of_unregistered_command_is_denied(client_operator: Any) -> None:
    response = client_operator.get(reverse("admin:admin_commands_launch", args=["migrate"]))
    assert response.status_code == 403


def test_launch_of_unsupported_command_redirects(client_operator: Any, override_spec: Any) -> None:
    override_spec("demo_subparsers")
    response = client_operator.get(reverse("admin:admin_commands_launch", args=["demo_subparsers"]))
    assert response.status_code == 302
    assert response.url == reverse("admin:admin_commands_list")


def test_launch_creates_run(client_operator: Any, quiet_runner: list) -> None:
    response = client_operator.post(
        reverse("admin:admin_commands_launch", args=["demo_slow"]),
        {"arg_seconds": "2", "arg_mode": "fast", "reason": "тикет-7"},
    )
    assert response.status_code == 302
    run = Run.objects.get()
    assert run.argv == ["--seconds", "2", "--mode", "fast"]
    assert run.reason == "тикет-7"


def test_launch_shows_rejection_in_the_form(client_operator: Any, quiet_runner: list) -> None:
    CommandState.objects.create(name="demo_slow", enabled=False)
    response = client_operator.post(
        reverse("admin:admin_commands_launch", args=["demo_slow"]),
        {"arg_seconds": "2", "arg_mode": "fast", "reason": ""},
    )
    assert response.status_code == 200
    assert "выключена администратором" in response.content.decode()


def test_invalid_form_is_redisplayed(client_operator: Any, quiet_runner: list) -> None:
    response = client_operator.post(
        reverse("admin:admin_commands_launch", args=["demo_report"]),
        {"arg_target": "", "confirmation": ""},
    )
    assert response.status_code == 200
    assert Run.objects.count() == 0


def test_run_detail_is_rendered(client_operator: Any) -> None:
    run = make_run(arguments={"seconds": 5}, argv=["--seconds", "5"])
    response = client_operator.get(reverse("admin:admin_commands_run_change", args=[run.pk]))
    assert response.status_code == 200
    assert b"manage.py demo_slow --seconds 5" in response.content


def test_run_detail_denied_without_permission(client: Any, outsider: User) -> None:
    run = make_run()
    client.force_login(outsider)
    response = client.get(reverse("admin:admin_commands_run_change", args=[run.pk]))
    assert response.status_code == 403


def test_output_endpoint_returns_new_chunks(client_operator: Any) -> None:
    run = make_run()
    RunOutputChunk.objects.create(run=run, seq=1, text="первый\n")
    RunOutputChunk.objects.create(run=run, seq=2, text="второй\n")
    url = reverse("admin:admin_commands_output", args=[run.pk])
    payload = client_operator.get(url).json()
    assert [chunk["seq"] for chunk in payload["chunks"]] == [1, 2]
    assert payload["last_seq"] == 2
    assert payload["active"] is True

    payload = client_operator.get(url, {"after": 2}).json()
    assert payload["chunks"] == []
    assert payload["last_seq"] == 2


def test_output_endpoint_falls_back_to_tail(client_operator: Any) -> None:
    run = make_run(status=RunStatus.SUCCEEDED, output_tail="хвост")
    payload = client_operator.get(reverse("admin:admin_commands_output", args=[run.pk])).json()
    assert payload["chunks"] == [{"seq": 0, "text": "хвост"}]
    assert payload["active"] is False


def test_output_endpoint_denied_without_permission(client: Any, outsider: User) -> None:
    run = make_run()
    client.force_login(outsider)
    response = client.get(reverse("admin:admin_commands_output", args=[run.pk]))
    assert response.status_code == 403


def test_stop_requires_post(client_operator: Any) -> None:
    run = make_run()
    response = client_operator.get(reverse("admin:admin_commands_stop", args=[run.pk]))
    assert response.status_code == 405


def test_stop_records_request(client_operator: Any) -> None:
    run = make_run()
    response = client_operator.post(
        reverse("admin:admin_commands_stop", args=[run.pk]), {"mode": StopMode.HARD}
    )
    assert response.status_code == 302
    run.refresh_from_db()
    assert run.stop_mode == StopMode.HARD


def test_stop_of_finished_run_warns(client_operator: Any) -> None:
    run = make_run(status=RunStatus.SUCCEEDED)
    response = client_operator.post(
        reverse("admin:admin_commands_stop", args=[run.pk]), {}, follow=True
    )
    assert "уже завершён" in response.content.decode()


def test_run_admin_is_read_only(client_operator: Any) -> None:
    run_admin = django_admin.site._registry[Run]
    request = RequestFactory().get("/")
    assert run_admin.has_add_permission(request) is False
    assert run_admin.has_change_permission(request) is False
    assert run_admin.has_delete_permission(request) is False


def test_list_columns(client_operator: Any) -> None:
    from django_admin_commands.admin import RunAdmin

    run_admin = cast(RunAdmin, django_admin.site._registry[Run])
    run = make_run(status=RunStatus.FAILED)
    assert "Ошибка" in run_admin.status_badge(run)
    assert run_admin.duration_display(run) == "—"
    run = wait_started(run)
    assert ":" in run_admin.duration_display(run)


def wait_started(run: Run) -> Run:
    from django.utils import timezone

    run.started_at = timezone.now()
    return run


def test_changelist_is_reachable(client_operator: Any) -> None:
    make_run()
    response = client_operator.get(reverse("admin:admin_commands_run_changelist"))
    assert response.status_code == 200


def test_command_state_admin_records_editor(client_operator: Any, operator: User) -> None:
    from django.contrib.auth.models import Permission

    # Рубильником управляет администратор, а не оператор, — своё право.
    operator.user_permissions.add(*Permission.objects.filter(codename__endswith="commandstate"))
    response = client_operator.post(
        reverse("admin:admin_commands_commandstate_add"),
        {"name": "demo_slow", "enabled": "on", "disabled_reason": ""},
    )
    assert response.status_code in (200, 302)
    state = CommandState.objects.get(name="demo_slow")
    assert state.updated_by == operator


def test_registry_exposes_specs_to_the_page(client_operator: Any) -> None:
    assert {spec.name for spec in registry.all()} >= {"demo_slow", "demo_report"}


def test_launch_denied_when_spec_requires_extra_permission(
    client_operator: Any, override_spec: Any
) -> None:
    override_spec("demo_slow", permission="auth.add_user")
    response = client_operator.get(reverse("admin:admin_commands_launch", args=["demo_slow"]))
    assert response.status_code == 403
