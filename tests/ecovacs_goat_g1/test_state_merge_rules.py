"""Static checks that coordinator refreshes cannot write back a stale state.

MQTT pushes change ``coordinator.data`` while any command is pending, and the
mower answers map requests with pushes that arrive before the HTTP reply. A
``MowerState`` captured before an ``await`` and written afterwards erases them.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
import textwrap

PACKAGE_PATH = Path(__file__).parents[2] / "custom_components" / "ecovacs_goat_g1"
APPLY_FUNCTIONS = {"apply_response", "apply_command_data", "apply_mqtt_payload"}
READ, AWAIT, ASSIGN = 0, 1, 2


def _parse(module: str) -> ast.Module:
    return ast.parse((PACKAGE_PATH / module).read_text(encoding="utf-8"))


def _is_state_name(name: str) -> bool:
    return name == "state" or name.endswith("_state")


def _own_nodes(function: ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    """Yield the nodes of a function body, skipping nested functions and lambdas."""
    pending: list[ast.AST] = list(function.body)
    while pending:
        node = pending.pop()
        yield node
        pending.extend(
            child
            for child in ast.iter_child_nodes(node)
            if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        )


def stale_state_reads(function: ast.AsyncFunctionDef) -> list[tuple[int, str]]:
    """Return ``(line, name)`` for state locals read after an await they predate.

    Arguments are evaluated before an ``await`` suspends, and an assignment takes
    effect after its value, so each event is placed where it completes. Loops
    are read top to bottom; this is a lint, not a proof.
    """
    events = [
        ((function.lineno, -1), ASSIGN, arg.arg)
        for arg in (*function.args.args, *function.args.kwonlyargs)
        if _is_state_name(arg.arg)
    ]
    for node in _own_nodes(function):
        if isinstance(node, ast.Await):
            events.append(((node.end_lineno, node.end_col_offset), AWAIT, ""))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            events.extend(
                ((node.end_lineno, node.end_col_offset), ASSIGN, name.id)
                for target in targets
                for name in ast.walk(target)
                if isinstance(name, ast.Name) and _is_state_name(name.id)
            )
        elif (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and _is_state_name(node.id)
        ):
            events.append(((node.lineno, node.col_offset), READ, node.id))

    assigned: set[str] = set()
    fresh: set[str] = set()
    stale: list[tuple[int, str]] = []
    for (line, _column), kind, name in sorted(events):
        if kind == AWAIT:
            fresh.clear()
        elif kind == ASSIGN:
            assigned.add(name)
            fresh.add(name)
        elif name in assigned and name not in fresh:
            stale.append((line, name))
    return stale


def _apply_calls(tree: ast.Module) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in APPLY_FUNCTIONS
        ):
            yield node


def _function(source: str) -> ast.AsyncFunctionDef:
    function = ast.parse(textwrap.dedent(source)).body[0]
    assert isinstance(function, ast.AsyncFunctionDef)
    return function


def test_check_flags_the_keepalive_that_erased_the_mowed_area() -> None:
    """The keepalive shape that dropped pushed traces until 3b9e586 is caught."""
    function = _function(
        """
        async def keepalive(self):
            state = self.data
            await self.ping()
            state = await self.stream(state)
            self.async_set_updated_data(state)
        """
    )

    assert stale_state_reads(function) == [(5, "state")]


def test_check_accepts_state_assigned_from_the_await() -> None:
    function = _function(
        """
        async def refresh(self, state):
            state = await self.refresh_groups(state)
            self.async_set_updated_data(state)
        """
    )

    assert stale_state_reads(function) == []


def test_coordinator_never_reads_a_state_snapshot_after_an_await() -> None:
    stale = [
        f"{function.name} line {line}: {name}"
        for function in ast.walk(_parse("mower_coordinator.py"))
        if isinstance(function, ast.AsyncFunctionDef)
        for line, name in stale_state_reads(function)
    ]

    assert stale == []


def test_coordinator_applies_replies_to_the_latest_state_only() -> None:
    """Any state other than ``self.data`` may predate pushes received meanwhile."""
    calls = [
        f"line {call.lineno}: {call.func.id}({ast.unparse(call.args[0])}, ...)"
        for call in _apply_calls(_parse("mower_coordinator.py"))
        if ast.unparse(call.args[0]) != "self.data"
    ]

    assert calls == []


def test_compat_helpers_hand_replies_to_the_coordinator() -> None:
    """Helpers pass replies to ``merge_reply`` instead of applying them to a copy."""
    calls = [
        f"line {call.lineno}: {call.func.id}"
        for call in _apply_calls(_parse("mower_compat.py"))
    ]

    assert calls == []
