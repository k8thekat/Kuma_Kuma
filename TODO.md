# TODO

Markers: ⭐ **DONE** · 🗨 suggestion · ⚠️ issue / needs attention · ⛔ deprecated, removed or decided against.

## General
- Work on readme for cogs/etc.
- Add Helper replies to `support-forums` or any forum.
- ⭐ **DONE** Improve help command (Possibly try out Components V2)
    - `KumaHelpCommand` in `utils/help.py`. Lists prefix, hybrid, application and context menu
      commands, with the permission each needs.
    - ⚠️ Unverified against Discord: the select and Overview button, the `/about` banner and
      thumbnail, and whether the permission notes read well on a crowded cog.
- Online Member Count for Discord Guild via locked channel and edit channel name to current count. Use emojis for channel names.
- Double check sqlite connection pool and or other sqlite connections.
- Automate handling Sentry IO issues and write out a proposed POA (Plan of Action/Code) for a fix?
    - Create GH issues? Show "traffic/usage".
    - ⚠️ Not started — needs the Sentry API token/scope decided before anything can be built.
- 🗨 Naming: the Claude cog's locals were de-abbreviated per `CLAUDE.md` (`res`→`result`, `idx`→`marker_index`,
  `ts`→`timestamp`, `buf`→`buffer`, `data`→`result_event`, `i`→`index`, `count/size`→`file_count/total_size`).
  `conn` and `row` were kept — they are the established pattern in `ffxiv.py`/`automod.py`. Worth deciding
  whether the no-abbreviations rule should apply repo-wide before churning the other extensions.
- 🗨 Docstring mood is mixed repo-wide (`utils/cog.py` "Get the…", `utils/embeds.py` "Set the…",
  `kuma_claude/` "Returns…"). Each file is internally consistent; worth picking one convention before
  the next big pass.

- ⚠️ `@app_commands.default_permissions(...)` is dead on a **prefix-only** command — there is no app
  command for it to attach to. `moderator.clear` and `moderator.who_is` both carry one and are gated
  only by the inline check in the body (`clear`) or not at all (`who_is`). `trusted` was one of these
  and is now a hybrid group, so its decorator is live; the other two still need a decision — make
  them hybrid, or gate them with a real check and drop the decorator.
- 🗨 `extensions/private/` is not held to the repo's `ruff` standard (73 errors in `housing.py` alone).
  Worth deciding whether it should be, or be excluded explicitly.

## Modules

**Kuma.bash / kuma_kuma.py**:
- ⭐ **DONE** `-live` / `-dev` restart in place; bare invocation still only starts if nothing is running.
    - ⚠️ Deliberate: a bare call must **not** restart, or a periodic crontab entry would bounce the
      bot every tick. Only an explicit mode flag restarts.
- ⭐ **DONE** Mode recorded beside the pid, so a crash-restart resumes the mode it was last run in.
- ⭐ **DONE** The `@reboot` crontab entry pointed at `/home/kat/github/Kuma_Kuma/` (lowercase
  `github`) on a case-sensitive filesystem, so it had never run. Corrected by hand.

**Claude** (`kuma_claude/` — the loaded cog, an installed package rather than a file in `extensions/`;
its own `TODO.md` and `NOTES.md` carry what is left of the split):
- ⭐ **DONE** Opened end-to-end against Discord. `/claude` resolves and the session panel's persistent
  buttons answer, both after the move to the package — their custom IDs were unchanged by it. The
  items below are the parts of a session that a single opened session does not exercise.
- ⭐ **DONE** Turn queueing verified against a real thread. Two messages in a row run in order rather
  than the second being refused, and `.stop` drops both — `live_session` does not reorder the pair.
- ⚠️ The cleanup work is unverified against Discord. Worth checking by hand: a closed post reaching 30
  days becomes `[EXPIRED]`, a hand-made forum post is removed and DM'd back, and an orphan workspace
  survives its grace window before going. The pure logic is covered by a scratch harness (21 checks).
- ⭐ **DONE** The inline transcript verified against a live stream. The `user` → `tool_result` shape and
  the `tool_use_id` ↔ `id` pairing hold, so `on_tool_result` fires and a call is closed by its own
  result rather than by whatever ran next.
- ⚠️ `missing_permissions` is only ever called with `guild=`, so the eight `FORUM_PERMISSIONS` are
  declared and never checked. The previous cog called it a second time with `channel=forum` once the
  forum was resolved.
- ⚠️ The inline transcript costs message *creates*: a text block is now the block plus a reopened tail,
  against one before. Discord's per-channel create budget is shared with the animation's edits, so a turn
  emitting many short blocks in a burst will feel it. `interject(reopen=False)` trades the spinner's
  continuity back for one message fewer if 429s show up in the log.
- ⭐ **DONE** Transcript snapshot *and* restore are wired end to end. `snapshot_transcript` gzips the
  CLI's log into the workspace on close and on expiry (park first, then snapshot, so the process has
  stopped writing); `restore_transcript` writes it back to the exact path `live_session` tests when it
  chooses `--resume` over `--session-id`, so putting the file back *is* the restore and nothing else
  needs to know one happened. It never overwrites a live copy — the snapshot is taken at park time, so
  anything already there is at least as new.
    - **Restore Session** runs the restore *before* it unlocks anything, and an EXPIRED session with no
      snapshot left is refused outright rather than handed back as a post that looks like the old
      conversation and answers as though it never happened.
    - 🗨 `.restore` the dot command is only a signpost — it points at the panel button, since a locked
      thread cannot receive the message anyway.
