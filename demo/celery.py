"""Celery-приложение демо-стенда."""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo.settings")

app = Celery("demo")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
# Задача инструмента лежит в его собственном модуле, а не в приложении проекта.
app.autodiscover_tasks(["django_exec_tool"], related_name="tasks")
