"""
UBIK-TUI — Full-screen terminal UI.

3-panel layout:
  [Sessions] | [Chat + streaming] | [Context / QUBIK stats]

Engine: run_headless() from ubik_cli — zero duplication.
Styles: ubik.tcss — edit that file, not this one.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
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
from textual.worker import get_current_worker

from ubik_cli.session import Session
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
    """Left panel — session list."""

    def compose(self) -> ComposeResult:
        yield Label("  SESSIONS", classes="panel-header")
        yield ListView(id="session-list")
        yield Label("  + New session", id="new-session")

    def refresh_sessions(self, current_id: str | None = None) -> None:
        lv = self.query_one("#session-list", ListView)
        self._session_ids: list[str] = []
        sessions = Session.list_all()[:30]
        items = []
        for s in sessions:
            self._session_ids.append(s["id"])
            is_current = s["id"] == current_id
            date = s.get("date", "—")
            title = s.get("title") or "Nouvelle session"
            if is_current:
                label = f"[bold #58a6ff]▶ {date:<8}[/bold #58a6ff] [bold #e6edf3]{title}[/bold #e6edf3]"
            else:
                label = f"[#444d56]  {date:<8}[/#444d56] [#8b949e]{title}[/#8b949e]"
            items.append(ListItem(Label(label, markup=True)))
        lv.clear()
        for item in items:
            lv.append(item)


class ContextPanel(Vertical):
    """Right panel — QUBIK stats."""

    _stats: reactive[dict] = reactive({})

    def compose(self) -> ComposeResult:
        yield Label("  CONTEXT", classes="panel-header")
        yield Static("", id="ctx-stats", markup=True)

    def update_stats(self, stats: dict) -> None:
        self._stats = stats
        lines = []

        if stats.get("intent_type"):
            lines.append(f"[#444d56]Intent[/#444d56]  [#e6edf3]{stats['intent_type']}[/#e6edf3]")
        if stats.get("complexity"):
            c = stats["complexity"]
            color = "#3fb950" if c <= 3 else "#d29922" if c <= 6 else "#f85149"
            lines.append(f"[#444d56]Complex[/#444d56] [{color}]{'█' * c}{'░' * (10 - c)}[/{color}] {c}/10")
        if stats.get("duration_ms"):
            ms = stats["duration_ms"]
            color = "#3fb950" if ms < 500 else "#d29922" if ms < 1500 else "#f85149"
            lines.append(f"[#444d56]QUBIK[/#444d56]   [{color}]{ms}ms[/{color}]")
        if stats.get("skills_count"):
            lines.append(f"[#444d56]Skills[/#444d56]  [#58a6ff]{stats['skills_count']}[/#58a6ff]")
        if stats.get("tools_count"):
            lines.append(f"[#444d56]Tools[/#444d56]   [#58a6ff]{stats['tools_count']}[/#58a6ff]")
        if stats.get("cortex_injected"):
            lines.append("[#444d56]Cortex[/#444d56]  [#3fb950]● active[/#3fb950]")
        if stats.get("model"):
            lines.append(f"[#444d56]Model[/#444d56]   [#8b949e]{stats['model']}[/#8b949e]")
        if stats.get("total_tokens"):
            lines.append(f"[#444d56]Tokens[/#444d56]  [#8b949e]{stats['total_tokens']:,}[/#8b949e]")

        self.query_one("#ctx-stats", Static).update(
            "\n".join(lines) if lines else "[#444d56]Waiting…[/#444d56]"
        )

    def clear_stats(self) -> None:
        self._stats = {}
        self.query_one("#ctx-stats", Static).update("[#444d56]Waiting…[/#444d56]")


class ChatPanel(Vertical):
    """Center panel — conversation log + streaming + input."""

    _stream_buf: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield Label("  CHAT", classes="panel-header", id="chat-header")
        yield RichLog(id="chat-log", highlight=True, markup=True, wrap=True)
        yield Static("", id="stream-box", markup=True)
        yield Input(placeholder="Message…  (Enter↵ envoyer  Ctrl+C annuler)", id="chat-input")

    def watch__stream_buf(self, value: str) -> None:
        box = self.query_one("#stream-box", Static)
        box.update(f"[bold #3fb950]UBIK[/bold #3fb950]  {value}" if value else "")

    def set_session_title(self, title: str) -> None:
        self.query_one("#chat-header", Label).update(f"  {title.upper()}")

    def append_user(self, text: str) -> None:
        self.query_one("#chat-log", RichLog).write(
            f"\n[bold #58a6ff]You[/bold #58a6ff]  [#e6edf3]{text}[/#e6edf3]"
        )

    def append_token(self, text: str) -> None:
        self._stream_buf += text

    def append_system(self, text: str, style: str = "dim") -> None:
        self.query_one("#chat-log", RichLog).write(f"[{style}]{text}[/{style}]")

    def append_tool(self, name: str, is_result: bool, content: str) -> None:
        log = self.query_one("#chat-log", RichLog)
        if is_result:
            short = content[:100] + ("…" if len(content) > 100 else "")
            log.write(f"[#444d56]  ↳ {short}[/#444d56]")
        else:
            log.write(f"\n[#d29922]⚙ {name}[/#d29922]")

    def start_assistant_turn(self) -> None:
        self._stream_buf = ""

    def commit_stream(self) -> None:
        text = self._stream_buf
        self._stream_buf = ""
        if text:
            self.query_one("#chat-log", RichLog).write(
                f"\n[bold #3fb950]UBIK[/bold #3fb950]  [#e6edf3]{text}[/#e6edf3]"
            )

    def focus_input(self) -> None:
        self.query_one("#chat-input", Input).focus()


# ── Main App ──────────────────────────────────────────────────

class UbikTUI(App):
    """UBIK-TUI — full-screen terminal interface for UBIK-CLI."""

    TITLE = "UBIK-TUI"
    CSS_PATH = "ubik.tcss"

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
        chat.set_session_title("chat")
        chat.append_system(
            f"[#444d56]─── Session {self._session.id}  {datetime.now().strftime('%H:%M')} ───[/#444d56]"
        )
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
        title = self._session.title or "session"
        chat.set_session_title(title)
        for msg in self._session.messages:
            role = msg.get("role", "")
            content = (msg.get("content") or "")
            if role == "user":
                log.write(f"\n[bold #58a6ff]You[/bold #58a6ff]  [#e6edf3]{content[:300]}[/#e6edf3]")
            elif role == "assistant":
                log.write(f"\n[bold #3fb950]UBIK[/bold #3fb950]  [#e6edf3]{content[:600]}[/#e6edf3]")
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
                "[#58a6ff]/new[/#58a6ff] nouvelle session  "
                "[#58a6ff]/clear[/#58a6ff] vider  "
                "[#58a6ff]Ctrl+N[/#58a6ff] new  "
                "[#58a6ff]Ctrl+Q[/#58a6ff] quitter"
            )
        else:
            chat.append_system(f"[#f85149]Commande inconnue : {cmd}[/#f85149]")

    # ── Streaming worker ──────────────────────────────────────

    def _send(self, text: str) -> None:
        self._generating = True
        chat = self.query_one(ChatPanel)
        chat.append_user(text)
        history = list(self._session.messages)
        self._session.messages.append({"role": "user", "content": text})
        self.run_worker(
            lambda: self._stream_worker(text, history),
            thread=True,
            exclusive=True,
            name="stream",
        )

    def _stream_worker(self, prompt: str, history: list) -> None:
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
                        self.post_message(Token("\x00START"))
                    full_parts.append(event.content)
                    self.post_message(Token(event.content))

                elif et == "tool_call":
                    self.post_message(ToolEvent(event.name or "", is_result=False))

                elif et == "tool_result":
                    result_text = str(getattr(event, "result", "") or "")[:120]
                    self.post_message(ToolEvent(event.name or "", is_result=True, content=result_text))

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

    # ── Message handlers ──────────────────────────────────────

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
        self.query_one(ChatPanel).append_system(f"[#f85149]Erreur : {message.error}[/#f85149]")
        self._generating = False

    def on_stream_done(self, message: StreamDone) -> None:
        chat = self.query_one(ChatPanel)
        chat.commit_stream()

        if message.full_text:
            self._session.messages.append({"role": "assistant", "content": message.full_text})
            if not self._session.title:
                from ubik_cli.session import _generate_title
                self._session.title = _generate_title(self._session.messages)
                chat.set_session_title(self._session.title)

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
        idx = event.list_view.index
        ids = getattr(self.query_one(SessionPanel), "_session_ids", [])
        if idx is not None and idx < len(ids):
            session_id = ids[idx]
            if session_id != (self._session.id if self._session else ""):
                self._load_session(session_id)

    # ── Actions ───────────────────────────────────────────────

    def action_new_session(self) -> None:
        self._new_session()

    def action_clear_chat(self) -> None:
        self.query_one(ChatPanel).query_one("#chat-log", RichLog).clear()

    def action_quit(self) -> None:
        self.exit()