- ⚠️ **Revisit the whole prompt-view structure** (`ClaudePrompt`, `ToolApproval`, `PlanApproval`,
  `QuestionPrompt`, `SettledPrompt`). Patched, not designed — each view assembles its own container by
  hand and has to remember the 4000-character budget for itself. `SettledPrompt` re-derives what the live
  prompt already knew, and `settled()` returning a *different* view class is why an over-budget plan could
  post fine and then fail on the button press. Wanted: one builder that owns the container, the budget and
  the component count, with each prompt declaring only its heading, body and buttons; and a settled form
  that is the same view with its actions swapped out rather than a rebuild.
    - 🗨 `SessionPanel` sits outside this and has the same budget problem — `warn_if_oversized` only
      *logs* when the panel is over, where a prompt now fits itself. Fold it into the same builder.
    - 🗨 Components V2 enforces its limits at **construction**, not on send, so a builder is also the
      place to catch an oversized view before it can raise inside an event handler.
- ⛔ **`.claude/settings.json` does not apply to `claude -p`, and sessions must never depend on it.**
  Confirmed and accepted: it is the local file for interactive editing in the repo, and the session
  infrastructure is deliberately independent of it. Measured — a `Write` covered by that file's own
  `Write(extensions/.claude_asks/**)` rule was denied under `manual` with no `--settings` flag, and
  allowed only when the same file was passed explicitly as `--settings`.
    - A session gets exactly what `build_command()` hands it. Editing that file cannot break sessions and
      cannot fix them. The `.. warning::` on `build_command()` is the durable note; keep the two independent.
- ⚠️ **`--settings` composition limits** — what a session can be handed inline is narrower than the
  settings-file syntax, so verify against a real run before adding a rule *shape* not already in use.
    - ⛔ Repeating `--settings` is **last-wins**, not additive. Passing the project file *and* an inline
      grant drops one of them, so the two cannot be layered.
    - ⭐ **DONE** Works inline: bare tool names (`Read`, `Write`) and `Bash(cmd:*)` prefix rules.
    - ⛔ Does **not** work inline: path-scoped file rules. `Write(extensions/.claude_asks/**)` was denied
      inline as a relative path, as an absolute path, and with a `//` prefix — while the identical rule
      in `.claude/settings.json` passed via `--settings <path>` was allowed.
    - 🗨 The reason is **not** established. Two hypotheses were tested and both failed: it is not simply
      relative-vs-absolute (absolute was denied too), and it is not resolution against the settings
      file's own directory. The only observed difference is that the working case was the canonical
      `.claude/settings.json` path. Do not build on path-scoped inline rules until someone pins this down.
- ⚠️ **Needs your action** — `.claude/settings.json` Bash rules are written `Bash(python*)`, missing the
  `:*` that makes them prefix rules, so they have never matched. Fix via `/permissions` or the
  `update-config` skill (direct writes to that file are gated). Recommended set: `Bash(ruff check:*)`,
  `Bash(ruff format:*)`, the two `.venv/bin/ruff` equivalents, `Bash(ls:*)`, `Bash(cat:*)`,
  `Bash(grep:*)` — and **drop** `python`, `sed` and `find`: `Bash(python:*)` matches `python -c
  "<anything>"` and subsumes every other rule, while `sed -i` and `find -exec` are similarly unbounded.
- ⚠️ `--allowed-tools` **cannot confine** — measured: a run given `--allowed-tools Read,Edit,Write` ran
  Bash regardless. It grants only. Any preset built on it withholds nothing, which is why permission
  presets go through `--settings`.
    - ⚠️ Presets use `allow` only, never `deny`. A `deny` rule (and `--disallowed-tools`) removes the
      tool outright: Claude says it has no such tool and the run emits **no** `permission_denials`, so a
      retry offer would go blind. Confinement belongs to the mode.
    - 🗨 Read-only shell is sandbox-auto-approved regardless of rules (`git status`/`log`/`diff` all ran
      ungranted under `manual`), so presets stay thin. Rules are prefix matches over the literal command,
      so `Bash(ruff check:*)` does **not** match `.venv/bin/ruff check` — list both forms.
- ⛔ `--add-dir` / `.addrepo` deliberately **not** implemented; one containment boundary per session.
- 🗨 `/claude announcement` is shown on every `/claude ask` until it is replaced or cleared. If that
  turns out to be too repetitive it could go through `HintsCog` instead, keyed per announcement.
- 🗨 `thinking` events are still dropped by `on_cli_event`. `system/thinking_tokens` could drive a
  progress line.
- 🗨 `auto`, `dontAsk` and `manual` are offered as modes but only `default` and `plan` were measured as
  producing a `can_use_tool` prompt. `manual` is known to *deny* rather than ask. Confirm what the other
  two do before anyone relies on them.
