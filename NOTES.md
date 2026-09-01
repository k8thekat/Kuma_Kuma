# Notes

Standing preferences and settled decisions. `TODO.md` is what still needs doing; this is what has
already been decided and should stay decided.

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

## Sonarr / Radarr (`extensions/sonarr_radarr.py`)

### Owner gate is per-command, not per-cog

`_owner_only()` is an `@app_commands.check` on each command. The panel's own `interaction_check`
still restricts button presses to the user who opened the panel — that is a separate concern from the
owner gate on the command that creates it.

### Both cogs load from one `setup()`

`extensions/__init__.py` auto-discovers by `iter_modules`, so renaming the file was enough. Both
`SonarrCog` and `RadarrCog` are added in `setup()`. Either cog's credentials can be absent — a
missing `[SONARR]` or `[RADARR]` section in `local.ini` disables that cog's commands without
stopping the other.

## Claude cog (`kuma_claude/`) — settled decisions

### `.claude/settings.json` does not apply to `claude -p`

Sessions are deliberately independent of the repo's interactive settings file. A `Write` covered by
a file-scope rule in that file was denied under `manual` with no `--settings` flag, and allowed
only when the same file was passed explicitly as `--settings`. Editing the repo file cannot break
sessions and cannot fix them; `build_command()` is the only source of truth.

### `--settings` is last-wins, not additive

Passing the project file *and* an inline grant drops one of them, so the two cannot be layered.

### Path-scoped file rules don't work inline

`Write(extensions/.claude_asks/**)` was denied inline as a relative path, as an absolute path, and
with a `//` prefix — while the identical rule in `.claude/settings.json` passed via `--settings <path>`.
Bare tool names (`Read`, `Write`) and `Bash(cmd:*)` prefix rules do work inline. Root cause unknown —
don't build on path-scoped inline rules until someone pins this down.

### Bash rule recommended set

`Bash(ruff check:*)`, `Bash(ruff format:*)`, the two `.venv/bin/ruff` equivalents, `Bash(ls:*)`,
`Bash(cat:*)`, `Bash(grep:*)`. **Drop** `python`, `sed` and `find`: `Bash(python:*)` matches
`python -c "<anything>"` and subsumes every other rule; `sed -i` and `find -exec` are similarly
unbounded. Rules are prefix matches over the literal command, so `Bash(ruff check:*)` does **not**
match `.venv/bin/ruff check` — list both forms.

### `--allowed-tools` only grants, never confines

Measured: a run given `--allowed-tools Read,Edit,Write` ran Bash regardless. Confinement belongs to
the mode. Presets use `allow` only, never `deny` — a deny rule removes the tool outright (Claude
says it has no such tool, emits no `permission_denials`), so a retry offer would go blind. Read-only
shell is sandbox-auto-approved regardless of rules.

### `--add-dir` / `.addrepo` deliberately not implemented

One containment boundary per session.

### `~/.claude/CLAUDE.md` still applies to every session

Discovery walks up past the per-user root. The only ways to suppress it are `--safe-mode` and
`--bare`, which cost skills and break subscription auth respectively. Left alone deliberately.

### Mode and model vs effort dispatch

Mode and model stay on **control requests**, effort on a **slash command**. There is no `/mode`
(`/permissions` "isn't available in this environment"), and while `/model` works, a control request
is out of band and can be sent mid-turn, which the "Always" button requires. Do not unify the three.

### Cancel does not roll back

Cancelling a turn does not roll back what the run already wrote to the workspace, and the closing
line says so. The subprocess kill lives in `run_claude`'s `finally` and must stay there — a cancel
arrives as a `CancelledError` with no `return` to hang it off, and a CLI left running holds the
directory and keeps spending the shared account's limit.

### Rate-limit handling is global

One account backs every session, so `_limit_reset` is global rather than per user.

### Thread ownership

The bot creates session posts, so `Thread.owner_id` is the bot — every ownership check based on it
would pass for anyone. The owner is written into the state line instead.

### Transcript tail cuts

Cuts happen between `TranscriptBlock`s, never inside one, so a call is never split from its result.
Only blocks marked `final` are sealed — a still-running call is carried onto the new tail rather than
frozen mid-flight onto a message that will never be edited again.

### `.raw` vs `resolve_prompt_links`

