"""The sections of a document as a full-screen checklist in the terminal: a tick or an x on every row, documents folded,
the names of what is ticked, how many decks when several top sections are ticked, and the Enter and Cancel buttons
below. Nothing is decided until Enter is pressed on its button."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType
from prompt_toolkit.output.defaults import create_output
from prompt_toolkit.styles import Style

TICK, CROSS = "✓", "✗"
FOLDED, OPEN = "▸", "▾"
PAGE_ROWS = 10           # rows PageUp and PageDown move
INDENT = 3               # columns a nested row is indented
BUTTONS = ("Enter", "Cancel")
OUTPUT = "Output"        # the row choosing one deck, or one per top section, between the list and the buttons
SUMMARY_NAMES = 3        # ticked top sections named on the status line
SUMMARY_TITLE_CHARS = 32 # how much of each name
HELP = "↑↓ move   Space tick/untick   → open   ← close   A all/none   Tab buttons   Esc cancel"
STYLE = Style.from_dict({"tick": "ansigreen bold", "cross": "ansired", "cursor": "reverse", "help": "ansibrightblack",
                         "status": "ansicyan", "button": "bold", "button.cursor": "reverse bold"})


def pages_note(item: dict) -> str:
    """A row's page count, when it has one."""
    return f"  ({item['pages']} p)" if item.get("pages") is not None else ""


class Checklist:
    """The rows' state: which are ticked and open, where the cursor is (a row, or a button after the last row)."""

    def __init__(self, items: List[dict], each: bool = False, each_label: str = "One deck per top section"):
        self.items = items
        self.each, self.each_label = each, each_label     # one deck per ticked top section, or one for all
        self.on: Dict[int, bool] = {i: False for i in range(len(items))}
        self.open: set = set()
        self.cursor = 0
        self.status = ""
        at = {e["num"]: i for i, e in enumerate(items)}
        self.parent = {i: at.get(e["num"].rpartition(".")[0]) for i, e in enumerate(items)}
        self.kids: Dict[Optional[int], List[int]] = {}
        for i, p in self.parent.items():
            self.kids.setdefault(p, []).append(i)

    def rows(self) -> List[int]:
        """The rows on screen: the top level and what is under an open row."""
        out: List[int] = []

        def walk(p: Optional[int]) -> None:
            for i in self.kids.get(p, []):
                out.append(i)
                if i in self.open:
                    walk(i)
        walk(None)
        return out

    def under(self, i: int) -> List[int]:
        out = list(self.kids.get(i, []))
        for k in list(out):
            out += self.under(k)
        return out

    def ancestors(self, i: int) -> List[int]:
        out: List[int] = []
        while self.parent[i] is not None:
            i = self.parent[i]
            out.append(i)
        return out

    def toggle(self, i: int) -> None:
        """Tick or untick a row with all that is under it; a row ticked deep inside a ticked section ticks the rows
        between them, since a section left out takes what is under it along."""
        new = not self.on[i]
        for k in [i, *self.under(i)]:
            self.on[k] = new
        if new:
            chain = self.ancestors(i)[::-1]
            first = next((n for n, a in enumerate(chain) if self.on[a]), None)
            if first is not None:
                for a in chain[first:]:
                    self.on[a] = True
        self.status = ""

    def toggle_all(self) -> None:
        new = not all(self.on.values())
        self.on = {i: new for i in self.on}
        self.status = ""
        self.cursor = min(self.cursor, len(self.rows()) + len(self.controls()) - 1)

    def ticked_tops(self) -> List[int]:
        """The top sections with a tick in them."""
        return [t for t in self.kids.get(None, []) if any(self.on[k] for k in [t, *self.under(t)])]

    def controls(self) -> Tuple[str, ...]:
        """What the cursor reaches after the last row."""
        return ((OUTPUT, *BUTTONS) if len(self.ticked_tops()) > 1 else BUTTONS)

    def choice(self) -> Tuple[Optional[List[int]], List[int]]:
        """(heading ids picked, heading ids left out), as `scope_menu.parse` gives them. Picked None: the whole
        document. ValueError when nothing is ticked."""
        picked, left = [], []
        for i, e in enumerate(self.items):
            above = self.ancestors(i)
            inside = any(self.on[a] for a in above)
            if self.on[i] and not inside:
                picked.append(i)
            elif not self.on[i] and inside and all(self.on[a] for a in above[:1]):
                left.append(i)
        if not picked:
            raise ValueError("Nothing is ticked: tick a section, or Cancel.")
        hid = lambda rows: [self.items[r]["hid"] for r in rows]
        if set(self.kids.get(None, [])) <= set(picked):
            return None, hid(left)
        return hid(picked), hid(left)

    def summary(self) -> str:
        """What is ticked, by name, so the choice is seen before Enter."""
        tops = self.ticked_tops()
        if not tops:
            return "Nothing ticked"
        names = [f"{self.items[t]['num']} {self.items[t]['title'][:SUMMARY_TITLE_CHARS]}" for t in tops[:SUMMARY_NAMES]]
        more = f" +{len(tops) - SUMMARY_NAMES} more" if len(tops) > SUMMARY_NAMES else ""
        return f"Ticked: {'; '.join(names)}{more}  ({sum(self.on.values())} of {len(self.items)} sections)"

    def move(self, step: int) -> None:
        self.cursor = max(0, min(len(self.rows()) + len(self.controls()) - 1, self.cursor + step))

    def on_button(self) -> Optional[str]:
        n = self.cursor - len(self.rows())
        return self.controls()[n] if n >= 0 else None

    def fold(self, i: int, opened: bool) -> None:
        if opened and self.kids.get(i):
            self.open.add(i)
        elif not opened:
            self.open.discard(i)