- 🗨 `~/.claude/CLAUDE.md` still applies to every session, since discovery walks up past the per-user
  root. The only ways to suppress it are `--safe-mode` and `--bare`, which cost skills and break
  subscription auth respectively — left alone deliberately, but it is the one gap in the isolation.
- 🗨 `AskUserQuestion` can carry several questions; only the first is shown. A turn stalls on the answer
  and stacking four views on one blocked turn reads as four separate problems, so the model is left to
  ask the next on its own turn. Revisit if that proves annoying in practice.
- 🗨 `multiSelect` on `AskUserQuestion` is ignored — every option is a single-choice button. Needs a
  select, or toggle buttons plus a confirm, to honour it.
- 🗨 `IDLE_REAP_MINUTES` is a guess at 30. A resume replays the transcript, so reaping too eagerly costs
  a re-read of the conversation on the next message; worth tuning once there is real usage.
- 🗨 `USAGE_BLOCK_PERCENT` is 100 and assumes `utilization` is capped there. If a plan can report over
  100 the check still works, but if one reports a spent window as something other than 100 this would not
  catch it. Only seen 42-45% on this account so far; re-check against a genuinely exhausted one.
- 🗨 Only `five_hour` and `seven_day` are read; `get_usage` also returns `seven_day_opus`,
  `seven_day_sonnet` and an `extra_usage` credit pool, all `null` here. A model-specific limit would
  currently go unnoticed.
- 🗨 Context usage is read on demand (`.context`) and once past 85% of the auto-compact threshold. It
  could also ride on the session panel, but `SessionPanel.__init__` is sync and the read is a round trip,
  so it would need the usage passing in from the caller that already has it.
- 🗨 `compact_boundary` is reported but not acted on. `preservedSegment` / `preservedMessages` in its
  `compactMetadata` would let the notice say what survived rather than just that it happened.
- 🗨 `/compact` accepts free-text instructions on what to keep; `.compact` forwards an argument but
  nothing documents that to the user.
- 🗨 The `initialize` handshake hands back all 45 of the CLI's slash commands, and unrecognised dot
  commands already fall through to them. `.help` only lists the bot's own — it could list the CLI's too,
  or offer them as autocomplete, now that we know what they are.
- 🗨 `/effort` is sent blind: the reply ("Effort level set to auto (this session only)") is dropped,
  since nothing is listening when it goes out. Capturing it would let a rejected level be reported back
  instead of the panel claiming a level the CLI refused. Low risk while the select and `.effort` both
  validate against `EFFORTS` first — and `/model` with no argument reports *"Current model: Sonnet 5
  (effort: high)"*, so there is a ready-made way to read both back if it ever matters.
- 🗨 The transcript tail cuts between `TranscriptBlock`s, never inside one, so a call is never split from
  its result; and only blocks marked `final` are sealed, so a still-running call is carried onto the new
  tail rather than frozen mid-flight onto a message that will never be edited again.
- ⭐ **DONE (the post-process half)** `to_discord_markdown()` rewrites the model's markdown into what
  Discord renders, called from `on_text` before the chunker so a table is still whole when it is
  measured and fenced. `result.blocks` keeps the raw text, since that is the answer as written.
    - **Tables** become an aligned fenced block. Monospace is the only place column padding survives,
      and a table is detected by its `|---|` rule rather than by its pipes, so "use `a | b` for
      either" is not mistaken for a one row table.
    - `---` / `***` / `___` rules become `REPLY_SEPARATOR`, and `- [ ]` / `- [x]` become ☐ / ☑ with
      their indent kept.
    - Emphasis markers are stripped from table cells — nothing renders inside a fence, so `**Yes**`
      would arrive with the asterisks showing. Links are left whole; the text still reads and dropping
      the URL would lose what the cell was for.
    - ⛔ `__text__` is deliberately **not** rewritten to `**text**`. Discord reads it as underline
      rather than bold, but which one the model meant is a guess, and this pass only does the
      mechanical ones.
    - `walk_markup()` is now the only place a code fence is recognised; `balance_markup` reads from it
      too, so the two cannot drift apart. Verified nothing inside a fence is touched, that a long
      table still chunks with every fence balanced and every chunk under 2000, and that
      `balance_markup`'s own contract is unchanged.
- 🗨 A very wide table is a horizontal scroll inside its fence rather than a wrap. Not truncated on
  purpose, since cutting cells loses data; revisit if a real answer proves unreadable on mobile.
  - See `~/.claude/skills/discord-py/SKILL.md` for the full markdown divergence table.
- 🗨 `.raw` is kept for byte-exact debugging: it writes the content with **no** header, which is what you
  want when hunting a formatting bug. `resolve_prompt_links` is for context, so it adds provenance.
  Attachments on a linked message are not saved, only its text.
- ⛔ Zero-width-space defanging of fences *inside* a code block was tried and reverted — it broke the
  render in the Discord client. Triple backticks inside a code block remain unfixable; **attach the
  sample as a file** instead of inlining it.
- ⚠️ A bare message ID has no lookup-by-ID endpoint, so `_search_message` probes channels one REST call
  at a time (session thread → sibling threads → guild text channels), capped at `MESSAGE_SEARCH_LIMIT`
  (40). Pasting the full link is always cheaper.
