# Notes

Standing preferences and settled decisions. `TODO.md` is what still needs doing; this is what has
already been decided and should stay decided.

## Claude sessions (`kuma_claude/`)

### This repo's `CLAUDE.md` does not apply to a session

`--add-dir PROJECT_ROOT` grants a session *file access* to the repo. It does **not** pull the repo's
memory into the session's context, and it must not be made to.

`~/gitHub/Kuma_Kuma/CLAUDE.md` is k8thekat's CLI interaction with this repo. A session reached through
the cog is a different thing with a different owner, and its instructions are hers to give in the
thread or in her own per-user `CLAUDE.md` at `~/.kuma_claude/<user_id>/CLAUDE.md`.

Confirmed against a live session — the only two memory files loaded are:

- `~/.claude/CLAUDE.md`, the machine owner's global file.
- `~/.kuma_claude/<user_id>/CLAUDE.md`, seeded by `prepare_user_root`; it is the session's cwd.

Do not "fix" the absence.

## Updating PIP packages

- moogle: `pip install git+https://github.com/k8thekat/moogles_intuition@development`

## Discord rendering

### `ansi` code blocks need the real ESC byte

Discord colours a ```` ```ansi ```` block only when `0x1B` is genuinely in the payload. The escape
written as *text* — a backslash, `u`, `001b` — arrives as prose and renders as visible garbage. Build
them with `utils.codeblocks.ansi()`, never by hand.

This is also why colour has to be bot-authored. A Claude session's reply cannot carry the byte
through its own pipeline, so anything coloured must be produced by code on this side.

Only eight foregrounds, eight backgrounds and three styles exist; unsupported SGR codes are dropped
silently. `KumaLogFormatter` is deliberately held to that set so the same bytes work in the console
and in a Discord block.

### A fence token sits flush against the backticks

```` ```ps ```` highlights, ```` ``` ps ```` does not. The token is any highlight.js language name or
alias, several of which are useful purely as colour palettes — see `CodeFormat`.

### `attachment://` only resolves within one request

It refers to a file being uploaded *in the same payload*. An edit that keeps its existing attachments
has nothing to resolve against and Discord rejects the whole thing with
`50035 ... The referenced attachment was not found`. Point at the attachment's CDN URL instead; see
`PanelMedia` in `utils/help.py`.

A Components V2 message carries attachments but never renders them, so an attachment no component
points at is uploaded for nothing.

### Sessions are not steered with a system prompt — decided against

Styling the model's markdown through `--append-system-prompt` was built, measured and removed.
`to_discord_markdown` does the job deterministically; the prompt only made it *more likely* the model
would cooperate, at the cost of ~190 tokens on every turn of every session.

Keep the measurements so nobody pays for them twice:

- The flag **does** work with the live flag set (`-p --input-format stream-json
  --permission-prompt-tool stdio`), in `default`, `plan` and `acceptEdits`. A first probe said
  otherwise; that was a softly worded instruction, not the flag being dropped. Anything sent this way
  has to read as an instruction.
- Compliance is partial. Three runs of a table-shaped question on Haiku 4.5: markdown tables fell
  3/3 → 1/3 and `###` headings rose 0/3 → 2/3, while `-#` subtext and `> ` blockquotes were not picked
  up at all. Tables actually reaching Discord were 0/3 **either way** — that is the rewriter, not the
  prompt, and it is the reason the prompt was not worth its per-turn cost.
- Run-to-run variance is wider than most of the differences worth chasing. An earlier single-sample
  comparison looked like a regression and was noise.
- `USER_MEMORY_TEMPLATE` was never the right home for it regardless: seeded once and then the user's,
  so anything written there is un-editable by us afterwards and absent for everyone who already has a
  file.

### `-#` is subtext only at the start of a line

A second `-#` further along the same line renders as literal text.

## Command dispatch

### `Bot.on_message` and cog `on_message` listeners fire independently

`process_commands` and every `@commands.Cog.listener(name="on_message")` both run for the same
message. Any handler that consumes messages must decide with `Kuma_Kuma.commands_enabled_for()`
whether the command half already claimed it, or the message is handled twice.

Inside a Claude session thread only an explicit mention counts as a command. Deferring to `ctx.valid`
alone is *not* enough: `when_mentioned_or` makes the guild's bare prefix apply to prose, so
`kuma help me debug this` parses as `help`.

### `default_permissions` does nothing on a prefix-only command

`@app_commands.default_permissions(...)` needs an app command to attach to. On a plain
`@commands.command()` there is none and the decorator is silently inert — make the command hybrid, or
gate it with a real check.

### Context menus are not owned by a cog

They are registered onto `bot.tree` in a cog's `__init__`, so `get_bot_mapping()` and
`cog.get_app_commands()` both miss them. Read them off the tree.

## Reload and restart

### `help_command` is an instance, bound once

Reloading `utils.help` puts new code in `sys.modules` and changes nothing about the object answering
`help`. `reload` calls `Kuma_Kuma.refresh_help_command()` to rebind it. Edits to `kuma_kuma.py`
itself still need a full restart, since `reload` never re-executes that file.

### A cog can end up newer than the live bot object

`reload` re-executes extensions but not `kuma_kuma.py`, so a cog referencing a bot attribute added
since the process started raises `AttributeError`. Read such attributes defensively where the command
is the way *out* of a stale process — `moderator.restart` does.

### `purge(after=...)` is exclusive, and stays that way

`clear` anchored on a message — replied-to or by ID — removes everything *after* it and leaves the
anchor standing. Both anchoring forms agree on this deliberately. Decided; do not make it inclusive.
