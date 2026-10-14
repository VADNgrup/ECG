"""Where a PDF's headings come from, chosen before its KG is built: its embedded outline (TOC, pymupdf get_toc) or the page
reading (OCR: the printed contents page and the heading marks, checked by the LLM). Who reads the pages is .env's
OCR_MODE. The outline's size is shown, the likelier choice starts selected, and nothing runs until Enter is pressed."""
from __future__ import annotations

from typing import Callable, List, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.mouse_events import MouseEventType

from src.workflow import scope_picker

TOC, OCR = "toc", "ocr"       # the two `--headings` values offered
NAMES = {TOC: "TOC", OCR: "OCR"}
HELP = "↑↓ choose   Enter run   Esc cancel"


def rows(facts: dict) -> List[dict]:
    """The two choices; `facts` as `extract.reading_facts` gives them."""
    return [{"value": TOC, "detail": f"the headings are the PDF's outline ({facts['entries']} entries, pymupdf get_toc)"},
            {"value": OCR, "detail": f"the headings are found while the pages are read ({facts['reader']}): the printed "
                                     f"contents page and the heading marks, checked by the LLM"}]


def build(choices: List[dict], start: int, title: str, **session) -> Application:
    """The application; it returns the chosen value, or None when cancelled."""
    keys = KeyBindings()
    at = [start]
    box: list = []

    @keys.add("up")
    def _up(event):
        at[0] = max(0, at[0] - 1)

    @keys.add("down")
    def _down(event):
        at[0] = min(len(choices) - 1, at[0] + 1)

    @keys.add("enter")
    def _enter(event):
        event.app.exit(result=choices[at[0]]["value"])

    @keys.add("escape", eager=True)
    @keys.add("c-c")
    def _cancel(event):
        event.app.exit(result=None)

    def clicked(act: Callable[[], None]):
        def handle(event):
            if event.event_type != MouseEventType.MOUSE_UP:
                return NotImplemented
            act()
        return handle

    def body() -> StyleAndTextTuples:
        out: StyleAndTextTuples = []
        for n, row in enumerate(choices):
            on_click = clicked(lambda n=n: at.__setitem__(0, n))
            style = "class:cursor" if n == at[0] else ""
            out.append((style, f" ({'•' if n == at[0] else ' '}) {NAMES[row['value']]}  {row['detail']}\n", on_click))
        return out

    def buttons() -> StyleAndTextTuples:
        run = clicked(lambda: box[0].exit(result=choices[at[0]]["value"]))
        stop = clicked(lambda: box[0].exit(result=None))
        return [("", "  "), ("class:button", f"[ {scope_picker.BUTTONS[0]} ]", run), ("", "   "),
                ("class:button", f"[ {scope_picker.BUTTONS[1]} ]", stop)]

    root = HSplit([Window(FormattedTextControl(lambda: [("class:help", HELP)]), height=1),
                   Window(FormattedTextControl(lambda: [("class:status", title)]), height=1),
                   Window(FormattedTextControl(body), dont_extend_height=True),
                   Window(FormattedTextControl(buttons), dont_extend_height=True)])
    app = Application(layout=Layout(root), key_bindings=keys, style=scope_picker.STYLE, full_screen=True,
                      mouse_support=True, **session)
    box.append(app)
    return app


def typed(choices: List[dict], title: str, read: Callable[[str], str], show: Callable[[str], None]) -> str:
    """The value of the name typed at a prompt, asked again until it is one of them."""
    show(f"\n{title}\n" + "\n".join(f"  {NAMES[row['value']]}: {row['detail']}" for row in choices))
    named = {NAMES[row["value"]].casefold(): row["value"] for row in choices}
    while True:
        answer = read(f"Take the headings from ({' / '.join(NAMES.values())}): ").strip().casefold()
        if answer in named:
            return named[answer]
        show(f"  '{answer}' is not one of them")


def ask(name: str, facts: dict, read: Optional[Callable[[str], str]] = None, show: Callable[[str], None] = print) -> str:
    """The `--headings` value chosen for this PDF; SystemExit when cancelled."""
    choices, title = rows(facts), f"Where do the headings of {name} come from?"
    if read is not None or not scope_picker.available():
        return typed(choices, title, read or input, show)
    chosen = build(choices, 0 if facts["reliable"] else 1, title).run()
    if chosen is None:
        raise SystemExit("Cancelled: nothing was run.")
    return chosen