- ⚠️ One account backs every session, so rate-limit handling (`_limit_reset`) is deliberately global,
  rather than per user.
- ⚠️ The **bot** creates session posts, so `Thread.owner_id` is the *bot* — every ownership check based
  on it would pass for anyone. The owner is written into the state line instead.
- ⚠️ `STATE_LINE_PATTERN` still matches the retired `project` key *optionally* so posts written before it
  was dropped keep parsing. Delete that group once no live session post predates it, or those sessions
  become unrecoverable.
- ⚠️ Settled: mode and model stay on **control requests**, effort on a **slash command**. Measured —
  there is no `/mode` (`/permissions` "isn't available in this environment"), and while `/model` does
  work, a control request is out of band and can be sent mid-turn, which the "Always" approval button
  requires. Do not unify the three; the note is on `LiveSession.set_mode`.
- ⚠️ Cancelling a turn does **not** roll back what the run already wrote to the workspace, and the
  closing line says so. Anything that starts implying otherwise is wrong.
    - ⚠️ The subprocess kill lives in `run_claude`'s `finally` and must stay there. A cancel arrives as a
      `CancelledError` raised at whatever line the run is on; there is no `return` to hang it off, and a
      CLI left running holds the project directory and keeps spending the shared account's limit.
    - 🗨 This is a stop, not a steer. Steering (one process held open across turns) is a separate
      decision: it would move a session from state plus `--resume` to a live process the cog owns,
      supervises and reaps. Worth it for mid-run input generally — answering a denial inline rather than
      re-running the turn — not for an interrupt key alone.
- ⚠️ **Regression to keep in mind:** the sibling libraries (`ampapi`, `async_universalis`,
  `async_garlandtools`) are installed as **copies** into `.venv/…/site-packages/`, not editable installs.
  That is inside `PROJECT_ROOT`, so a session can still edit them — the fix looks applied, passes a test
  run, then silently vanishes on the next `pip install`.
- 🗨 `panel_files` could render the workspace as a `discord.ui.MediaGallery` when it holds images, instead
  of the current filename list. Costs the reply its `content` line (a Components V2 message cannot carry
  one), so it needs a `LayoutView` of its own, image-extension filtering and the 10-item gallery cap.
- 🗨 Pre-existing research folders in `extensions/.claude_asks/` (sonarr_radarr, components_v2, …) sit
  beside the per-user dirs. They are never scanned or attached, but could move under a `_shared/` folder.

**Claude — shelved variants** (`.archive/_claude.py`, `.archive/_claude_old.py`):
- ⚠️ **Mind the name.** There are two different files called `_claude.py` and only one of them is this
  section. `.archive/_claude.py` (6,085 lines) is the parallel branch described below;
  `extensions/_claude.py` (7,008) is the *pre-split live cog*, kept in place as the only thing a first
  commit in `kuma_claude` could be blamed against. Neither loads — `.archive/` is not walked and the
  other is skipped on its underscore — so the mix-up is silent.
- ⚠️ **Decide what comes forward.** `.archive/_claude.py` carries work that is **not** in the live cog
  and would have to be ported rather than merged — it is a parallel branch, not an ancestor:
    - Access tiers (`AccessTier` / `Access` / `AccessConfig`, `access_for()`, the `claude_access` table,
      `/claude access list|grant|revoke`, `.access`) and the `[CLAUDE] bypass_user_ids` allowlist.
    - `ToolDenial.outside_cwd` / `names_path_outside()` — telling a cwd-boundary refusal apart from a
      missing-tool one, and the `DenialRetry` **Allow & Retry** button.
    - `tools_explicit`, so a hand-set `.tools` list outranks a mode preset.
    - `attach_transcripts`, `session_is_known()`, `adopt_transcript()`. **Not** `snapshot`/`restore` —
      those came across and are live in `mixins/panel_callbacks.py`; see the DONE entry above.
    - `/claude spoof`, the dev-only UI renderer.
- ⚠️ If access tiers are ported, keep these findings — each cost a measured run to establish:
    - A cwd-boundary refusal **does** produce a `permission_denials` entry, so a naive retry offer treats
      "read a file above your workspace" as a missing-tool problem and offers a grant that cannot help.
      An allow rule does not widen the cwd (tested with an explicit `Bash(cat:*)` still being refused).
    - `AccessTier` must be re-resolved every turn and **fail closed** — a DM, a departed member or an
      unfetchable user all come back Standard. It must **not** live in the state line: a panel is
      editable history, so a tier parsed back out of one is a tier a user could try to forge.
    - Store access in the database, **not** a JSON file at the repo root — that path is an elevated
      session's own cwd, and no deny rule covered it, so an elevated session could grant itself elevation.
    - Deny rules are scoped to one tool, so `Edit(*.sqlite*)` / `Write(*.sqlite*)` does not cover a shell
      client reaching the same file. `Bash(sqlite3:*)` and `Bash(litecli:*)` are needed too.
    - `Bypass` deliberately does **not** imply `Elevated`: the power has to be reached for, not carried.
    - `transcript_slug` must be built from the **working directory**, not the project root (they differ
      for a standard session), and `TRANSCRIPT_SLUG_TABLE` must flatten `.` as well as `/` and `_`.
