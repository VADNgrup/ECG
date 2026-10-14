"""The agents' system prompts as Markdown files (`<name>.md`), in one frame: <role> <input> <task> <rules> <output>
<example>. A file holding several prompts marks each with `<!-- prompt: <section> -->` and is loaded as
`<name>:<section>`. A prompt is plain text the model reads; its digest keys the agent's decision cache, so editing a
file asks the model again.

A field of study (`Config.DOMAINS`) may overlay a prompt with `domains/<domain>/<name>.md` (same section markers): its
`<domain>` block is inserted after the prompt's `<rules>` and its `<example>` replaces the prompt's. The input and output
never change, so every field's answers parse the same way. `general` has no overlay: the prompts as they are."""
import re
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent
SECTIONS = ("role", "input", "task", "rules", "output", "example")
_MARK = re.compile(r"^<!-- prompt: (\w+) -->$", re.M)
_BLOCK = lambda tag: re.compile(rf"<{tag}>.*?</{tag}>", re.S)


def _section(path: Path, section: str) -> str:
    """The `section` of a prompt file, or the whole file when no section is asked; "" when the file is missing."""
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if not section:
        return text.strip()
    pieces = _MARK.split(text)
    found = dict(zip(pieces[1::2], pieces[2::2]))
    return found.get(section, "").strip()


@lru_cache(maxsize=None)
def load(name: str, domain: str = "general") -> str:
    """The prompt `<name>.md`, or the `<section>` of `<file>.md` for `<file>:<section>`, without surrounding blank
    lines; with a field's overlay when `domain` has one."""
    file, _, section = name.partition(":")
    base = _section(_DIR / f"{file}.md", section)
    if not base:
        raise KeyError(f"no prompt '{section}' in {file}.md" if section else f"no prompt file {file}.md")
    overlay = _section(_DIR / "domains" / domain / f"{file}.md", section) if domain != "general" else ""
    if not overlay:
        return base
    block, example = _BLOCK("domain").search(overlay), _BLOCK("example").search(overlay)
    if block:
        rules = base.rfind("</rules>")
        at = rules + len("</rules>") if rules >= 0 else len(base)
        base = base[:at] + "\n\n" + block.group(0) + base[at:]
    if example and _BLOCK("example").search(base):
        base = _BLOCK("example").sub(lambda _: example.group(0), base, count=1)
    return base.strip()
