from __future__ import annotations

import time
from typing import Any, Callable, Iterator

import pytest
from django.contrib.auth.models import Permission, User

from django_admin_commands.models import Run
from django_admin_commands.registry import CommandSpec, registry


@pytest.fixture
def operator(db: Any) -> User:
    """Пользователь с правом запускать команды."""
    user = User.objects.create_user("operator", password="secret", is_staff=True)
    user.user_permissions.add(Permission.objects.get(codename="run_command"))
    return User.objects.get(pk=user.pk)


@pytest.fixture
def outsider(db: Any) -> User:
    return User.objects.create_user("outsider", password="secret", is_staff=True)


@pytest.fixture
def override_spec() -> Iterator[Callable[..., CommandSpec]]:
    """Временно подменить спеку команды в реестре."""
    replaced: list[tuple[str, CommandSpec | None]] = []

    def _override(name: str, **kwargs: Any) -> CommandSpec:
        try:
            previous: CommandSpec | None = registry.get(name)
        except Exception:
            previous = None
        replaced.append((name, previous))
        registry.unregister(name)
        spec = CommandSpec(name=name, **kwargs)
        registry.register(spec)
        return spec

    yield _override

    for name, previous in reversed(replaced):
        registry.unregister(name)
        if previous is not None:
            registry.register(previous)


def wait_for(predicate: Callable[[], bool], timeout: float = 30.0) -> bool:
    """Дождаться условия, опрашивая его; тесты работают с реальными процессами."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def wait_finished(run: Run, timeout: float = 30.0) -> Run:
    def done() -> bool:
        run.refresh_from_db()
        return not run.is_active

    assert wait_for(done, timeout), f"run stayed active: {run.status}"
    run.refresh_from_db()
    return run