- 🗨 `.archive/_claude_old.py` (1,909 lines) is the original embed-based cog (`ClaudeEmbed`,
  `claude_history`, `/claude history`, `/claude ask project:`). Superseded by the forum-post design;
  kept only as a reference.
- ⛔ The old `claude_sessions` / `claude_history` tables are no longer read or written. They are left on
  disk untouched — drop them by hand once the rewrite has proven itself.

**KumaAnimation** (`utils/animation.py`):
- ⚠️ `min_interval` is clamped to a 1.0s floor. Sub-second intervals are silently raised, so a test that
  sleeps 0.5s and expects three frames will fail.
- 🗨 `status_last` flips the overflow trim with it: a bottom-anchored status has to survive the cut, so
  the *oldest* body lines go instead of the live line. Snaps forward to a line break so the trim never
  opens on half a line, and the header is held out of the trim entirely.
- 🗨 `ffxiv.py` still runs its own `processing_replies` loop; migrating it to `self.animate(...)` would
  drop the shared `reply_messages` list, the `reply_flag` toggle and the `_reply_lock`.
- ⛔ Inflection is deliberately *not* inside the animation — it is a text concern, so the caller inflects
  with `KumaCog.to_progressive()` before assigning the label.

**KumaView** (`utils/ui.py`):
- 🗨 With the `indx` clamp corrected, `next_callback`'s `if indx <= len - 1` is always true, so its
  trailing `reset_view()` fallthrough is unreachable. Clicking Next past the last page re-renders the
  last page instead of resetting the view — more predictable, and already unreachable through the UI
  since Next is disabled there. Left in place as defensive code.

**BaseView / paging** (`extensions/ffxiv.py`):
- ⚠️ `CurrencyView.reset_view` (`ffxiv.py:2361`) is a paging view whose override neither calls `super()`
  nor resets `indx` — it only re-enables `Next`, so its Reset button leaves the view on whatever page it
  was on. The override looks deliberately trimmed (the `super()` call is commented out just below), so it
  needs a decision rather than a blind fix. `UserView:2121`, `RecipeView:2503` and `FishingView:2560`
  also skip `super()`, but none of them page, so only the button-state half applies.
- 🗨 Footers are still written unconditionally here (`ffxiv.py:1730`/`:1758`), the same overwrite that
  motivated `KumaView.page_embed()`. Latent only — no ffxiv caller builds its own footer today. Port
  `page_embed()` if one ever does.
- ⚠️ `BaseView` does not inherit `KumaView`, so every `KumaView` fix has to be applied twice. Worth
  deciding whether the fork can be collapsed.

**UnicodeTable** (`utils/cog.py`):
- 🗨 The class docstring's `__Current Attributes__` block is a hand-maintained mirror of the attributes
  below it, so every new character has to be written twice or the two drift apart.

**Hints** (`extensions/hints.py`):
- ⚠️ `Remind me later` raises `retire_at`; it must **never** decrement `seen`. `seen` is a fact (times
  shown), `retire_at` is a preference (times wanted) — folding one into the other makes a snoozed view
  indistinguishable from a real one, which is unrecoverable and corrupts the counts `/hints` reports.
- ⚠️ `HINTS_PER_PAGE` is 8 because a full page measures **35** of the 40-component CV2 cap. Nine is 38,
  ten is 41. Raising it breaks the panel at whatever page first fills up.
- ⛔ **No global `users` table.** A snowflake is already a stable 64-bit natural key, so a surrogate
  would store 8 bytes to avoid storing 8, add an ordering rule to every write and a JOIN to every read,
  and re-couple the cog to core. `userid INTEGER NOT NULL` repeated per table is the join key.
    - 🗨 One reason given for this at design time was wrong and should not be re-quoted: `asqlite`
      *does* set `pragma foreign_keys=ON` per connection (`asqlite/__init__.py:501`). The decision
      stands on the other three points.
- 🗨 The panel is deliberately **not** persistent, unlike `SessionPanel` — its custom IDs encode the hint
  key so they cannot be pre-registered. `/hints` is cheap to re-run.
- No cog declares a `__hints__` yet. First one planned: on `/settings`, telling admins their own options
  live under `/preferences`.

**Preferences** (`extensions/preferences.py`):
- ⛔ `/settings` and `/preferences` **cannot** be merged into one command group. Discord applies
  `default_permissions` per top-level command and it cannot vary by subcommand, so admin+guild-only
  and open+DM-capable are unsatisfiable under one parent however they are nested. Don't retry this.
- ⚠️ `PreferencesPanel` is a Components V2 view, so its replies **cannot** carry `content` or `embeds`.
  Anything that needs to be said alongside it has to be a `TextDisplay` inside the panel or a separate
  ephemeral message, which is what a failed write does.
- 🗨 Four switches so far (`hints_enabled`, `hint_style_block`, `auto_mystbin`, `thread_rename`); the
  table is the place to add more, and `KumaCog.settings_choices()` means the command choice comes along
  for free — as does the `PreferencesPanel` row, which is built from the row the database hands back
  rather than a list here.
