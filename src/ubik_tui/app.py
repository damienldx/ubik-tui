"""
UBIK-TUI — Full-screen terminal UI.

3-panel layout:
  [Sessions] | [Chat + streaming] | [Context / QUBIK stats]

Engine: run_headless() from ubik_cli — zero duplication.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.events import Mount
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import (
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    RichLog,
    Static,
)
from textual.worker import Worker, get_current_worker

from ubik_cli.session import Session, SESSIONS_DIR
from ubik_cli.tools import TOOLS_OPENAI


# ── Messages (TUI ↔ worker) ───────────────────────────────────

class Token(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class QubikStats(Message):
    def __init__(self, stats: dict) -> None:
        super().__init__()
        self.stats = stats


class StreamDone(Message):
    def __init__(self, full_text: str, usage: dict) -> None:
        super().__init__()
        self.full_text = full_text
        self.usage = usage


class StreamError(Message):
    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error


class ToolEvent(Message):
    def __init__(self, name: str, is_result: bool = False, content: str = "") -> None:
        super().__init__()
        self.name = name
        self.is_result = is_result
        self.content = content


# ── Panels ────────────────────────────────────────────────────

class SessionPanel(Vertical):
    """Left panel — session list + [New] button."""

    DEFAULT_CSS = """
    SessionPanel {
        width: 22;
        border-right: solid $accent-darken-2;
        padding: 0 1;
    }
    SessionPanel Label.panel-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }
    SessionPanel ListView {
        height: 1fr;
        border: none;
    }
    SessionPanel #new-session {
        margin-top: 1;
        color: $success;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("Sessions", classes="panel-title")
        yield ListView(id="session-list")
        yield Label("[ + New ]", id="new-session")

    def refresh_sessions(self, current_id: str | None = None) -> None:
        lv = self.query_one("#session-list", ListView)
        self._session_ids: list[str] = []
        sessions = Session.list_all()[:30]
        items = []
        for s in sessions:
            self._session_ids.append(s["id"])
            is_current = s["id"] == current_id
            date = s.get("date", "—")
            title = s.get("title", "—") or "—"
            marker = "▶" if is_current else " "
            # Fixed-width date (6 chars), then title
            label = f"{marker} [dim]{date:<8}[/dim] {title}"
            items.append(ListItem(Label(label, markup=True)))
        lv.clear()
        for item in items:
            lv.append(item)


class ContextPanel(Vertical):
    """Right panel — QUBIK stats + cortex info."""

    DEFAULT_CSS = """
    ContextPanel {
        width: 26;
        border-left: solid $accent-darken-2;
        padding: 0 1;
    }
    ContextPanel Label.panel-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }
    ContextPanel Static {
        color: $text-muted;
        height: auto;
    }
    """

    _stats: reactive[dict] = reactive({})

    def compose(self) -> ComposeResult:
        yield Label("Context", classes="panel-title")
        yield Static("", id="ctx-stats")

    def update_stats(self, stats: dict) -> None:
        self._stats = stats
        lines = []
        if stats.get("intent_type"):
            lines.append(f"Intent : {stats['intent_type']}")
        if stats.get("complexity"):
            lines.append(f"Complexity : {stats['complexity']}")
        if stats.get("duration_ms"):
            lines.append(f"QUBIK : {stats['duration_ms']}ms")
        if stats.get("skills_count"):
            lines.append(f"Skills : {stats['skills_count']}")
        if stats.get("tools_count"):
            lines.append(f"Tools : {stats['tools_count']}")
        if stats.get("cortex_injected"):
            lines.append("Cortex : ✓")
        if stats.get("model"):
            lines.append(f"Model : {stats['model']}")
        if stats.get("total_tokens"):
            lines.append(f"Tokens : {stats['total_tokens']:,}")
        self.query_one("#ctx-stats", Static).update("\n".join(lines) if lines else "Waiting…")

    def clear_stats(self) -> None:
        self._stats = {}
        self.query_one("#ctx-stats", Static).update("Waiting…")