`.raw` writes content with no header for byte-exact debugging. `resolve_prompt_links` is for context
and adds provenance. Attachments on a linked message are not saved, only its text.

## Claude cog — archive findings (`.archive/_claude.py`)

Two files called `_claude.py` exist. `.archive/_claude.py` (6,085 lines) is a parallel branch, not
an ancestor of the live cog. `extensions/_claude.py` (7,008) is the pre-split live cog, kept for
blame. Neither loads. `.archive/_claude_old.py` (1,909 lines) is the original embed-based cog,
superseded by the forum-post design. Old `claude_sessions`/`claude_history` tables are left on disk —
drop by hand once the rewrite has proven itself.

### Access tier findings

If access tiers are ported from the archive, keep these — each cost a measured run:

- A cwd-boundary refusal **does** produce a `permission_denials` entry, so a naive retry offer treats
  "read a file above your workspace" as a missing-tool problem and offers a grant that cannot help.
  An allow rule does not widen the cwd.
- `AccessTier` must be re-resolved every turn and fail closed — DM, departed member or unfetchable
  user all come back Standard. Must **not** live in the state line: a panel is editable history.
- Store access in the database, not a JSON file at the repo root — that path is an elevated session's
  own cwd, and no deny rule covered it.
- Deny rules are scoped to one tool: `Edit(*.sqlite*)`/`Write(*.sqlite*)` does not cover
  `Bash(sqlite3:*)`/`Bash(litecli:*)`.
- `Bypass` deliberately does not imply `Elevated` — the power has to be reached for.
- `transcript_slug` must be built from the working directory, not the project root; flatten `.` as
  well as `/` and `_`.

## Markdown post-processing (`to_discord_markdown`)

### `__text__` not rewritten

Discord reads `__text__` as underline rather than bold, but which the model meant is a guess. The
markdown pass only does mechanical rewrites.

### Zero-width-space defanging of fences inside code blocks

Tried and reverted — broke the render in the Discord client. Triple backticks inside a code block
remain unfixable; **attach the sample as a file** instead of inlining it.

### Wide tables

A very wide table is a horizontal scroll inside its fence rather than a wrap. Not truncated on
purpose since cutting cells loses data. See `~/.claude/skills/discord-py/SKILL.md` for the full
markdown divergence table.

## KumaAnimation (`utils/animation.py`)

### `min_interval` clamped to 1.0s

Sub-second intervals are silently raised. A test that sleeps 0.5s and expects three frames will fail.

### `status_last` and overflow trim

A bottom-anchored status survives the cut by trimming the *oldest* body lines instead of the live
line. Snaps forward to a line break so the trim never opens on half a line; header held out of trim.

### Inflection is a text concern, not an animation concern

The caller inflects with `KumaCog.to_progressive()` before assigning the label.

## KumaView (`utils/ui.py`)

With the `indx` clamp corrected, `next_callback`'s `if indx <= len - 1` is always true, so its
trailing `reset_view()` fallthrough is unreachable. Clicking Next past the last page re-renders the
last page — more predictable, and already unreachable through the UI since Next is disabled there.
Left in place as defensive code.

## Hints (`extensions/hints.py`)

### No global `users` table

A snowflake is already a stable 64-bit natural key, so a surrogate would store 8 bytes to avoid
storing 8, add an ordering rule to every write and a JOIN to every read, and re-couple the cog to
core. `userid INTEGER NOT NULL` repeated per table is the join key. `asqlite` *does* set
`pragma foreign_keys=ON` per connection (`asqlite/__init__.py:501`).

### Panel is deliberately not persistent

Custom IDs encode the hint key so they cannot be pre-registered. `/hints` is cheap to re-run.

## Preferences (`extensions/preferences.py`)

### `/settings` and `/preferences` cannot be merged

Discord applies `default_permissions` per top-level command and it cannot vary by subcommand, so
admin+guild-only and open+DM-capable are unsatisfiable under one parent.

## AutoMod (`extensions/automod.py`)

### `_is_new_member` fails open

A member with no `joined_at` comes back False, so unknown tenure is treated as established. Safer
default but undocumented.

## Housing (`extensions/private/housing.py`)

### `Zillow_*` attribute names stay as Zillow spells them

`__init__` maps the payload with `setattr(self, key, value)`, so renaming them for `N815` would
break the mapping they exist for. Held with a file-level `# ruff: noqa: N801, N815`.