- 🗨 `enabled()` is the method other cogs should call for a switch, `value()` for a cog-declared choice.
  Both answer with the declared default rather than raising, so a preference read can never be why
  another cog's command fails.
- ⚠️ A choice preference costs three components to a switch's two — a select **cannot** be a `Section`
  accessory, only a button or a thumbnail can, so it needs its own `TextDisplay` and `ActionRow`. The
  panel is at 24 of Discord's 40 with one declared; past roughly five, it needs paging like `HintsPanel`.

**Housing** (`extensions/private/housing.py`):
- ⚠️ Untested against Discord. The models, the boolean coercion and both renderers are covered by an
  offline harness against a representative payload, but no listing has been dropped in the forum
  since the thread-swap fix — which is the one that matters, because the replacement thread was the
  one being deleted.
- 🗨 `?parse` re-runs `housing_thread_create`, which only does anything when the starter message is
  still a bare Zillow URL. On an already-rebuilt thread the starter message is the generated summary,
  so `?parse` is a no-op there. It is the recovery path for a listing that failed the first time, and
  it would read better if it said so when there is nothing to re-parse.
- 🗨 The forum is matched by name (`house-hunting`) on every event, and the family role ID is a class
  attribute. Both want moving into the settings table now that `moderator` has one.
- 🗨 The `?` commands are an if-chain; the cog's own docstring already calls for a mapping.
- ⛔ The `Zillow_*` attribute names stay as Zillow spells them. `__init__` maps the payload with
  `setattr(self, key, value)`, so renaming them for `N815` would break the mapping they exist for.
  Held with a file-level `# ruff: noqa: N801, N815` and a comment saying why.

**AutoMod** (`extensions/automod.py`):
- ⚠️ **Awaiting your review** — the cog never kicks. Both the class docstring and the `/mention_spam
  create` reply promise "established members are **kicked**", but `on_mention_spam` only bans new
  members and silently does nothing for everyone else. A written-up fix (an `AutoModAction` StrEnum,
  one `escalate()` that carries out the action, an owner report through `AutoModEmbed`, and the
  promise generated from the same constants that drive the behaviour) is ready to apply on your word.
- ⚠️ `new_member_role_ids` is declared, read by `_is_new_member`, and never populated — there is no
  command or table behind it, so the "has a new-member role" half of the tenure check is dead.
- 🗨 `_is_new_member` fails *open*: a member with no `joined_at` comes back False, so an unknown
  tenure is treated as established. That is the safer default, but it is undocumented.
- 🗨 `_track_rule` logs a failed insert and adds the rule to the in-memory set regardless, so a rule
  whose row never landed works until the next restart and then quietly stops escalating.
- 🗨 No `on_automod_rule_delete` listener, so a rule deleted through the Discord UI leaves its row
  behind and `tracked_rule_ids` keeps claiming it.
- 🗨 `AutoModEmbed` lives in `moderator.py` but is named for this cog. If AutoMod is to report
  escalations the way `_duplicate_attachment_check` does, it wants moving to `utils/embeds.py` so
  both cogs reach it the same way `KumaEmbed` is reached.

**Moderator** (`extensions/moderator.py`):
- ⚠️ `Moderator.migrate()` leaves a guild's rows alone when they *disagree*, since picking a winner
  would silently discard a setting, and the UNIQUE index is skipped for as long as that is true. It
  logs a warning per guild on every load — if one ever appears, reconcile that guild by hand.
- 🗨 `guild_settings` on the class is `dict[int, ModeratorSettings]` — the type object assigned as a
  value rather than an annotation, and nothing reads it. Either annotate it and use it as the
  settings cache it was meant to be, or drop it.
- 🗨 Add Moderator settings as needed. A new column in `moderator` gets a `ModeratorSettingsPanel` row and
  a `settings set` choice for free; `MOD_SETTING_SUMMARIES` is the only place to add anything by hand, and
  a column missing from it still renders. Keep `MOD_SETTINGS_EXCLUDED` as the one list of what to hide.
- ⚠️ `ModeratorSettingsPanel` is a Components V2 view, so its replies **cannot** carry `content` or
  `embeds`. Anything that needs to be said alongside it has to be a `TextDisplay` inside the panel or a
  separate ephemeral message, which is what a failed write does.
- Add a function to start and stop `self.bot.task_loops`.
- If `clear` is called in a reply, clear messages up until the "reply" message.
- Change `settings` command to `moderator_settings`.
- Consider making the interaction ephemeral.

**Preferences** (`extensions/preferences.py`) — see also the section further down:
- 🗨 There are two kinds, and which one a setting is decides where it is declared.
  - A core **switch** is two edits: a column on `USER_SETTINGS_SETUP_SQL` and an entry in
    `USER_SETTING_DEFAULTS`. `Preferences.migrate()` adds it to a live table on the next load, and the
    panel row, the `/preferences set` choice and the allowlist all follow from those two.
  - A cog's own **choice** is one edit, in that cog: a `Preference` on its `__preferences__`, read back
    with `Preferences.value()`. Stored in `user_preferences` rather than as a column, so there is no
    DDL and no load-order dependency, and it leaves when the extension does.