class ChatPanel(Vertical):
    """Center panel — conversation log + streaming widget + input bar."""

    DEFAULT_CSS = """
    ChatPanel {
        width: 1fr;
        padding: 0 1;
    }
    ChatPanel RichLog {
        height: 1fr;
        border: none;
        scrollbar-gutter: stable;
    }
    ChatPanel #stream-box {
        height: auto;
        min-height: 1;
        color: $text;
        background: $background;
        padding: 0 0;
    }
    ChatPanel Input {
        margin-top: 1;
        border: tall $accent-darken-2;
    }
    """

    _stream_buf: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)
        yield Static("", id="stream-box", markup=True)
        yield Input(placeholder="Message… (Enter to send, Ctrl+C to cancel)", id="chat-input")

    def watch__stream_buf(self, value: str) -> None:
        box = self.query_one("#stream-box", Static)
        if value:
            box.update(f"[bold green]UBIK[/bold green]  {value}")
        else:
            box.update("")

    def append_user(self, text: str) -> None:
        self.query_one("#chat-log", RichLog).write(f"\n[bold cyan]You[/bold cyan]  {text}")

    def append_token(self, text: str) -> None:
        self._stream_buf += text

    def append_system(self, text: str, style: str = "dim") -> None:
        self.query_one("#chat-log", RichLog).write(f"[{style}]{text}[/{style}]")

    def append_tool(self, name: str, is_result: bool, content: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        if is_result:
            short = content[:80] + ("…" if len(content) > 80 else "")
            log.write(f"[dim]  ↳ {name}: {short}[/dim]")
        else:
            log.write(f"\n[yellow]⚙ {name}[/yellow]")

    def start_assistant_turn(self) -> None:
        self._stream_buf = ""

    def commit_stream(self) -> None:
        """Move completed stream buffer into the RichLog."""
        text = self._stream_buf
        self._stream_buf = ""
        if text:
            self.query_one("#chat-log", RichLog).write(
                f"\n[bold green]UBIK[/bold green]  {text}"
            )

    def focus_input(self) -> None:
        self.query_one("#chat-input", Input).focus()


# ── Main App ──────────────────────────────────────────────────

class UbikTUI(App):
    """UBIK-TUI — full-screen terminal interface for UBIK-CLI."""

    TITLE = "UBIK-TUI"
    CSS = """
    Screen {
        background: $background;
    }
    Horizontal#main {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("ctrl+n", "new_session", "New"),
        Binding("ctrl+l", "clear_chat", "Clear"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, agent, engine_url: str = "http://localhost:8801") -> None:
        super().__init__()
        self._agent = agent
        self._engine_url = engine_url
        self._session: Optional[Session] = None
        self._generating = False

    # ── Layout ────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            yield SessionPanel(id="sessions")
            yield ChatPanel(id="chat")
            yield ContextPanel(id="context")
        yield Footer()

    def on_mount(self) -> None:
        self._new_session()
        self.query_one(ChatPanel).focus_input()

    # ── Session management ────────────────────────────────────

    def _new_session(self) -> None:
        self._session = Session()
        chat = self.query_one(ChatPanel)
        chat.query_one("#chat-log", RichLog).clear()
        chat.append_system(f"Session {self._session.id} — {datetime.now().strftime('%H:%M')}", "dim")
        self.query_one(SessionPanel).refresh_sessions(self._session.id)
        self.query_one(ContextPanel).clear_stats()

    def _load_session(self, session_id: str) -> None:
        try:
            self._session = Session.load(session_id)
        except Exception:
            return
        chat = self.query_one(ChatPanel)
        log = chat.query_one("#chat-log", RichLog)
        log.clear()
        for msg in self._session.messages:
            role = msg.get("role", "")
            content = msg.get("content", "") or ""
            if role == "user":
                log.write(f"\n[bold cyan]You[/bold cyan]  {content[:200]}")
            elif role == "assistant":
                log.write(f"\n[bold green]UBIK[/bold green]  {content[:400]}")
        self.query_one(SessionPanel).refresh_sessions(session_id)
        self.query_one(ContextPanel).clear_stats()

    # ── Input handling ────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self._generating:
            return
        event.input.value = ""

        if text.startswith("/"):
            self._handle_slash(text)
            return

        self._send(text)

    def _handle_slash(self, cmd: str) -> None:
        chat = self.query_one(ChatPanel)
        if cmd in ("/new", "/reset", "/clear"):
            self._new_session()
        elif cmd == "/help":
            chat.append_system(
                "/new   new session\n/clear  clear display\n/help   this help\nCtrl+N  new  Ctrl+Q  quit",
                "cyan",
            )
        else:
            chat.append_system(f"Unknown command: {cmd}", "red")

    # ── Streaming worker ──────────────────────────────────────

    def _send(self, text: str) -> None:
        self._generating = True
        chat = self.query_one(ChatPanel)
        chat.append_user(text)

        # Snapshot messages before adding user turn
        history = list(self._session.messages)
        self._session.messages.append({"role": "user", "content": text})

        self.run_worker(
            lambda: self._stream_worker(text, history),
            thread=True,
            exclusive=True,
            name="stream",
        )

    def _stream_worker(self, prompt: str, history: list) -> None:
        """Runs in a thread — bridges sync run_headless() to textual messages."""
        worker = get_current_worker()
        from ubik_cli.headless import run_headless

        full_parts: list[str] = []
        usage: dict = {}
        assistant_started = False

        try:
            for event in run_headless(
                agent=self._agent,
                prompt=prompt,
                messages=history,
                cwd=str(Path.home()),
                lightweight=False,
            ):
                if worker.is_cancelled:
                    break

                et = event.type

                if et == "token" and event.content:
                    if not assistant_started:
                        assistant_started = True
                        self.post_message(Token("\x00START"))  # sentinel
                    full_parts.append(event.content)
                    self.post_message(Token(event.content))

                elif et == "tool_call":
                    self.post_message(ToolEvent(event.name or "", is_result=False))

                elif et == "tool_result":
                    result_text = ""
                    if hasattr(event, "result"):
                        result_text = str(event.result or "")[:120]
                    self.post_message(ToolEvent(
                        event.name or "", is_result=True, content=result_text
                    ))

                elif et == "error":
                    self.post_message(StreamError(event.error or "Unknown error"))

                elif et == "done":
                    usage = event.usage or {}
                    qm = event.qubik or {}
                    ext = qm.get("extraction", {})
                    if qm:
                        self.post_message(QubikStats({
                            "intent_type": ext.get("type", "") or qm.get("intent_type", ""),
                            "complexity": ext.get("complexity", 0) or qm.get("complexity", 0),
                            "duration_ms": qm.get("duration_ms", 0),
                            "skills_count": len(qm.get("skills", [])),
                            "tools_count": len(qm.get("tools", [])),
                            "cortex_injected": qm.get("cortex_injected", False),
                        }))

        except Exception as exc:
            self.post_message(StreamError(str(exc)))

        self.post_message(StreamDone("".join(full_parts), usage))

    # ── Message handlers (main thread) ───────────────────────

    def on_token(self, message: Token) -> None:
        chat = self.query_one(ChatPanel)
        if message.text == "\x00START":
            chat.start_assistant_turn()
        else:
            chat.append_token(message.text)

    def on_qubik_stats(self, message: QubikStats) -> None:
        self.query_one(ContextPanel).update_stats(message.stats)

    def on_tool_event(self, message: ToolEvent) -> None:
        self.query_one(ChatPanel).append_tool(
            message.name, message.is_result, message.content
        )

    def on_stream_error(self, message: StreamError) -> None:
        self.query_one(ChatPanel).append_system(f"Error: {message.error}", "red bold")
        self._generating = False

    def on_stream_done(self, message: StreamDone) -> None:
        chat = self.query_one(ChatPanel)
        chat.commit_stream()

        if message.full_text:
            self._session.messages.append({"role": "assistant", "content": message.full_text})

        usage = message.usage
        if usage:
            ctx = self.query_one(ContextPanel)
            stats = dict(ctx._stats)
            stats["model"] = usage.get("model", "")
            stats["total_tokens"] = usage.get("total_tokens", 0)
            ctx.update_stats(stats)

        self._session.save()
        self.query_one(SessionPanel).refresh_sessions(self._session.id)
        self._generating = False
        chat.focus_input()

    # ── List view: session click ──────────────────────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        lv = self.query_one("#session-list", ListView)
        idx = event.list_view.index
        sessions_panel = self.query_one(SessionPanel)
        ids = getattr(sessions_panel, "_session_ids", [])
        if idx is not None and idx < len(ids):
            session_id = ids[idx]
            if session_id != (self._session.id if self._session else ""):
                self._load_session(session_id)

    def on_label_clicked(self, event) -> None:
        if hasattr(event, "label") and event.label and "+" in str(event.label):
            self._new_session()

    # ── Actions ───────────────────────────────────────────────

    def action_new_session(self) -> None:
        self._new_session()

    def action_clear_chat(self) -> None:
        self.query_one(ChatPanel).query_one("#chat-log", RichLog).clear()

    def action_quit(self) -> None:
        self.exit()
