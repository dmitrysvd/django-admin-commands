"""Белый список демо-проекта.

Именно такой файл заводится в каждом приложении, команды которого можно
запускать из админки.
"""

from __future__ import annotations

from django_exec_tool import CommandSpec, registry

registry.register(
    CommandSpec(
        name="demo_slow",
        title="Долгая задача",
        description="Тикает раз в секунду. Удобна, чтобы проверить остановку и таймаут.",
        timeout=120,
        interruptible=True,
        idempotent=True,
        nice=10,
        queue="exec_tool",
    )
)

registry.register(
    CommandSpec(
        name="demo_report",
        title="Отчёт по аргументам",
        description="Показывает, как argparse превращается в форму.",
        timeout=60,
        idempotent=True,
        confirm=True,
        queue="exec_tool",
        # Аргумент зафиксирован спекой: в форме его нет, но в argv он попадёт.
        fixed_arguments={"ratio": 2.0},
        lock_key=lambda arguments: arguments.get("target"),
    )
)
