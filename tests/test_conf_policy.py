from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser

from django_admin_commands.conf import app_settings
from django_admin_commands.policy import can_run, can_use_tool, default_policy
from django_admin_commands.registry import CommandSpec
from django_admin_commands.runners.base import BaseRunner
from django_admin_commands.runners.thread import ThreadRunner


def test_defaults_are_used(settings: Any) -> None:
    settings.ADMIN_COMMANDS = {}
    assert app_settings.DEFAULT_TIMEOUT == 900


def test_override_is_picked_up(settings: Any) -> None:
    settings.ADMIN_COMMANDS = {"DEFAULT_TIMEOUT": 5}
    assert app_settings.DEFAULT_TIMEOUT == 5


def test_import_strings_are_resolved(settings: Any) -> None:
    settings.ADMIN_COMMANDS = {"RUNNER": "django_admin_commands.runners.thread.ThreadRunner"}
    assert app_settings.RUNNER is ThreadRunner


def test_unknown_setting_raises() -> None:
    with pytest.raises(AttributeError):
        _ = app_settings.NOPE


def test_base_runner_is_abstract() -> None:
    with pytest.raises(NotImplementedError):
        BaseRunner().enqueue(object())


def test_anonymous_user_is_denied() -> None:
    spec = CommandSpec(name="alpha")
    assert default_policy(AnonymousUser(), spec, {}) is False
    assert can_use_tool(AnonymousUser()) is False
    assert can_use_tool(None) is False


def test_permission_is_required(operator: Any, outsider: Any) -> None:
    spec = CommandSpec(name="alpha")
    assert can_run(operator, spec) is True
    assert can_run(outsider, spec) is False
    assert can_use_tool(operator) is True


def test_extra_permission_is_checked(operator: Any) -> None:
    spec = CommandSpec(name="alpha", permission="auth.add_user")
    assert can_run(operator, spec, {}) is False


def test_cache_is_reset_only_for_our_setting(settings: Any) -> None:
    settings.ADMIN_COMMANDS = {"DEFAULT_TIMEOUT": 11}
    assert app_settings.DEFAULT_TIMEOUT == 11
    # Чужая настройка кэш трогать не должна.
    settings.DEBUG = not settings.DEBUG
    assert app_settings.DEFAULT_TIMEOUT == 11
