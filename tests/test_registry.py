from __future__ import annotations

import sys
from typing import Any

import pytest

from django_exec_tool.registry import CommandNotRegistered, CommandSpec, Registry, registry


def test_register_and_lookup() -> None:
    local = Registry()
    local._discovered = True
    spec = local.register(CommandSpec(name="alpha", title="Альфа"))
    assert local.get("alpha") is spec
    assert "alpha" in local
    assert local.all() == [spec]


def test_duplicate_registration_is_rejected() -> None:
    local = Registry()
    local._discovered = True
    local.register(CommandSpec(name="alpha"))
    with pytest.raises(ValueError):
        local.register(CommandSpec(name="alpha"))


def test_unregister_and_clear() -> None:
    local = Registry()
    local._discovered = True
    local.register(CommandSpec(name="alpha"))
    local.unregister("alpha")
    local.unregister("alpha")  # повторное снятие безвредно
    assert "alpha" not in local
    local.register(CommandSpec(name="beta"))
    local.clear()
    assert local._specs == {}


def test_missing_command_raises() -> None:
    local = Registry()
    local._discovered = True
    with pytest.raises(CommandNotRegistered):
        local.get("nope")


def test_label_falls_back_to_name() -> None:
    assert CommandSpec(name="alpha").label == "alpha"
    assert CommandSpec(name="alpha", title="Альфа").label == "Альфа"


def test_effective_timeout_uses_default(settings: Any) -> None:
    settings.EXEC_TOOL = {"DEFAULT_TIMEOUT": 42}
    assert CommandSpec(name="alpha").effective_timeout() == 42
    assert CommandSpec(name="alpha", timeout=7).effective_timeout() == 7


def test_lock_key_resolution() -> None:
    assert CommandSpec(name="alpha").resolve_lock_key({"a": 1}) is None
    spec = CommandSpec(name="alpha", lock_key=lambda arguments: arguments["a"])
    assert spec.resolve_lock_key({"a": "x"}) == "x"


def test_load_command_for_unknown_command() -> None:
    with pytest.raises(CommandNotRegistered):
        CommandSpec(name="definitely_missing").load_command()


def test_load_command_accepts_command_instance(monkeypatch: Any) -> None:
    # ``django_exec_tool.registry`` перекрыт объектом реестра, поэтому берём
    # сам модуль из sys.modules.
    registry_module = sys.modules["django_exec_tool.registry"]

    sentinel = object()
    monkeypatch.setattr(registry_module, "get_commands", lambda: {"alpha": sentinel})
    assert CommandSpec(name="alpha").load_command() is sentinel


def test_autodiscover_runs_once() -> None:
    # Приложения уже загружены, значит exec_commands демо-проекта подхвачены.
    assert "demo_slow" in registry
    registry.autodiscover()
    assert registry.get("demo_slow").name == "demo_slow"
