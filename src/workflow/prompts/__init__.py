"""The agents' system prompts as Markdown files (`<name>.md`), in one frame: <role> <input> <task> <rules> <output>
<example>. A file holding several prompts marks each with `<!-- prompt: <section> -->` and is loaded as
`<name>:<section>`. A prompt is plain text the model reads; its digest keys the agent's decision cache, so editing a
file asks the model again."""
import re
from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent
SECTIONS = ("role", "input", "task", "rules", "output", "example")
_MARK = re.compile(r"^<!-- prompt: (\w+) -->$", re.M)


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """The prompt `<name>.md`, or the `<section>` of `<file>.md` for `<file>:<section>`, without surrounding blank lines."""
    file, _, section = name.partition(":")
    text = (_DIR / f"{file}.md").read_text(encoding="utf-8")
    if not section:
        return text.strip()
    pieces = _MARK.split(text)
    found = dict(zip(pieces[1::2], pieces[2::2]))
    if section not in found:
        raise KeyError(f"no prompt '{section}' in {file}.md")
    return found[section].strip()
