# Kuma_Kuma

See the parent directory `CLAUDE.md` at `~/gitHub/CLAUDE.md` for the global project index and repo overview.

Use `KumaEmoji` styling when sending replies and errors.

## Code style

Derived from the existing source. `ruff` config lives in `pyproject.toml` — run `ruff check <files>` on anything
touched and leave it clean. Pre-existing errors in files you did not touch are not yours to fix
unless asked.

### Typing

- **`Optional[X]`, never `X | None`.** `UP045` is disabled in `pyproject.toml` specifically to allow
  this. Likewise `Union[A, B]` rather than `A | B`.
- **Annotate locals**, not just signatures: `messages: list[discord.Message] = []`. This is pervasive
  (~2000 occurrences) and is the single most visible habit in the codebase.
- `ClassVar[...]` for mutable class attributes.
- `from __future__ import annotations` at the top of new modules.
- Runtime-only imports go under `if TYPE_CHECKING:`.

### Docstrings

numpydoc, with Sphinx roles on every type:

```python
def parse_log(self, entries: int = 15, *, colour: bool = False) -> str:
    """Read the tail of the current log file.

    Slices by *record*, not by line, so a traceback always arrives attached to the message that
    raised it.

    Parameters
    ----------
    entries: :class:`int`, optional
        How many of the most recent records to return, by default 15.
    colour: :class:`bool`, optional
        Re-apply :class:`KumaLogFormatter` colours, by default False.

    Returns
    -------
    :class:`str`
        The selected records, newline joined.

    Raises
    ------
    FileNotFoundError
        The current log file does not exist.

    """
```

- `:class:`X`` on every parameter and return type, `Optional[...]` included.
- `, optional` and `, by default X` on anything with a default.
- Blank line before the closing `"""`.
- `.. note::` and `.. warning::` for caveats worth interrupting the reader for.
- `D101` (public class) and `D107` (`__init__`) are disabled — a class docstring is still wanted when
  it has something non-obvious to say, but is not mandatory.

### Comments

Per `~/gitHub/CLAUDE.md`: comment whenever possible, high level, explaining *why* rather than what.
The established habit here is a comment above a block explaining the reasoning or the trap it avoids,
not a running narration.

- **Abbreviated names are fine for common short-lived locals** — `e` / `err` for exceptions,
  `res` for results, loop counters, and similar cases where meaning is obvious from context.
  Avoid them in parameters, class attributes, and longer-lived variables where the intent
  isn't immediately clear: `for attachment in ...`, not `for a in ...`.
- Line length is **140**.
- **Never ` -- ` as an aside separator.** Use a real em dash, `—`. Measured across the repo before
  this rule was written: 0 occurrences of ` -- ` in any comment or docstring k8thekat wrote, against
  217 in Claude-authored ones. It reads as a diff marker and it is not her hand.
- Keep them **short**. Hers run one or two lines and stop; a comment that needs a paragraph is
  usually a `.. note::` on the docstring instead. Do not narrate the history of a bug in a comment —
  what it protects against is worth a clause, not a story. `CHANGELOG.md` is where the history goes.
- Sentence case, ordinary prose, and `;` to join two clauses rather than splitting into two comments.

### Logging

`LOGGER.info("<%s.%s> | Message | Key: %s", __class__.__name__, "method_name", value)`

- Lazy `%s` formatting, never f-strings in a logging call (0 occurrences of the latter in the repo).
- The `<Class.method> | Thing | Key: value` shape is the house format.
- `__class__.__name__`, never `type(self).__name__`. It resolves inside a `@staticmethod` too, so a
  helper like `Preferences.migrate` still logs as `<Preferences.migrate>` without hardcoding a name.
  The one place `type(self).__name__` is correct is a *runtime* value — `KumaCog.__init__` keys its
  metrics by the subclass, which is the bug `__class__` caused there.

### Discord

- Set `allowed_mentions` on important responses or long-timed responses where an accidental ping would
  be disruptive; not every send needs it. See `~/.claude/skills/discord-py/` for the behavioural traps.
- Components V2 (`LayoutView`) cannot carry `content` or `embeds`, but *can* carry attachments.
- `KumaEmojiTable` for emoji in text and replies, `KumaResources` for image paths. Unicode emoji and
  default Discord emoji (🔍, 🔄, 🗑️, ➕, etc.) are fine for button styling and visual markers where
  no kuma or unicode table entry fits the intent. `UnicodeTable` for structural characters (middle
  dot, star, arrows).
- **Markdown tables do not render in Discord.** `| a | b |` with a `|---|` rule arrives as literal
  pipes and dashes, separator row and all. This applies to every string the bot sends *and* to every
  reply written into a thread. Discord markdown is otherwise excellent, so use it:
    - Two columns, few rows: a bullet list, `- **Key** — value`. Keeps bold, `code`, links and emoji.
    - Three or more columns, or anything needing alignment: a fenced code block, space padded.
      Nothing inside a fence renders, so no links, emoji or mentions in one.
    - Rows that each need a link or an emoji: a `###` heading per row with `-#` subtext under it.
    - `·` (`UnicodeTable.middle_dot`) is the separator that reads well inline; `|` reads as a
      broken table.

## Replying about code

Include the actual code in replies — the real diff or the real snippet, formatted as it lands in the
file, so the style can be checked at a glance rather than after the fact. Do not paraphrase a change
into prose when the code itself is the answer.

## Git

- **Do not add `Co-Authored-By` trailers to commit messages.** They pollute the generated changelogs.

## Housekeeping

`TODO.md` and `CHANGELOG.md` are maintained per `~/gitHub/CLAUDE.md`; consult it for the marker
characters and the changelog's theme-key format.