- 🗨 `/preferences set` still only offers the switches. Its choices come from `KumaCog.settings_choices`,
  which runs in the class body at import time and so cannot see a registry that is walked per call; the
  panel is the only way to change a cog-declared preference.

**Utility** (`extensions/utility.py`):
- ⭐ **DONE** `GithubIssueSubmissionModal` moved off the deprecated `TextInput(label=...)` onto
  `discord.ui.Label`, and the select-then-modal two-step collapsed into one modal. Repository and
  type are now selects *inside* it, so `GithubIssueSubmissionView`, `GithubIssueSubmissionSelect`,
  `GithubIssueSubmissionResult` and `check_results()` are gone, along with the `is_done`/`result`
  state threaded between them. The context menu opens the modal directly.
    - ⭐ **DONE** A fifth **Source** field, pre-filled with the message's jump URL and optional, so
      the link can be read before submitting and cleared when the issue has outgrown the message.
      The modal is now **full** at five children; a sixth means dropping one or moving it into the
      body, which is where the attachment list goes.
    - ⭐ **DONE** Attachments from the source message are listed in the issue body with filename,
      size and link, via `build_body()`.
        - ⚠️ **They are links, and Discord signs its CDN URLs so they expire in about a day.** An
          issue read a week later has dead links. The issue body says so in a blockquote rather than
          only in a code comment. Making them durable means re-hosting the bytes somewhere — mystbin
          covers text, nothing covers images today — which is a bigger decision than this command
          should make on its own. The GitHub issues API takes no file upload, so there is no direct
          route.
    - ⚠️ Untested against Discord. Verified offline: five `Label`s build, the body pre-fills from the
      message, Source pre-fills with the jump URL, a simulated submit reads every value back, and
      `build_body()` is checked both with attachments and source and with neither.
    - 🗨 The deprecation was quiet, which is why it sat: the *parameter* carries only a
      `.. deprecated:: 2.6` docstring marker and warns about nothing, and the `DeprecationWarning`
      fires only when the `.label` *property* is read or written. Grep for it elsewhere rather than
      waiting for the log.
- Add a generic command to take either an image URL/etc and turn the image into an app emoji.
- Expand `source` command to search any Object and its associated functions (not just registered bot commands).
    - Resolve arbitrary objects/attributes (classes, methods, functions) via dotted paths, not just `bot.get_command`.
    - Support returning multiple matches when a query is ambiguous.
    - Present the matches as a selectable list (View/Select menu) so the user can pick one.
    - On selection, return the full content (source link + code/snippet) for that specific result.
- Add auto-thread creation for a "set" channel, with specific rulesets (eg. Contains Images, @mentions, etc...)
- ⭐ **DONE** `/about` covers `/info` via aliases (`botinfo`, `info`, `bi`).
    - ⭐ **DONE** Intents object. Reprs as twenty-odd flags, most of them `False`, so it names the
      three *privileged* ones that are granted and prints the raw bitfield beside them.
    - ⭐ **DONE** Members/User list. `len([self.bot.get_all_members()])` measured a one element list
      wrapped around the generator, so it read `1` forever; it reports unique users and total members.
    - Latency was printed in seconds with an `ms` suffix; fixed alongside.
    - See about labeling the commit/update information?

**Gatekeeper** (`extensions/gatekeeper.py`):
- ⭐ **DONE** Keeping server status or current Online player count/etc.
    - Change channel type from VC to TextChannel and pull ongoing server chat.
    - Global chat between each Server if MC.
- ⭐ **DONE** Start, Stop and Restart of any server.
- Take Announcements channel that @role and pass those into the Server Console for each server to notify players.
- Notification when server is online. Server thread/WARN: Dedicated server took 88.557 seconds to load.
- Fixed Server list updating in realtime.
    - Fix logger prints when updating "available Instances" (It appears ~ 4 times).
- 🗨 `bot.task_loops` is only ever appended to and read by nothing (`kuma_kuma.py:465`), so a missing
  `cog_unload` is a leak rather than a visible failure. `extensions/private/_work.py:56` still has that
  gap, but it is underscored and never loaded.

**Reddit Image Crawler** (`extensions/reddit.py`):
- Change compare to a slash command.
    - support RedditEmbeds, file attachments and urls.
    - support message links/IDs.
    - Store last 50/60 Embeds? Use Title for listings?
- See about getting the Subreddits Banner/Icon and using that to populate the Embed.
- Components V2 conversion — `RedditPost`, `RedditPagePanel` and `reddit_preview` exist; the call
  sites still send `RedditEmbed`. Swapping them in needs three things fixed at the same time:
    - ⚠️ `on_reaction_compare` reads `reaction.message.embeds[0].image.url`. A V2 message has **no
      embeds**, so the ✅ compare goes dead the moment the crawler stops sending one. It has to read
      `message.attachments[0].url` (the file still rides along) or the gallery's URL.
    - ⚠️ `webhook_send`'s 413 retry mutates `embed.field_image` / `set_image`. The V2 equivalent is
      `RedditPost.use_remote_media()`, which repoints the gallery and empties `files`.
    - ⚠️ `KumaView` pages by swapping `embed=` on the message and cannot drive a `LayoutView` at all;
      `get_subreddit`, `_send_paginated_list` and `crawler_stats` each need `RedditPagedView` instead.
