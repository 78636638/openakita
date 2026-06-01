"""
CLI stream renderer — consumes chat_with_session_stream() events
and renders them in real-time using Rich Live.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from copy import deepcopy
import os
import time

from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from ..events import StreamEventType
from ..utils.errors import format_user_friendly_error

E = StreamEventType


async def render_stream(
    event_stream: AsyncIterator[dict],
    console: Console,
    agent_name: str = "OpenAkita",
    initial_status: str = "",
    turn_id: str = "",
    turn_index: int = 0,
    user_input: str = "",
    turn_container_enabled: bool | None = None,
    turn_archive: list[dict] | None = None,
    turn_archive_limit: int | None = None,
) -> str:
    """Consume an SSE event stream and render it progressively in the terminal.

    Returns the final assistant text content.
    """
    buffer: list[str] = []
    iteration = 0
    thinking_started = False
    tool_stack: list[str] = []
    has_error = False
    graceful_done = False

    state = {
        "iteration": iteration,
        "thinking_started": thinking_started,
        "has_error": has_error,
        "graceful_done": graceful_done,
        "todo_plan": None,
        "panel_rendered": False,
        "status_line": str(initial_status or "").strip(),
        "last_rendered_content": "",
        "last_rendered_status": "",
        "tool_history": [],
        "sub_agent_runtime": None,
        "sub_agent_tool_states": {},
        "turn_id": str(turn_id or "").strip(),
        "turn_index": int(turn_index or 0),
        "user_input": str(user_input or "").strip(),
        "turn_container_enabled": _turn_container_enabled(turn_container_enabled),
    }

    with Live(
        console=console,
        auto_refresh=False,
        vertical_overflow="crop",
        transient=True,
    ) as live:
        refresh_task = asyncio.create_task(_runtime_refresh_loop(live, agent_name, buffer, state))
        if state["status_line"]:
            _render_status_or_panel(live, agent_name, buffer, state)
        try:
            async for event in event_stream:
                etype = event.get("type", "")

                if etype == E.SECURITY_CONFIRM:
                    live.stop()
                    _handle_security_confirm_interactive(event, console)
                    live.start()
                    continue

                if etype == E.ASK_USER:
                    live.stop()
                    _handle_ask_user_interactive(event, console)
                    live.start()
                    continue

                _handle_event(
                    etype,
                    event,
                    live,
                    console,
                    agent_name,
                    buffer,
                    tool_stack,
                    state,
                )
                if state["graceful_done"]:
                    break
        except Exception as exc:
            console.print(f"  [red]⚠️ 流式传输中断: {exc}[/red]")
            state["has_error"] = True
        finally:
            refresh_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await refresh_task

    if not state["graceful_done"] and not state["has_error"]:
        console.print("  [yellow]⚠️ 流式响应未正常结束[/yellow]")

    content = _compose_panel_content(buffer, state)

    if state.get("turn_container_enabled"):
        if turn_archive is not None:
            _append_turn_archive_record(
                turn_archive,
                _build_archived_turn_record(buffer, state),
                limit=_turn_archive_limit(turn_archive_limit),
            )
        console.print()
        console.print(_build_archived_turn_summary_renderable(agent_name, buffer, state))
        console.print()
        return content

    if content and not state["has_error"] and not state["panel_rendered"]:
        console.print()
        console.print(
            _wrap_turn_renderable(
                state,
                Panel(
                    Markdown(content),
                    title=f"[bold green]{agent_name}[/bold green]",
                    border_style="green",
                ),
            )
        )

    console.print()
    return content


def _handle_event(
    etype: str,
    event: dict,
    live: Live,
    console: Console,
    agent_name: str,
    buffer: list[str],
    tool_stack: list[str],
    ref: dict,
) -> None:
    """Dispatch a single SSE event to the appropriate Rich renderer."""

    if etype == E.HEARTBEAT:
        return

    if etype == E.ITERATION_START:
        ref["iteration"] = event.get("iteration", ref["iteration"] + 1)
        if ref["iteration"] > 1:
            ref["status_line"] = f"⟳ 轮次 {ref['iteration']}..."
            _render_status_or_panel(live, agent_name, buffer, ref)
        return

    if etype == E.THINKING_START:
        ref["thinking_started"] = True
        ref["status_line"] = f"LLM 正在分析请求 (轮次 {ref['iteration']})..."
        _render_status_or_panel(live, agent_name, buffer, ref)
        return

    if etype == E.THINKING_END:
        duration = (event.get("duration_ms") or 0) / 1000
        if ref["thinking_started"]:
            console.print(f"  [dim]💭 思考完成 ({duration:.1f}s)[/dim]")
        ref["thinking_started"] = False
        ref["status_line"] = ""
        _refresh_runtime_view(live, agent_name, buffer, ref)
        return

    if etype == E.THINKING_DELTA:
        return

    if etype == E.CHAIN_TEXT:
        text = event.get("content", event.get("text", ""))
        if text:
            ref["status_line"] = f"LLM 执行中: {_summarize_runtime_text(text)}"
            _render_status_or_panel(live, agent_name, buffer, ref)
        return

    if etype == E.TEXT_DELTA:
        if event.get("content"):
            ref["status_line"] = ""
        buffer.append(event.get("content", ""))
        _render_agent_panel(live, agent_name, buffer, ref)
        return

    if etype == E.TEXT_REPLACE:
        content = event.get("content", "")
        if content:
            ref["status_line"] = ""
        buffer.clear()
        if content:
            buffer.append(content)
        _render_agent_panel(live, agent_name, buffer, ref)
        return

    if etype == E.TOOL_CALL_START:
        tool_name = event.get("tool", "unknown")
        tool_stack.append(tool_name)
        _register_tool_call_start(ref, event)
        ref["status_line"] = _format_tool_runtime_status(ref, tool_name)
        _render_status_or_panel(live, agent_name, buffer, ref)
        return

    if etype == E.TOOL_CALL_END:
        tool_name = event.get("tool", "unknown")
        is_error = event.get("is_error", False)
        skipped = event.get("skipped", False)
        detail = _format_tool_end_detail(event)
        final_status = _classify_tool_end_status(event, detail)
        _register_tool_call_end(ref, event, detail, final_status=final_status)
        if final_status == "skipped":
            status = "[yellow]已跳过[/yellow]"
            ref["status_line"] = f"工具已跳过: {tool_name}" + (f" | {detail}" if detail else "")
        elif final_status == "failed":
            status = "[red]执行失败[/red]"
            ref["status_line"] = f"工具执行失败: {tool_name}" + (f" | {detail}" if detail else "")
        else:
            status = "[green]已完成[/green]"
            ref["status_line"] = f"工具已完成: {tool_name}" + (f" | {detail}" if detail else "")
        _render_status_or_panel(live, agent_name, buffer, ref)
        if tool_stack and tool_stack[-1] == tool_name:
            tool_stack.pop()
        return

    if etype == E.CONTEXT_COMPRESSED:
        before = event.get("before_tokens", "?")
        after = event.get("after_tokens", "?")
        console.print(f"  [dim]📦 上下文压缩: {before} → {after} tokens[/dim]")
        return

    if etype == E.TODO_CREATED:
        plan_payload = dict(event.get("plan", {}) or {})
        if "restored" in event and "restored" not in plan_payload:
            plan_payload["restored"] = event.get("restored")
        ref["todo_plan"] = _normalize_todo_plan(plan_payload)
        _render_agent_panel(live, agent_name, buffer, ref)
        return

    if etype == E.TODO_STEP_UPDATED:
        _apply_todo_step_update(ref.get("todo_plan"), event)
        _render_agent_panel(live, agent_name, buffer, ref)
        return

    if etype == E.TODO_COMPLETED:
        _apply_todo_completion(ref.get("todo_plan"), completed=True)
        _render_agent_panel(live, agent_name, buffer, ref)
        return

    if etype == E.TODO_CANCELLED:
        if event.get("clear"):
            ref["todo_plan"] = None
            if _has_panel_content(buffer, ref):
                _render_agent_panel(live, agent_name, buffer, ref)
            else:
                ref["panel_rendered"] = False
                live.update(Text(""), refresh=True)
            return
        _apply_todo_completion(ref.get("todo_plan"), completed=False)
        if isinstance(ref.get("todo_plan"), dict):
            reason = str(event.get("reason") or event.get("message") or "").strip()
            if reason:
                ref["todo_plan"]["terminal_reason"] = reason
            if str(event.get("status") or "").strip() in {"cancelled", "interrupted", "failed"}:
                ref["todo_plan"]["status"] = str(event.get("status") or "").strip()
        cancel_reason = str(event.get("reason") or event.get("message") or "").strip()
        if cancel_reason:
            ref["status_line"] = f"任务已终止: {_summarize_runtime_text(cancel_reason, limit=120)}"
        _render_agent_panel(live, agent_name, buffer, ref)
        return

    if etype == E.SECURITY_CONFIRM or etype == E.ASK_USER:
        return

    if etype == E.AGENT_HANDOFF:
        from_agent = event.get("from_agent", "")
        to_agent = event.get("to_agent", "")
        reason = event.get("reason", "")
        ref["status_line"] = _format_agent_handoff_status(from_agent, to_agent, reason)
        _render_status_or_panel(live, agent_name, buffer, ref)
        return

    if etype == E.SUB_AGENT_STATE:
        _register_sub_agent_runtime(ref, event)
        status_line = _format_sub_agent_state_status(ref.get("sub_agent_runtime"))
        if status_line:
            ref["status_line"] = status_line
            _render_status_or_panel(live, agent_name, buffer, ref)
        return

    if etype == E.USER_INSERT:
        content = event.get("content", "")
        if content:
            console.print(f"  [blue]📝 用户插入: {content[:80]}[/blue]")
        return

    if etype == E.ARTIFACT:
        name = event.get("name", "")
        atype = event.get("artifact_type", "")
        console.print(f"  [green]📎 产出: {name} ({atype})[/green]")
        return

    if etype == E.ERROR:
        msg = event.get("message", "")
        friendly = format_user_friendly_error(msg)
        ref["status_line"] = ""
        console.print(f"  [red]{friendly}[/red]")
        ref["has_error"] = True
        return

    if etype == E.DONE:
        ref["graceful_done"] = True
        ref["status_line"] = ""
        _refresh_runtime_view(live, agent_name, buffer, ref)
        usage = event.get("usage")
        if usage:
            inp = usage.get("input_tokens", 0)
            out = usage.get("output_tokens", 0)
            ctx = usage.get("context_tokens")
            parts = [f"输入 {inp}", f"输出 {out}"]
            if ctx:
                limit = usage.get("context_limit", 0)
                parts.append(f"上下文 {ctx}/{limit}")
            console.print(f"  [dim]📊 {' | '.join(parts)} tokens[/dim]")
        return


async def _runtime_refresh_loop(
    live: Live,
    agent_name: str,
    buffer: list[str],
    state: dict,
) -> None:
    """Keep running elapsed timers fresh while the stream is active."""
    try:
        while True:
            await asyncio.sleep(0.1)
            if state.get("graceful_done") or state.get("has_error"):
                return
            if not _needs_live_elapsed_refresh(state):
                continue
            _refresh_runtime_view(live, agent_name, buffer, state)
    except asyncio.CancelledError:
        raise


def _render_agent_panel(
    live: Live,
    agent_name: str,
    buffer: list[str],
    state: dict,
) -> None:
    if state.get("turn_container_enabled"):
        renderable = _build_turn_container_renderable(agent_name, buffer, state)
        snapshot = _snapshot_turn_container_content(buffer, state)
        if state.get("panel_rendered") and state.get("last_rendered_content") == snapshot:
            return
        state["panel_rendered"] = True
        state["last_rendered_content"] = snapshot
        state["last_rendered_status"] = ""
        live.update(renderable, refresh=True)
        return
    content = _compose_panel_content(buffer, state)
    if not content:
        return
    if state.get("panel_rendered") and state.get("last_rendered_content") == content:
        return
    state["panel_rendered"] = True
    state["last_rendered_content"] = content
    state["last_rendered_status"] = ""
    live.update(
        _wrap_turn_renderable(
            state,
            Panel(
                Markdown(content),
                title=f"[bold green]{agent_name}[/bold green]",
                border_style="green",
                padding=(0, 1),
            ),
        ),
        refresh=True,
    )


def _render_status_or_panel(
    live: Live,
    agent_name: str,
    buffer: list[str],
    state: dict,
) -> None:
    if state.get("turn_container_enabled"):
        renderable = _build_turn_container_renderable(agent_name, buffer, state)
        snapshot = _snapshot_turn_container_content(buffer, state)
        if state.get("panel_rendered") and state.get("last_rendered_content") == snapshot:
            return
        state["panel_rendered"] = True
        state["last_rendered_content"] = snapshot
        state["last_rendered_status"] = ""
        live.update(renderable, refresh=True)
        return
    if _has_panel_content(buffer, state):
        _render_agent_panel(live, agent_name, buffer, state)
        return
    status_line = _get_dynamic_status_line(state)
    if status_line:
        if state.get("last_rendered_status") == status_line:
            return
        state["last_rendered_status"] = status_line
        state["last_rendered_content"] = ""
        live.update(
            _wrap_turn_renderable(state, Text(f"  {status_line}", style="dim italic")),
            refresh=True,
        )


def _refresh_runtime_view(
    live: Live,
    agent_name: str,
    buffer: list[str],
    state: dict,
) -> None:
    if state.get("turn_container_enabled"):
        _render_status_or_panel(live, agent_name, buffer, state)
        return
    if _has_panel_content(buffer, state):
        _render_agent_panel(live, agent_name, buffer, state)
        return
    status_line = _get_dynamic_status_line(state)
    if status_line:
        _render_status_or_panel(live, agent_name, buffer, state)
        return
    state["panel_rendered"] = False
    state["last_rendered_content"] = ""
    state["last_rendered_status"] = ""
    live.update(_wrap_turn_renderable(state, Text("")), refresh=True)


def _has_panel_content(buffer: list[str], state: dict) -> bool:
    return bool(
        "".join(buffer).strip()
        or _build_todo_markdown(state.get("todo_plan"))
        or _build_tool_tree_markdown(state.get("tool_history"))
    )


def _compose_panel_content(buffer: list[str], state: dict) -> str:
    assistant_text = "".join(buffer).strip()
    todo_text = _build_todo_markdown(state.get("todo_plan"))
    tool_tree_text = _build_tool_tree_markdown(state.get("tool_history"))
    status_line = _get_dynamic_status_line(state)
    status_text = f"> {status_line}" if status_line else ""
    parts = [part for part in (status_text, todo_text, tool_tree_text, assistant_text) if part]
    return "\n\n".join(parts).strip()


def _turn_container_enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    raw = str(os.getenv("CLI_TURN_CONTAINER_ENABLED", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _turn_archive_limit(explicit: int | None = None) -> int:
    if explicit is not None:
        return max(1, int(explicit))
    raw = str(os.getenv("CLI_TURN_ARCHIVE_LIMIT", "20") or "20").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 20


def _wrap_turn_renderable(state: dict, inner_renderable):
    if not state.get("turn_container_enabled"):
        return inner_renderable

    turn_index = int(state.get("turn_index", 0) or 0)
    turn_id = str(state.get("turn_id") or "").strip()
    user_input = str(state.get("user_input") or "").strip()
    title = f"[bold cyan]Turn #{turn_index}[/bold cyan]" if turn_index > 0 else "[bold cyan]当前提问[/bold cyan]"
    subtitle = turn_id or ""

    sections = []
    if user_input:
        sections.append(Text("用户问题", style="bold blue"))
        sections.append(Text(user_input))
        sections.append(Text(""))
    sections.append(inner_renderable)
    return Panel(
        Group(*sections),
        title=title,
        subtitle=subtitle,
        border_style="blue",
        padding=(0, 1),
    )


def _build_turn_container_renderable(
    agent_name: str,
    buffer: list[str],
    state: dict,
):
    title = _build_turn_container_title(state)
    subtitle = str(state.get("turn_id") or "").strip()
    sections: list[object] = []

    user_input = str(state.get("user_input") or "").strip()
    if user_input:
        sections.append(
            Panel(
                Text(user_input),
                title="[bold blue]用户问题[/bold blue]",
                border_style="blue",
                padding=(0, 1),
            )
        )

    status_line = _get_dynamic_status_line(state)
    if status_line:
        sections.append(
            Panel(
                Text(status_line),
                title="[bold yellow]运行状态[/bold yellow]",
                border_style="yellow",
                padding=(0, 1),
            )
        )

    is_running = not state.get("graceful_done") and not state.get("has_error")
    current_execution_text = _build_current_execution_markdown(state) if is_running else ""
    tool_tree_text = _build_tool_tree_markdown(
        state.get("tool_history"),
        max_entries=8 if is_running else None,
        newest_first=is_running,
    )
    if current_execution_text:
        sections.append(
            Panel(
                Markdown(current_execution_text),
                title="[bold cyan]当前执行[/bold cyan]",
                border_style="cyan",
                padding=(0, 1),
            )
        )
    todo_text = _build_todo_markdown(state.get("todo_plan"))
    if todo_text:
        sections.append(
            Panel(
                Markdown(todo_text),
                title="[bold cyan]Todo 任务[/bold cyan]" if is_running else "[bold cyan]任务计划[/bold cyan]",
                border_style="cyan",
                padding=(0, 1),
            )
        )
    if tool_tree_text:
        sections.append(
            Panel(
                Markdown(tool_tree_text),
                title=f"[bold green]{agent_name} 执行过程[/bold green]",
                border_style="green",
                padding=(0, 1),
            )
        )

    assistant_text = "".join(buffer).strip()
    if assistant_text:
        body_text = _build_live_output_preview(assistant_text) if is_running else assistant_text
        result_title = (
            f"[bold magenta]{agent_name} 最终结果[/bold magenta]"
            if state.get("graceful_done")
            else f"[bold magenta]{agent_name} 输出预览[/bold magenta]"
        )
        sections.append(
            Panel(
                Markdown(body_text),
                title=result_title,
                border_style="magenta",
                padding=(0, 1),
            )
        )

    if not sections:
        sections.append(
            Panel(
                Text("等待事件..."),
                title=f"[bold green]{agent_name}[/bold green]",
                border_style="green",
                padding=(0, 1),
            )
        )

    return Panel(
        Group(*sections),
        title=title,
        subtitle=subtitle,
        border_style="blue",
        padding=(0, 1),
    )


def _build_turn_container_title(state: dict) -> str:
    turn_index = int(state.get("turn_index", 0) or 0)
    if turn_index > 0:
        return f"[bold cyan]Turn #{turn_index}[/bold cyan]"
    return "[bold cyan]当前提问[/bold cyan]"


def _build_archived_turn_summary_renderable(
    agent_name: str,
    buffer: list[str],
    state: dict,
):
    return _build_archived_turn_summary_renderable_from_record(_build_archived_turn_record(buffer, state))


def _build_archived_turn_record(buffer: list[str], state: dict) -> dict:
    assistant_text = "".join(buffer).strip()
    tool_tree_text = _build_tool_tree_markdown(state.get("tool_history"), max_entries=20)
    todo_text = _build_todo_markdown(state.get("todo_plan"))
    return {
        "turn_index": int(state.get("turn_index", 0) or 0),
        "turn_id": str(state.get("turn_id") or "").strip(),
        "user_input": _summarize_runtime_text(str(state.get("user_input") or "").strip(), limit=180),
        "status": _build_turn_completion_label(state),
        "result_summary": _build_turn_result_summary(state, assistant_text),
        "inline_output": assistant_text if _should_inline_turn_output(assistant_text) else "",
        "todo_summary": _build_turn_todo_progress_summary(state.get("todo_plan")),
        "detail_user_input": _truncate_multiline_text(str(state.get("user_input") or "").strip(), max_chars=1000),
        "detail_output": assistant_text,
        "detail_tool_tree": _truncate_multiline_text(tool_tree_text, max_chars=4000),
        "detail_todo": _truncate_multiline_text(todo_text, max_chars=2000),
    }


def _build_archived_turn_summary_renderable_from_record(record: dict):
    turn_index = int(record.get("turn_index", 0) or 0)
    turn_title = (
        f"[bold cyan]Turn #{turn_index}[/bold cyan]" if turn_index > 0 else "[bold cyan]当前提问[/bold cyan]"
    )
    turn_id = str(record.get("turn_id") or "").strip()
    user_input = str(record.get("user_input") or "").strip()
    status = str(record.get("status") or "`未完成`").strip()
    result_summary = str(record.get("result_summary") or "").strip()
    inline_output = str(record.get("inline_output") or "").strip()
    todo_summary = str(record.get("todo_summary") or "").strip()

    lines = [f"**状态**: {status}"]
    if user_input:
        lines.append(f"**用户问题**: {user_input}")
    if result_summary:
        lines.append(f"**结果摘要**: {result_summary}")
    if todo_summary:
        lines.append(f"**任务进度**: {todo_summary}")
    if inline_output:
        lines.append("**完整结果**:")
        lines.append(inline_output)

    return Panel(
        Markdown("\n\n".join(lines)),
        title=turn_title,
        subtitle=turn_id,
        border_style="blue",
        padding=(0, 1),
    )


def _append_turn_archive_record(turn_archive: list[dict], record: dict, limit: int) -> None:
    if not isinstance(turn_archive, list):
        return
    turn_archive.append(dict(record or {}))
    overflow = len(turn_archive) - max(1, int(limit))
    if overflow > 0:
        del turn_archive[:overflow]


def _build_archived_turn_detail_renderable_from_record(record: dict):
    turn_index = int(record.get("turn_index", 0) or 0)
    turn_title = (
        f"[bold cyan]Turn #{turn_index}[/bold cyan]" if turn_index > 0 else "[bold cyan]当前提问[/bold cyan]"
    )
    turn_id = str(record.get("turn_id") or "").strip()
    status = str(record.get("status") or "`未完成`").strip("`")
    user_input = str(record.get("detail_user_input") or record.get("user_input") or "").strip()
    output_text = str(record.get("detail_output") or "").strip()
    tool_tree_text = str(record.get("detail_tool_tree") or "").strip()
    todo_text = str(record.get("detail_todo") or "").strip()

    sections: list[object] = [
        Panel(Text(status), title="[bold yellow]状态[/bold yellow]", border_style="yellow", padding=(0, 1))
    ]
    if user_input:
        sections.append(
            Panel(
                Text(user_input),
                title="[bold blue]用户问题[/bold blue]",
                border_style="blue",
                padding=(0, 1),
            )
        )
    if todo_text:
        sections.append(
            Panel(
                Markdown(todo_text),
                title="[bold cyan]任务计划详情[/bold cyan]",
                border_style="cyan",
                padding=(0, 1),
            )
        )
    if tool_tree_text:
        sections.append(
            Panel(
                Markdown(tool_tree_text),
                title="[bold green]工具调用详情[/bold green]",
                border_style="green",
                padding=(0, 1),
            )
        )
    if output_text:
        sections.append(
            Panel(
                Markdown(output_text),
                title="[bold magenta]最终结果详情[/bold magenta]",
                border_style="magenta",
                padding=(0, 1),
            )
        )
    return Panel(
        Group(*sections),
        title=turn_title,
        subtitle=turn_id,
        border_style="blue",
        padding=(0, 1),
    )


def _snapshot_turn_container_content(buffer: list[str], state: dict) -> str:
    assistant_text = "".join(buffer).strip()
    status_line = _get_dynamic_status_line(state)
    tool_tree_text = _build_tool_tree_markdown(state.get("tool_history"))
    todo_text = _build_todo_markdown(state.get("todo_plan"))
    values = [
        str(state.get("turn_index", 0) or 0),
        str(state.get("turn_id") or ""),
        str(state.get("user_input") or ""),
        status_line,
        tool_tree_text,
        assistant_text,
        todo_text,
        "done" if state.get("graceful_done") else "",
    ]
    return "\n||\n".join(values)


def _build_turn_completion_label(state: dict) -> str:
    if state.get("has_error"):
        return "`执行失败`"
    plan = state.get("todo_plan")
    if isinstance(plan, dict):
        plan_status = str(plan.get("status") or "").strip()
        if plan_status == "cancelled":
            return "`已取消`"
        if plan_status == "interrupted":
            return "`已中断`"
        if plan_status == "failed":
            return "`部分失败`"
    history = state.get("tool_history", []) or []
    failed = [
        entry for entry in history if isinstance(entry, dict) and str(entry.get("status") or "") == "failed"
    ]
    if failed:
        return "`部分失败`"
    if state.get("graceful_done"):
        return "`已完成`"
    return "`未完成`"


def _build_turn_result_summary(state: dict, assistant_text: str) -> str:
    text = assistant_text.strip()
    if text:
        lines = _select_turn_summary_lines([line.strip() for line in text.splitlines() if line.strip()])
        if lines:
            return _summarize_runtime_text(" ".join(lines[:2]), limit=180)

    plan = state.get("todo_plan")
    if isinstance(plan, dict):
        stop_reason = _get_todo_terminal_reason(plan)
        if stop_reason:
            prefix = "中止原因" if str(plan.get("status") or "").strip() in {"cancelled", "interrupted"} else "原因"
            return _summarize_runtime_text(f"{prefix}: {stop_reason}", limit=180)

    history = state.get("tool_history", []) or []
    for entry in reversed(history):
        if not isinstance(entry, dict):
            continue
        detail = str(entry.get("detail") or "").strip()
        if detail:
            tool = str(entry.get("tool") or "unknown").strip()
            status = str(entry.get("status") or "").strip()
            prefix = "失败于" if status == "failed" else "结束于"
            return _summarize_runtime_text(f"{prefix} {tool}: {detail}", limit=180)

    return ""


def _select_turn_summary_lines(lines: list[str]) -> list[str]:
    if not lines:
        return []

    skipped_prefixes = (
        "抱歉",
        "总结如下",
        "如下：",
        "如下:",
        "以下是",
        "上面那条消息就是完整的报告内容",
        "现在重新呈现完整的报告",
    )
    filtered = [
        line
        for line in lines
        if line and line != "---" and not any(line.startswith(prefix) for prefix in skipped_prefixes)
    ]
    if not filtered:
        filtered = [line for line in lines if line and line != "---"]
    heading_index = next(
        (
            index
            for index, line in enumerate(filtered)
            if line.startswith(("# ", "## ", "### ")) or "报告" in line
        ),
        None,
    )
    if heading_index is not None:
        return filtered[heading_index:]
    return filtered


def _should_inline_turn_output(text: str) -> bool:
    normalized = str(text or "").strip()
    if not normalized:
        return False

    lines = [line.strip() for line in normalized.splitlines() if line.strip()]
    if len(lines) < 4:
        return False

    markdown_signal_count = sum(
        1
        for line in lines
        if line.startswith(("# ", "## ", "### ", "- ", "* ", "1. ", "2. ", "3. ", "> "))
        or line == "---"
        or "|" in line
    )
    if markdown_signal_count >= 2:
        return True
    return len(normalized) >= 280 and any(token in normalized for token in ("## ", "---", "报告", "总结"))


def _build_turn_todo_progress_summary(plan: dict | None) -> str:
    if not isinstance(plan, dict):
        return ""
    steps = [step for step in (plan.get("steps", []) or []) if isinstance(step, dict)]
    if not steps:
        return ""
    completed = sum(1 for step in steps if str(step.get("status") or "") == "completed")
    in_progress = sum(1 for step in steps if str(step.get("status") or "") == "in_progress")
    pending = sum(1 for step in steps if str(step.get("status") or "") == "pending")
    failed = sum(1 for step in steps if str(step.get("status") or "") == "failed")
    cancelled = sum(1 for step in steps if str(step.get("status") or "") == "cancelled")
    parts = [f"完成 {completed}"]
    if in_progress:
        parts.append(f"进行中 {in_progress}")
    if pending:
        parts.append(f"待处理 {pending}")
    if failed:
        parts.append(f"失败 {failed}")
    if cancelled:
        parts.append(f"取消 {cancelled}")
    return " | ".join(parts)


def _build_current_execution_markdown(state: dict) -> str:
    plan = state.get("todo_plan")
    lines = ["### 当前执行"]

    if isinstance(plan, dict):
        title = str(plan.get("title") or "").strip()
        if title:
            lines.append(f"**当前任务**: {_summarize_runtime_text(title, limit=120)}")

        in_progress_steps = [
            str(step.get("description") or "").strip()
            for step in (plan.get("steps", []) or [])
            if isinstance(step, dict) and str(step.get("status") or "") == "in_progress"
        ]
        pending_steps = [
            str(step.get("description") or "").strip()
            for step in (plan.get("steps", []) or [])
            if isinstance(step, dict) and str(step.get("status") or "") == "pending"
        ]
        if in_progress_steps:
            lines.append(
                f"**当前步骤**: {_summarize_runtime_text('；'.join(in_progress_steps), limit=120)}"
            )
        elif pending_steps:
            lines.append(f"**下一步**: {_summarize_runtime_text(pending_steps[0], limit=120)}")

        plan_status = _format_todo_plan_status(plan)
        if plan_status:
            lines.append(f"**计划状态**: {plan_status}")

        stop_reason = _get_todo_terminal_reason(plan)
        if stop_reason:
            lines.append(f"**终止原因**: {_summarize_runtime_text(stop_reason, limit=140)}")

        risk_hint = _infer_todo_risk_hint(plan)
        if risk_hint:
            lines.append(f"**风险提示**: {_summarize_runtime_text(risk_hint, limit=140)}")

    running_tool = _get_latest_running_tool_entry(state)
    if isinstance(running_tool, dict):
        iteration = int(running_tool.get("iteration", 0) or 0)
        tool_name = str(running_tool.get("tool") or "unknown").strip()
        elapsed = _compute_elapsed_seconds(
            running_tool.get("started_monotonic"),
            running_tool.get("ended_monotonic"),
        )
        tool_line = f"**当前工具**: 第 {iteration} 轮 · {tool_name}"
        if elapsed is not None:
            tool_line += f" · 已运行 {_format_elapsed_compact(elapsed)}"
        method = str(running_tool.get("method") or "").strip()
        if method:
            tool_line += f" · {_summarize_runtime_text(method, limit=60)}"
        lines.append(tool_line)
    else:
        status_line = _get_dynamic_status_line(state)
        if status_line:
            lines.append(f"**当前状态**: {_summarize_runtime_text(status_line, limit=140)}")

    return "\n".join(lines).strip()


def _summarize_runtime_text(text: str, limit: int = 120) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "..."


def _format_tool_end_detail(event: dict) -> str:
    summary = str(event.get("result_summary") or "").strip()
    if summary:
        return _summarize_runtime_text(summary, limit=140)

    result = str(event.get("result") or "").strip()
    if not result:
        return ""

    prefixes = (
        "工具执行错误:",
        "Tool error:",
        "Error:",
        "Exception:",
    )
    for prefix in prefixes:
        if result.startswith(prefix):
            result = result[len(prefix) :].strip()
            break

    return _summarize_runtime_text(result, limit=140)


def _register_tool_call_start(state: dict, event: dict) -> None:
    tool_name = str(event.get("tool") or event.get("name") or "unknown").strip()
    entry = {
        "id": str(event.get("id") or "").strip(),
        "iteration": int(state.get("iteration", 0) or 0),
        "tool": tool_name,
        "friendly_message": str(event.get("friendly_message") or "").strip(),
        "method": _describe_tool_method(event),
        "status": "running",
        "started_monotonic": time.monotonic(),
        "ended_monotonic": None,
        "detail": "",
    }
    state.setdefault("tool_history", []).append(entry)


def _register_tool_call_end(
    state: dict,
    event: dict,
    detail: str,
    *,
    final_status: str | None = None,
) -> None:
    target = _find_tool_history_entry(state, event)
    if target is None:
        target = {
            "id": str(event.get("id") or "").strip(),
            "iteration": int(state.get("iteration", 0) or 0),
            "tool": str(event.get("tool") or event.get("name") or "unknown").strip(),
            "friendly_message": "",
            "method": _describe_tool_method(event),
            "status": "completed",
            "started_monotonic": time.monotonic(),
            "ended_monotonic": None,
            "detail": "",
        }
        state.setdefault("tool_history", []).append(target)
    target["ended_monotonic"] = time.monotonic()
    target["detail"] = detail
    target["status"] = final_status or _classify_tool_end_status(event, detail)


def _classify_tool_end_status(event: dict, detail: str) -> str:
    if event.get("skipped"):
        return "skipped"
    if event.get("is_error"):
        return "failed"

    tool_name = str(event.get("tool") or event.get("name") or "").strip()
    normalized_detail = str(detail or "").strip()
    if (
        tool_name in {"create_todo", "update_todo_step", "complete_todo", "create_plan_file"}
        and normalized_detail.startswith(("❌", "⚠️"))
    ):
        return "failed"
    return "completed"


def _find_tool_history_entry(state: dict, event: dict) -> dict | None:
    tool_id = str(event.get("id") or "").strip()
    tool_name = str(event.get("tool") or event.get("name") or "").strip()
    history = list(state.get("tool_history") or [])
    if tool_id:
        for entry in reversed(history):
            if str(entry.get("id") or "") == tool_id:
                return entry
    for entry in reversed(history):
        if (
            str(entry.get("tool") or "") == tool_name
            and str(entry.get("status") or "") == "running"
        ):
            return entry
    return None


def _describe_tool_method(event: dict) -> str:
    friendly = str(event.get("friendly_message") or "").strip()
    if friendly:
        return _summarize_runtime_text(friendly, limit=80)
    args = event.get("args")
    if not isinstance(args, dict):
        return ""
    for key in ("action", "method", "url", "selector", "command", "path"):
        value = args.get(key)
        if value:
            return _summarize_runtime_text(f"{key}={value}", limit=80)
    return ""


def _format_tool_runtime_status(state: dict, tool_name: str) -> str:
    target = _find_tool_history_entry(state, {"tool": tool_name})
    if not target:
        return f"正在调用工具: {tool_name}"
    elapsed = _compute_elapsed_seconds(target.get("started_monotonic"), target.get("ended_monotonic"))
    parts = [
        f"第 {int(target.get('iteration', 0) or 0)} 轮",
        f"工具执行中: {tool_name}",
    ]
    if elapsed is not None:
        parts.append(f"耗时 {_format_elapsed_compact(elapsed)}")
    method = str(target.get("method") or "").strip()
    if method:
        parts.append(method)
    return " | ".join(parts)


def _format_agent_handoff_status(from_agent: str, to_agent: str, reason: str) -> str:
    route = f"{from_agent or 'main'} -> {to_agent or 'default'}"
    summary = _summarize_runtime_text(reason, limit=80) if reason else ""
    return f"任务已委派: {route}" + (f" | {summary}" if summary else "")


def _register_sub_agent_runtime(state: dict, event: dict) -> None:
    runtime = dict(event)
    runtime["received_monotonic"] = time.monotonic()
    state["sub_agent_runtime"] = runtime
    _sync_tool_history_from_sub_agent_state(state, runtime)


def _format_sub_agent_state_status(event: dict | None) -> str:
    if not isinstance(event, dict):
        return ""
    name = str(event.get("name") or event.get("agent_id") or "子 Agent").strip()
    status = str(event.get("status") or "").strip()
    iteration = event.get("iteration", 0)
    current_tool = str(event.get("current_tool_summary") or "").strip()
    tools_total = event.get("tools_total", 0)
    elapsed_s = _compute_sub_agent_elapsed(event)

    status_map = {
        "starting": "已启动",
        "running": "执行中",
        "completed": "已完成",
        "cancelled": "已取消",
        "timeout": "已超时",
        "error": "执行失败",
        "interrupted": "已中断",
        "idle": "空闲",
    }
    label = status_map.get(status, status or "执行中")
    details: list[str] = [f"{name} {label}"]
    if isinstance(iteration, int) and iteration > 0:
        details.append(f"轮次 {iteration}")
    if current_tool:
        details.append(f"当前工具 {current_tool}")
    elif isinstance(tools_total, int) and tools_total > 0:
        details.append(f"已调用 {tools_total} 个工具")
    reason = str(
        event.get("error")
        or event.get("reason")
        or event.get("message")
        or event.get("last_result_summary")
        or ""
    ).strip()
    if reason:
        details.append(_summarize_runtime_text(reason, limit=80))
    if isinstance(elapsed_s, (int, float)) and elapsed_s > 0:
        details.append(f"耗时 {_format_elapsed_compact(elapsed_s)}")
    return " | ".join(details)


def _get_dynamic_status_line(state: dict) -> str:
    running_tool = _get_latest_running_tool_entry(state)
    if isinstance(running_tool, dict):
        return _format_tool_runtime_status(state, str(running_tool.get("tool") or "unknown"))
    runtime = state.get("sub_agent_runtime")
    dynamic = _format_sub_agent_state_status(runtime)
    if dynamic:
        return dynamic
    return str(state.get("status_line", "") or "").strip()


def _build_tool_tree_markdown(
    history: list[dict] | None,
    max_entries: int | None = None,
    *,
    newest_first: bool = False,
) -> str:
    if not history:
        return ""

    entries = [entry for entry in history if isinstance(entry, dict)]
    hidden_count = 0
    if isinstance(max_entries, int) and max_entries > 0 and len(entries) > max_entries:
        hidden_count = len(entries) - max_entries
        entries = entries[-max_entries:]
    if newest_first:
        entries = list(reversed(entries))

    lines = ["### 工具调用树"]
    if newest_first:
        lines.append("")
        visible_count = len(entries)
        lines.append(
            f"**滚动条**: {_build_ascii_scrollbar(visible_count, visible_count + hidden_count)}"
        )
        lines.append(
            f"**视窗**: 最新 {visible_count} / 总数 {visible_count + hidden_count}"
            + (f"（更早 {hidden_count} 项已折叠，最新项置顶）" if hidden_count > 0 else "（全部可见）")
        )
    lines.extend(["", "```text"])

    current_iteration: int | None = None
    for index, entry in enumerate(entries):
        iteration = int(entry.get("iteration", 0) or 0)
        if iteration != current_iteration:
            if current_iteration is not None:
                lines.append("")
            current_iteration = iteration
            label = f"轮次 {iteration}" if iteration > 0 else "轮次 ?"
            lines.append(label)

        elapsed = _compute_elapsed_seconds(entry.get("started_monotonic"), entry.get("ended_monotonic"))
        tool = str(entry.get("tool") or "unknown").strip()
        status = _format_tool_tree_status(entry)
        duration = _format_elapsed_compact(elapsed) if elapsed is not None else "00:000"
        branch = "└─" if _is_last_tool_in_iteration(entries, index) else "├─"
        lines.append(f"{branch} {tool} | {status} | {duration}")

        method = str(entry.get("method") or "").strip()
        if method:
            child = "   " if branch == "└─" else "│  "
            lines.append(f"{child}├─ 调用: {method}")

        detail = str(entry.get("detail") or "").strip()
        if detail:
            child = "   " if branch == "└─" else "│  "
            lines.append(f"{child}└─ 结果: {detail}")

    if hidden_count > 0:
        lines.extend(["", f"... 已折叠更早的 {hidden_count} 次工具调用"])
    lines.extend(["```"])
    return "\n".join(lines).strip()


def _build_ascii_scrollbar(visible_count: int, total_count: int, width: int = 10) -> str:
    total = max(1, int(total_count or 0))
    visible = max(0, min(int(visible_count or 0), total))
    filled = min(width, max(1, round(width * (visible / total)))) if visible else 0
    return f"▕{'█' * filled}{'░' * (width - filled)}▏"


def _build_live_todo_markdown(plan: dict | None) -> str:
    if not isinstance(plan, dict):
        return ""
    title = str(plan.get("title", "") or "").strip()
    steps = [step for step in (plan.get("steps", []) or []) if isinstance(step, dict)]
    if not title and not steps:
        return ""

    completed = sum(1 for step in steps if str(step.get("status") or "") == "completed")
    in_progress_steps = [
        str(step.get("description") or "").strip()
        for step in steps
        if str(step.get("status") or "") == "in_progress"
    ]
    pending_steps = [
        str(step.get("description") or "").strip()
        for step in steps
        if str(step.get("status") or "") == "pending"
    ]
    parts = [f"### 任务计划：{title}", "", f"**进度**: 完成 {completed} / 总数 {len(steps)}"]
    if in_progress_steps:
        parts.append(f"**当前步骤**: {_summarize_runtime_text('；'.join(in_progress_steps), limit=120)}")
    elif pending_steps:
        parts.append(f"**下一步**: {_summarize_runtime_text(pending_steps[0], limit=120)}")
    return "\n".join(parts).strip()


def _build_live_output_preview(assistant_text: str) -> str:
    lines = [line.rstrip() for line in str(assistant_text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    tail = lines[-6:]
    preview = "\n".join(tail)
    if len(lines) > 6:
        preview = "...\n" + preview
    return _truncate_multiline_text(preview, max_chars=360)


def _truncate_multiline_text(text: str, max_chars: int = 360) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max_chars - 3].rstrip() + "..."


def _format_tool_tree_status(entry: dict) -> str:
    status = str(entry.get("status") or "running").strip()
    return {
        "completed": "已完成",
        "failed": "执行失败",
        "skipped": "已跳过",
        "running": "执行中",
    }.get(status, "执行中")


def _get_latest_running_tool_entry(state: dict) -> dict | None:
    history = state.get("tool_history", []) or []
    for entry in reversed(history):
        if isinstance(entry, dict) and str(entry.get("status") or "").strip() == "running":
            return entry
    return None


def _is_last_tool_in_iteration(entries: list[dict], index: int) -> bool:
    current_iteration = int(entries[index].get("iteration", 0) or 0)
    for future in entries[index + 1 :]:
        if int(future.get("iteration", 0) or 0) == current_iteration:
            return False
    return True


def _compute_elapsed_seconds(started_monotonic: float | None, ended_monotonic: float | None) -> float | None:
    if not isinstance(started_monotonic, (int, float)):
        return None
    end = ended_monotonic if isinstance(ended_monotonic, (int, float)) else time.monotonic()
    return max(0.0, float(end) - float(started_monotonic))


def _compute_sub_agent_elapsed(event: dict) -> float:
    base = float(event.get("elapsed_s") or 0.0)
    status = str(event.get("status") or "").strip()
    if status not in {"running", "starting"}:
        return base
    received = event.get("received_monotonic")
    if not isinstance(received, (int, float)):
        return base
    return max(0.0, base + (time.monotonic() - float(received)))


def _sync_tool_history_from_sub_agent_state(state: dict, event: dict) -> None:
    status = str(event.get("status") or "").strip()
    agent_key = str(
        event.get("state_key")
        or event.get("agent_id")
        or event.get("profile_id")
        or event.get("name")
        or "sub_agent"
    ).strip()
    if not agent_key:
        return

    current_tool = str(event.get("current_tool_summary") or "").strip()
    iteration = int(event.get("iteration", 0) or 0)
    reason = str(
        event.get("error")
        or event.get("reason")
        or event.get("message")
        or event.get("last_result_summary")
        or ""
    ).strip()

    states = state.setdefault("sub_agent_tool_states", {})
    snapshot = states.get(agent_key, {})
    active_id = str(snapshot.get("active_id") or "").strip()
    active_entry = _find_tool_history_entry(state, {"id": active_id}) if active_id else None

    if status in {"running", "starting"} and current_tool:
        if (
            active_entry is not None
            and str(active_entry.get("tool") or "").strip() == current_tool
            and int(active_entry.get("iteration", 0) or 0) == iteration
        ):
            return
        if active_entry is not None and str(active_entry.get("status") or "").strip() == "running":
            active_entry["status"] = "completed"
            active_entry["ended_monotonic"] = time.monotonic()
        entry_id = f"subagent:{agent_key}:{iteration}:{current_tool}:{len(state.get('tool_history', []))}"
        method = f"{event.get('name') or event.get('agent_id') or '子 Agent'}"
        state.setdefault("tool_history", []).append(
            {
                "id": entry_id,
                "iteration": iteration,
                "tool": current_tool,
                "friendly_message": "",
                "method": _summarize_runtime_text(method, limit=80),
                "status": "running",
                "started_monotonic": time.monotonic(),
                "ended_monotonic": None,
                "detail": "",
            }
        )
        states[agent_key] = {"active_id": entry_id}
        return

    if active_entry is None:
        return

    active_entry["ended_monotonic"] = time.monotonic()
    if reason:
        active_entry["detail"] = _summarize_runtime_text(reason, limit=140)
    active_entry["status"] = "failed" if status in {"error", "timeout"} else "completed"
    states[agent_key] = {}


def _format_elapsed_compact(elapsed_seconds: float) -> str:
    elapsed = max(0.0, float(elapsed_seconds))
    seconds = int(elapsed)
    millis = int(round((elapsed - seconds) * 1000))
    if millis == 1000:
        seconds += 1
        millis = 0
    return f"{seconds}:{millis:03d}"


def _needs_live_elapsed_refresh(state: dict) -> bool:
    runtime = state.get("sub_agent_runtime")
    if isinstance(runtime, dict) and str(runtime.get("status") or "").strip() in {"running", "starting"}:
        return True
    for entry in state.get("tool_history", []) or []:
        if isinstance(entry, dict) and str(entry.get("status") or "").strip() == "running":
            return True
    return False


def _normalize_todo_plan(plan: dict | None) -> dict | None:
    if not isinstance(plan, dict):
        return None
    normalized = deepcopy(plan)
    normalized["plan_id"] = str(normalized.get("plan_id") or normalized.get("id") or "").strip()
    normalized["restored"] = bool(normalized.get("restored", False))
    normalized["terminal_reason"] = str(
        normalized.get("terminal_reason")
        or normalized.get("stop_reason")
        or normalized.get("reason")
        or normalized.get("message")
        or ""
    ).strip()
    normalized["title"] = (
        normalized.get("title")
        or normalized.get("task_summary")
        or normalized.get("taskSummary")
        or "任务计划"
    )
    steps = []
    for index, step in enumerate(normalized.get("steps", []) or [], start=1):
        if not isinstance(step, dict):
            continue
        item = dict(step)
        item["id"] = str(item.get("id", f"step_{index}"))
        item["description"] = (
            str(
                item.get("description")
                or item.get("content")
                or item.get("label")
                or item.get("id", f"step_{index}")
            ).strip()
        )
        item["status"] = str(item.get("status", "pending") or "pending")
        steps.append(item)
    normalized["steps"] = steps
    normalized["status"] = str(normalized.get("status", "in_progress") or "in_progress")
    return normalized


def _apply_todo_step_update(plan: dict | None, event: dict) -> None:
    if not isinstance(plan, dict):
        return
    step_id = str(event.get("step_id") or event.get("stepId") or "").strip()
    status = str(event.get("status", "") or "").strip()
    if not status:
        return
    steps = plan.get("steps", []) or []
    target = None
    if step_id:
        candidate_ids = {step_id}
        if step_id.isdigit():
            candidate_ids.add(f"step_{step_id}")
        elif step_id.startswith("step_") and step_id[5:].isdigit():
            candidate_ids.add(step_id[5:])
        for step in steps:
            if str(step.get("id", "") or "") in candidate_ids:
                target = step
                break
    if target is None:
        step_idx = event.get("step_idx", event.get("stepIdx"))
        if isinstance(step_idx, int) and 0 <= step_idx < len(steps):
            target = steps[step_idx]
    if target is None:
        return
    target["status"] = status


def _apply_todo_completion(plan: dict | None, *, completed: bool) -> None:
    if not isinstance(plan, dict):
        return
    plan["status"] = "completed" if completed else "cancelled"
    for step in plan.get("steps", []) or []:
        status = str(step.get("status", "pending") or "pending")
        if completed and status in {"pending", "in_progress"}:
            step["status"] = "completed"
        elif not completed and status in {"pending", "in_progress"}:
            step["status"] = "cancelled"


def _format_todo_plan_status(plan: dict | None) -> str:
    if not isinstance(plan, dict):
        return ""
    status = str(plan.get("status") or "").strip()
    return {
        "pending": "待开始",
        "in_progress": "进行中",
        "completed": "已完成",
        "cancelled": "已取消",
        "interrupted": "已中断",
        "failed": "部分失败",
        "skipped": "已跳过",
    }.get(status, "")


def _get_todo_terminal_reason(plan: dict | None) -> str:
    if not isinstance(plan, dict):
        return ""
    return str(plan.get("terminal_reason") or plan.get("reason") or plan.get("message") or "").strip()


def _infer_todo_risk_hint(plan: dict | None) -> str:
    if not isinstance(plan, dict):
        return ""
    combined = " ".join(
        part
        for part in (
            str(plan.get("title") or "").strip(),
            _get_todo_terminal_reason(plan),
        )
        if part
    )
    if "验证码" in combined:
        return "如登录页出现验证码，任务将提前结束并返回现场结果。"
    return ""


def _build_todo_markdown(plan: dict | None) -> str:
    if not isinstance(plan, dict):
        return ""
    title = str(plan.get("title", "") or "").strip()
    plan_id = str(plan.get("plan_id", "") or "").strip()
    restored = bool(plan.get("restored", False))
    plan_status = _format_todo_plan_status(plan)
    stop_reason = _get_todo_terminal_reason(plan)
    risk_hint = _infer_todo_risk_hint(plan)
    steps = [step for step in (plan.get("steps", []) or []) if isinstance(step, dict)]
    if not title and not steps:
        return ""

    completed = sum(1 for step in steps if str(step.get("status", "") or "") == "completed")
    failed = sum(1 for step in steps if str(step.get("status", "") or "") == "failed")
    cancelled = sum(1 for step in steps if str(step.get("status", "") or "") == "cancelled")
    skipped = sum(1 for step in steps if str(step.get("status", "") or "") == "skipped")
    in_progress = sum(1 for step in steps if str(step.get("status", "") or "") == "in_progress")
    pending = sum(1 for step in steps if str(step.get("status", "") or "") == "pending")

    lines = [f"### 任务计划：{title or '未命名任务'}"]
    if restored:
        lines.append("`状态`: `已恢复计划`")
    if plan_status:
        lines.append(f"**计划状态**: {plan_status}")
    if stop_reason:
        lines.append(f"**终止原因**: {_summarize_runtime_text(stop_reason, limit=160)}")
    if risk_hint:
        lines.append(f"**风险提示**: {_summarize_runtime_text(risk_hint, limit=160)}")
    if plan_id:
        lines.append(f"`Plan ID`: `{plan_id}`")
    if restored or plan_id or plan_status or stop_reason or risk_hint:
        lines.append("")
    for step in steps:
        status = str(step.get("status", "") or "pending")
        checkbox = {
            "completed": "[x]",
            "failed": "[-]",
            "cancelled": "[-]",
            "skipped": "[/]",
            "in_progress": "[>]",
            "pending": "[ ]",
        }.get(status, "[ ]")
        desc = str(step.get("description", "") or step.get("id", "")).strip()
        lines.append(f"- {checkbox} {desc}")

    summary_bits = [
        f"完成 {completed}",
        f"进行中 {in_progress}",
        f"待处理 {pending}",
    ]
    if failed:
        summary_bits.append(f"失败 {failed}")
    if skipped:
        summary_bits.append(f"跳过 {skipped}")
    if cancelled:
        summary_bits.append(f"取消 {cancelled}")
    lines.append("")
    lines.append("**进度**: " + " | ".join(summary_bits))
    return "\n".join(lines).strip()


# ── Interactive event handlers (called with Live paused) ──


def _handle_security_confirm_interactive(event: dict, console: Console) -> None:
    """Prompt the user for a security confirmation decision in the terminal."""
    tool = event.get("tool", "")
    reason = event.get("reason", "")
    risk = event.get("risk_level", "medium")
    confirm_id = event.get("id", "")
    needs_sandbox = event.get("needs_sandbox", False)
    args = event.get("args") or {}

    # C14 / R4-5: belt-and-suspenders — main.py 已在 stdin 非 TTY 时拒绝
    # 进入 interactive 模式，理论上不会走到这里。但 stream_renderer 也可能
    # 被脚本/测试单独调用，且 ``Prompt.ask`` 在没有 TTY 时会回退到原始
    # ``input()`` 永久 block。这里再守一层：直接打印告警 + 不发 resolve，
    # 让 unattended 路径接管（owner 在 setup-center 上看到 pending_approval）。
    import sys as _sys

    try:
        _has_tty = bool(_sys.stdin.isatty())
    except (ValueError, OSError):
        _has_tty = False
    if not _has_tty:
        console.print(
            f"[yellow]⚠️ 非交互终端无法对 {tool!r} 做安全确认 "
            f"(confirm_id={confirm_id[:8]}…)；请在 setup-center "
            f"或通过 /api/pending_approvals 由 owner 处理。[/yellow]"
        )
        return

    color = "red" if risk.lower() in ("high", "critical") else "yellow"

    info_lines = [f"工具: {tool}", f"原因: {reason}", f"风险等级: {risk}"]
    cmd = args.get("command") or args.get("code") or args.get("url", "")
    if cmd:
        info_lines.append(f"参数: {str(cmd)[:200]}")

    console.print()
    console.print(
        Panel(
            "\n".join(info_lines),
            title="[bold]🔒 安全确认[/bold]",
            border_style=color,
        )
    )

    choices = ["y", "n", "e", "a"]
    hint = (
        "[bold]y[/bold]=允许一次  [bold]n[/bold]=拒绝  "
        "[bold]e[/bold]=会话允许  [bold]a[/bold]=始终允许"
    )
    if needs_sandbox:
        choices.append("s")
        hint += "  [bold]s[/bold]=沙箱执行"

    decision_str = Prompt.ask(hint, choices=choices, default="n")
    decision_map = {
        "y": "allow_once",
        "n": "deny",
        "e": "allow_session",
        "a": "allow_always",
        "s": "sandbox",
    }
    decision = decision_map.get(decision_str, "deny")

    try:
        from ..core.policy_v2 import apply_resolution

        found = apply_resolution(confirm_id, decision)
        if found:
            labels = {
                "allow_once": "✅ 已允许（一次）",
                "allow_session": "✅ 已允许（本次会话）",
                "allow_always": "✅ 已允许（始终）",
                "deny": "❌ 已拒绝",
                "sandbox": "🔒 沙箱执行",
            }
            console.print(f"  {labels.get(decision, decision)}")
        else:
            console.print(f"  [yellow]⚠️ 确认项已过期或不存在 (id={confirm_id[:8]}…)[/yellow]")
    except Exception as exc:
        console.print(f"  [red]确认处理失败: {exc}[/red]")


def _handle_ask_user_interactive(event: dict, console: Console) -> None:
    """Display ask_user prompt with structured options for CLI users."""
    question = event.get("question", "")
    options = event.get("options", [])

    console.print()
    console.print(Panel(question, title="[bold]❓ 需要你的回答[/bold]", border_style="blue"))

    if options:
        for i, opt in enumerate(options, 1):
            label = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
            console.print(f"  [cyan][{i}][/cyan] {label}")
        console.print()
    console.print("  [dim]请在下次输入中回答（输入序号或直接输入文字）[/dim]")
