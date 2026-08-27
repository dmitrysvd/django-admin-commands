"""Дефолтный интерфейс инструмента, встроенный в админку.

Отдельный urls.py подключать не нужно: страницы живут внутри ``RunAdmin``, так
что достаточно добавить приложение в ``INSTALLED_APPS``.

Журнал запусков намеренно нередактируем и неудаляем: он и есть аудит.
"""

from __future__ import annotations

from typing import Any

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from .forms import UnsupportedCommand, build_form_class, describe_arguments
from .models import CommandState, Run, RunOutputChunk, RunStatus, StopMode
from .policy import can_run, can_use_tool
from .registry import CommandNotRegistered, registry
from .services import LaunchRejected, is_enabled, launch, request_stop

STATUS_COLORS = {
    RunStatus.PENDING: "#888",
    RunStatus.RUNNING: "#1b6ac9",
    RunStatus.SUCCEEDED: "#2c8a3d",
    RunStatus.FAILED: "#c0392b",
    RunStatus.CANCELED: "#888",
    RunStatus.STOPPED: "#d35400",
    RunStatus.TIMED_OUT: "#d35400",
    RunStatus.UNKNOWN: "#8e44ad",
}


@admin.register(CommandState)
class CommandStateAdmin(admin.ModelAdmin):
    """Рубильник. Может только выключать — включить в список ничего не может."""

    list_display = ("name", "enabled", "updated_at", "updated_by")
    list_filter = ("enabled",)
    search_fields = ("name",)
    readonly_fields = ("updated_at", "updated_by")

    def save_model(self, request: HttpRequest, obj: CommandState, form: Any, change: bool) -> None:
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "command",
        "status_badge",
        "requested_by_repr",
        "duration_display",
        "exit_code",
    )
    list_filter = ("status", "command")
    search_fields = ("command", "requested_by_repr", "reason", "id")
    date_hierarchy = "created_at"
    change_form_template = "django_exec_tool/run_detail.html"

    # -- журнал неизменяем ------------------------------------------------

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_view_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return can_use_tool(request.user)

    def has_module_permission(self, request: HttpRequest) -> bool:
        return can_use_tool(request.user)

    # -- колонки ----------------------------------------------------------

    @admin.display(description=_("статус"), ordering="status")
    def status_badge(self, obj: Run) -> str:
        return format_html(
            '<b style="color:{}">{}</b>',
            STATUS_COLORS.get(obj.status, "#000"),
            obj.status_display,
        )

    @admin.display(description=_("длительность"))
    def duration_display(self, obj: Run) -> str:
        duration = obj.duration
        if duration is None:
            return "—"
        return str(duration).split(".")[0]

    # -- маршруты ---------------------------------------------------------

    def get_urls(self) -> list[Any]:
        view = self.admin_site.admin_view
        custom = [
            path("launch/", view(self.command_list_view), name="exec_tool_command_list"),
            path("launch/<str:name>/", view(self.launch_view), name="exec_tool_launch"),
            path("<uuid:pk>/output/", view(self.output_view), name="exec_tool_output"),
            path("<uuid:pk>/stop/", view(self.stop_view), name="exec_tool_stop"),
        ]
        return custom + super().get_urls()

    def _context(self, request: HttpRequest, **extra: Any) -> dict[str, Any]:
        context = self.admin_site.each_context(request)
        context.update(opts=self.model._meta, has_view_permission=True, **extra)
        return context

    # -- страницы ---------------------------------------------------------

    def command_list_view(self, request: HttpRequest) -> HttpResponse:
        if not can_use_tool(request.user):
            raise PermissionDenied
        commands = []
        for spec in registry.all():
            commands.append(
                {
                    "spec": spec,
                    "allowed": can_run(request.user, spec),
                    "enabled": is_enabled(spec),
                    "url": reverse("admin:exec_tool_launch", args=[spec.name]),
                    "active": Run.objects.active().filter(command=spec.name).count(),
                }
            )
        return render(
            request,
            "django_exec_tool/command_list.html",
            self._context(request, title=_("Запуск команды"), commands=commands),
        )

    def launch_view(self, request: HttpRequest, name: str) -> HttpResponse:
        try:
            spec = registry.get(name)
        except CommandNotRegistered:
            raise PermissionDenied(_("Команда не входит в белый список.")) from None
        if not can_run(request.user, spec):
            raise PermissionDenied

        try:
            form_class = build_form_class(spec)
        except UnsupportedCommand as exc:
            self.message_user(request, str(exc), level=messages.ERROR)
            return redirect("admin:exec_tool_command_list")

        form = form_class(request.POST or None)
        if request.method == "POST" and form.is_valid():
            try:
                run = launch(request.user, spec, form.arguments(), form.cleaned_data["reason"])
            except LaunchRejected as exc:
                form.add_error(None, str(exc))
            else:
                return redirect("admin:django_exec_tool_run_change", run.pk)

        return render(
            request,
            "django_exec_tool/launch.html",
            self._context(
                request,
                title=_("Запуск: %s") % spec.label,
                spec=spec,
                form=form,
                enabled=is_enabled(spec),
            ),
        )

    def change_view(
        self,
        request: HttpRequest,
        object_id: str,
        form_url: str = "",
        extra_context: Any = None,
    ) -> HttpResponse:
        if not can_use_tool(request.user):
            raise PermissionDenied
        run = get_object_or_404(Run, pk=object_id)
        context = self._context(
            request,
            title=_("Запуск %s") % run.command,
            run=run,
            original=run,
            arguments=list(describe_arguments(run.arguments)),
            can_stop=can_use_tool(request.user) and run.is_active,
            stop_modes=StopMode.choices,
            output_url=reverse("admin:exec_tool_output", args=[run.pk]),
            stop_url=reverse("admin:exec_tool_stop", args=[run.pk]),
        )
        return render(request, self.change_form_template, context)

    def output_view(self, request: HttpRequest, pk: Any) -> JsonResponse:
        if not can_use_tool(request.user):
            raise PermissionDenied
        run = get_object_or_404(Run, pk=pk)
        after = int(request.GET.get("after") or 0)
        chunks = list(
            RunOutputChunk.objects.filter(run=run, seq__gt=after)
            .order_by("seq")
            .values("seq", "text")
        )
        payload: dict[str, Any] = {
            "status": run.status,
            "status_display": run.status_display,
            "active": run.is_active,
            "exit_code": run.exit_code,
            "chunks": chunks,
            "last_seq": chunks[-1]["seq"] if chunks else after,
        }
        if not run.is_active and not chunks and after == 0:
            # Чанки уже вычищены после архивации — показываем сохранённый хвост.
            payload["chunks"] = [{"seq": 0, "text": run.output_tail}]
        return JsonResponse(payload)

    def stop_view(self, request: HttpRequest, pk: Any) -> HttpResponse:
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        run = get_object_or_404(Run, pk=pk)
        mode = request.POST.get("mode") or StopMode.SOFT
        try:
            request_stop(run, request.user, mode)
        except LaunchRejected as exc:
            self.message_user(request, str(exc), level=messages.WARNING)
        else:
            self.message_user(request, _("Остановка запрошена (%s).") % mode)
        return redirect("admin:django_exec_tool_run_change", run.pk)