def _rows_text(state: Checklist, app_box: list) -> StyleAndTextTuples:
    """The rows as text with their click and scroll handlers; the cursor row marks where the window scrolls to."""
    def handler(row: int, what: str):
        def handle(event):
            if event.event_type == MouseEventType.SCROLL_UP:
                state.move(-1)
            elif event.event_type == MouseEventType.SCROLL_DOWN:
                state.move(1)
            elif event.event_type == MouseEventType.MOUSE_UP:
                state.cursor = state.rows().index(row)
                if what == "box":
                    state.toggle(row)
                elif what == "fold":
                    state.fold(row, row not in state.open)
            else:
                return NotImplemented
            app_box[0].invalidate()
        return handle

    out: StyleAndTextTuples = []
    for n, i in enumerate(state.rows()):
        e = state.items[i]
        here = n == state.cursor
        kids = state.under(i)
        fold = (OPEN if i in state.open else FOLDED) if kids else " "
        ticked = sum(state.on[k] for k in kids)
        count = f"   [{ticked}/{len(kids)} ticked]" if kids and i not in state.open and ticked else ""
        if here:
            out.append(("[SetCursorPosition]", ""))
        base = "class:cursor" if here else ""
        out.append((base, " " * (INDENT * (e["level"] - 1))))
        out.append((base, f"{fold} ", handler(i, "fold")))
        mark = TICK if state.on[i] else CROSS
        out.append((f"{base} class:{'tick' if state.on[i] else 'cross'}", f"[{mark}] ", handler(i, "box")))
        out.append((base, f"{e['num']}  {e['title']}{pages_note(e)}{count}", handler(i, "row")))
        out.append(("", "\n"))
    return out