- 🗨 `UnicodeTable.em_dash` is `\Ufe31`, the *vertical* presentation form (`︱`). It reads as a broken
  pipe in a bullet list; a literal `—` is what the house `- **Key** — value` form wants.
- Verify pHash implementation.
- ⚠️ `check_subreddit` uses `search_by_name`, which hits the Reddit API and iterates a generator.
  Expensive for a simple existence check — could cache results or use a HEAD request to
  `/r/{sub}/about.json`. Its docstring already claims it does the latter.
- 🗨 `normalize_subreddit()` is applied by `add_subreddit` only. `get_subreddit`, `update_subreddit`
  and `info_subreddit` still take whatever is typed, though the first is free-text and the other two
  autocomplete off the table, so a URL is unlikely to reach them.
- ⚠️ UserWarning: Palette images with Transparency expressed in bytes should be converted to RGBA images.

**Sonarr and Radarr** (planned):
- ⭐ **DONE** Project scope, API research (endpoints + JSON shapes), and example clients written up in
  `.claude_asks/sonarr_radarr/README.md`, `.claude_asks/sonarr_radarr/sonarr/` and
  `.claude_asks/sonarr_radarr/radarr/`.
- ⭐ **DONE** Sonarr wrapper (`async_sonarr/`) and cog (`extensions/sonarr.py`) — `/sonarr
  list|info|add|remove|status`, owner gated, SignalR backed cache.
- Try to use TVDB for movie ID to search Sonarr better.
    - 🗨 Radarr's lookup accepts `imdb:tt…` terms — likely the better bridge between the two apps
      (see `.claude_asks/sonarr_radarr/API_RESEARCH.md`).

**Sonarr** (`extensions/sonarr.py`):
- ⚠️ Needs a `[SONARR]` section in `local.ini` (`url`, `api_key`, optional `url_base`) before the
  commands do anything; the cog loads and refuses until then. A commented template is in the file.
- ⚠️ Untested against a live instance — every check so far is against a mock Sonarr and an offline
  render of each panel. First run wants a look at `/sonarr status` to confirm the hub connects.
- ⚠️ `async_sonarr` resolves through an editable install (`pip install -e ~/gitHub/async_sonarr
  --no-deps`) until it is published; a fresh venv here needs that run before the cog will load.
- ⭐ **DONE** `async_sonarr/` lifted out to its own repo at `~/gitHub/async_sonarr`, the way
  `async_universalis` and `async_garlandtools` did.
- 🗨 The hub carries `queue` messages too, so a live-updating queue panel is possible without the
  Refresh button — the cache already subscribes, `StatusPanel` just does not listen yet.
- 🗨 Episode level commands (season/episode search, per-season monitoring) are modelled in
  `CommandName` but not exposed; `SeriesPanel` is where they would go. Tracked in the wrapper's own
  `TODO.md` now for the API half.

**Google Keep** (planned):
- Interface with Google Keep via the unofficial `gkeepapi` library (no official REST API exists).
    - `gkeepapi` authenticates with a Google account master token via `keep.login()` or `keep.resume()`.
    - Token should be stored securely (env var or secrets file) and reused across sessions via `keep.getMasterToken()`.
    - Sync is manual: call `keep.sync()` to pull remote changes; `keep.createNote()` / `note.delete()` / `keep.sync()` to push.
- Commands to consider:
    - Create a note with an optional title and body.
    - Append to an existing note by label or title search.
    - List notes (filterable by label or pinned status).
    - Delete or archive a note by title/label.
    - Checklist support: create a `gkeepapi.node.List` node and add `ListItem` children.
- Labels map to Discord command groups or filters; consider a `label` parameter on create/list.
- ⚠️ Rate limiting: `gkeepapi` is unofficial and undocumented — avoid hammering sync; debounce writes and
  sync lazily.
- ⚠️ `gkeepapi` is sync-only; wrap calls in `asyncio.to_thread()` to avoid blocking the event loop.

**FFXIV** (`extensions/ffxiv.py`):
- Store a Users inventory via `Item.id` and `Item.quantity`.
    - Create a `to_dict()` function for an Inventory Item.
- Switch to V2 COMPs -> https://gist.github.com/pythonmcpi/83b95f6e86a8155c07d4ff924967b325
- Change most button styles to secondary.
- ConsoleGamesWiki parsing via url searching.
    - Useful URL - https://ffxiv.consolegameswiki.com/mediawiki/index.php?search=Magitek&title=Special%3ASearch&profile=default&fulltext=1
- Convert saved gifs/video into animated Emojis. (Downloads folder)

### FFXIV — Issues
#### Item/Recipe
- Show Crafting Ingredients breakdown entirely.
    - Possibly show first way to obtain~
- Add Recipes related to an Ingredient via Button?

#### SpearFishing Embed
- Need to create one...

### FFXIV — Features/Improvements
- Timers for Housing, Journal, etc...
- Add wild card searching (multi item lookup).
- Add item watch list functionality.
- Add Inventory functionality.
- Add Collect list for glamour/etc.?
- Store a users recent item lookups? (3 - 4?)
