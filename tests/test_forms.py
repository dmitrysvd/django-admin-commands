from __future__ import annotations

import argparse
from typing import Any

import pytest

from django_admin_commands.forms import (
    UnsupportedCommand,
    build_argv,
    build_form_class,
    describe_arguments,
    field_for_action,
    parse_or_none,
    visible_actions,
)
from django_admin_commands.registry import CommandSpec, registry


@pytest.fixture
def report_spec() -> CommandSpec:
    return registry.get("demo_report")


def test_base_options_are_hidden(report_spec: CommandSpec) -> None:
    names = {action.dest for action in visible_actions(report_spec)}
    assert "verbosity" not in names
    assert "target" in names
    # ratio зафиксирован спекой, поэтому в форме его быть не должно.
    assert "ratio" not in names


def test_extra_base_options_can_be_shown() -> None:
    spec = CommandSpec(name="demo_report", extra_base_options=["verbosity"])
    assert "verbosity" in {action.dest for action in visible_actions(spec)}


def test_hidden_arguments_are_skipped() -> None:
    spec = CommandSpec(name="demo_report", hidden_arguments=["limit"])
    assert "limit" not in {action.dest for action in visible_actions(spec)}


def test_subparsers_are_rejected() -> None:
    with pytest.raises(UnsupportedCommand):
        visible_actions(CommandSpec(name="demo_subparsers"))


def test_form_fields_match_argparse_types(report_spec: CommandSpec) -> None:
    form = build_form_class(report_spec)()
    assert form.fields["arg_limit"].__class__.__name__ == "IntegerField"
    assert form.fields["arg_dry_run"].__class__.__name__ == "BooleanField"
    assert form.fields["arg_tag"].__class__.__name__ == "CharField"
    assert form.fields["arg_target"].required is True
    assert form.fields["arg_fields"].required is False


def test_choices_become_choice_field() -> None:
    spec = registry.get("demo_slow")
    form = build_form_class(spec)()
    field: Any = form.fields["arg_mode"]
    assert [value for value, _label in field.choices] == ["", "fast", "slow"]


def test_confirmation_is_required_for_dangerous_commands(report_spec: CommandSpec) -> None:
    form_class = build_form_class(report_spec)
    form = form_class({"arg_target": "x", "confirmation": "wrong"})
    assert not form.is_valid()
    assert "confirmation" in form.errors

    form = form_class({"arg_target": "x", "confirmation": "demo_report"})
    assert form.is_valid(), form.errors
    assert form.arguments()["ratio"] == 2.0


def test_float_and_required_choice_fields() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ratio", type=float, default=1.5)
    parser.add_argument("--kind", choices=["a", "b"], required=True)
    fields: dict[str, Any] = {
        action.dest: field_for_action(action) for action in parser._actions[1:]
    }
    assert fields["ratio"].__class__.__name__ == "FloatField"
    assert [value for value, _label in fields["kind"].choices] == ["a", "b"]


def test_count_and_const_actions() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", action="count", default=0)
    parser.add_argument("--off", action="store_false")
    parser.add_argument("--const", action="store_const", const=7)
    actions = {action.dest: action for action in parser._actions[1:]}
    assert field_for_action(actions["verbose"] if "verbose" in actions else actions["v"])
    assert build_argv_for(actions["v"], 3) == ["-v", "-v", "-v"]
    assert build_argv_for(actions["off"], True) == []
    assert build_argv_for(actions["off"], False) == ["--off"]
    assert build_argv_for(actions["const"], True) == ["--const"]
    assert build_argv_for(actions["const"], False) == []


def build_argv_for(action: argparse.Action, value: Any) -> list:
    from django_admin_commands.forms import _argv_for_action

    return _argv_for_action(action, value)


def test_help_text_with_placeholder() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10, help="предел (%(default)s)")
    parser.add_argument("--broken", help="сломанный %(nope)s")
    actions = {action.dest: action for action in parser._actions[1:]}
    assert "10" in str(field_for_action(actions["limit"]).help_text)
    assert field_for_action(actions["broken"]).help_text == "сломанный %(nope)s"


def test_build_argv_puts_positionals_last(report_spec: CommandSpec) -> None:
    argv = build_argv(
        report_spec,
        {
            "target": "orders",
            "limit": 5,
            "ratio": 2.0,
            "dry_run": True,
            "tag": "a\nb\n",
            "fields": "id name",
        },
    )
    assert argv[-1] == "orders"
    assert "--dry-run" in argv
    assert argv.count("--tag") == 2
    assert argv[argv.index("--fields") + 1 : argv.index("--fields") + 3] == ["id", "name"]


def test_build_argv_skips_empty_values(report_spec: CommandSpec) -> None:
    argv = build_argv(report_spec, {"target": "orders", "limit": None, "fields": ""})
    assert argv == ["orders"]


def test_build_argv_accepts_list_values(report_spec: CommandSpec) -> None:
    argv = build_argv(report_spec, {"target": "orders", "fields": ["id", "name"]})
    assert argv == ["--fields", "id", "name", "orders"]


def test_build_argv_rejects_unknown_argument(report_spec: CommandSpec) -> None:
    with pytest.raises(UnsupportedCommand):
        build_argv(report_spec, {"nope": 1})


def test_describe_arguments_drops_empty_values() -> None:
    described = dict(describe_arguments({"a": 1, "b": "", "c": None, "d": False, "e": "x"}))
    assert described == {"a": 1, "e": "x"}


def test_parse_or_none_validates_argv(report_spec: CommandSpec) -> None:
    assert parse_or_none(report_spec, ["orders"]) is not None
    assert parse_or_none(report_spec, ["--limit", "not-a-number", "orders"]) is None


def test_positional_list_argument() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("items", nargs="*")
    action = parser._actions[-1]
    assert build_argv_for(action, "a b") == ["a", "b"]