def build(state: Checklist, **session) -> Application:
    """The application; it returns (picked, left, one deck per top section), or None when cancelled."""
    keys = KeyBindings()
    app_box: list = []

    def finish(event=None) -> None:
        name = state.on_button()
        if name == BUTTONS[1]:
            app_box[0].exit(result=None)
        elif name == BUTTONS[0]:
            try:
                app_box[0].exit(result=(*state.choice(), state.each))
            except ValueError as exc:
                state.status = str(exc)
        elif name == OUTPUT:
            state.each = not state.each

    @keys.add("up")
    def _up(event):
        state.move(-1)

    @keys.add("down")
    def _down(event):
        state.move(1)

    @keys.add("pageup")
    def _page_up(event):
        state.move(-PAGE_ROWS)

    @keys.add("pagedown")
    def _page_down(event):
        state.move(PAGE_ROWS)

    @keys.add("tab")
    def _tab(event):
        rows, controls, name = len(state.rows()), state.controls(), state.on_button()
        at = -1 if name is None else controls.index(name)
        state.cursor = rows + at + 1 if at + 1 < len(controls) else 0

    @keys.add("space")
    def _space(event):
        if state.on_button() is None:
            state.toggle(state.rows()[state.cursor])
        elif state.on_button() == OUTPUT:
            state.each = not state.each

    @keys.add("a")
    @keys.add("A")
    def _all(event):
        state.toggle_all()

    @keys.add("right")
    def _right(event):
        if state.on_button() is None:
            state.fold(state.rows()[state.cursor], True)
        elif state.on_button() == OUTPUT:
            state.each = not state.each
        else:
            state.move(1)

    @keys.add("left")
    def _left(event):
        if state.on_button() == OUTPUT:
            state.each = not state.each
            return
        if state.on_button() is not None:
            state.move(-1)
            return
        i = state.rows()[state.cursor]
        if i in state.open:
            state.fold(i, False)
        elif state.parent[i] is not None:
            state.cursor = state.rows().index(state.parent[i])

    @keys.add("enter")
    def _enter(event):
        if state.on_button() is None:                 # on a row Enter only folds: it never starts a run
            i = state.rows()[state.cursor]
            state.fold(i, i not in state.open)
        else:
            finish()

    @keys.add("escape", eager=True)
    @keys.add("c-c")
    def _cancel(event):
        event.app.exit(result=None)

    def buttons() -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        if OUTPUT in state.controls():
            def choose(event):
                if event.event_type != MouseEventType.MOUSE_UP:
                    return NotImplemented
                state.each = not state.each
            style = "class:button.cursor" if state.on_button() == OUTPUT else ""
            out.append((style, f"  {OUTPUT}: ", choose))
            out.append((style, f"({'•' if state.each else ' '}) {state.each_label}   ", choose))
            out.append((style, f"({' ' if state.each else '•'}) One deck", choose))
            out.append(("", "\n"))
        out.append(("", "  "))
        for name in BUTTONS:
            here = state.on_button() == name

            def click(event, name=name):
                if event.event_type != MouseEventType.MOUSE_UP:
                    return NotImplemented
                state.cursor = len(state.rows()) + state.controls().index(name)
                finish()
            out.append(("class:button.cursor" if here else "class:button", f"[ {name} ]", click))
            out.append(("", "   "))
        return out

    root = HSplit([
        Window(FormattedTextControl(lambda: [("class:help", HELP)]), height=1),
        Window(FormattedTextControl(lambda: _rows_text(state, app_box), focusable=True), wrap_lines=False),
        Window(FormattedTextControl(lambda: [("class:status", state.status or state.summary())]), height=1),
        Window(FormattedTextControl(buttons), dont_extend_height=True),
    ])
    app = Application(layout=Layout(root), key_bindings=keys, style=STYLE, full_screen=True, mouse_support=True, **session)
    app_box.append(app)
    return app


def available() -> bool:
    """Whether this terminal can draw the checklist (some, like Git Bash's, have no console screen buffer)."""
    try:
        create_output()
    except Exception:
        return False
    return True


def pick(items: List[dict], each: bool = False, each_label: str = "One deck per top section"):
    """Run the checklist: (picked, left, one deck per top section); SystemExit when cancelled."""
    result = build(Checklist(items, each, each_label)).run()
    if result is None:
        raise SystemExit("Cancelled: nothing was run.")
    return result
