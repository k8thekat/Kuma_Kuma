# Kuma_Kuma_Bear
A personal Discord Bot for assisting my management and development of Discord.py Modules/Cogs.

Kuma Kuma Bear is built on [discord.py](https://github.com/Rapptz/discord.py) with an extension-based architecture, an async SQLite database ([asqlite](https://github.com/Rapptz/asqlite)), cached HTTP sessions (`aiohttp_client_cache`), and [Sentry](https://sentry.io) error reporting.

## Table of Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Modules](#modules)
- [Usage](#usage)
- [Project Layout](#project-layout)
- [Credits](#credits)
- [Issues]
- [Changelog]

# Requirements
- Python **3.12+**
- The packages listed in `requirements.txt` (installed into a `.venv`).
- A Discord Bot application with the **Members** and **Message Content** privileged intents enabled.

# Installation
Invite link -> https://discord.com/oauth2/authorize?client_id=1053576011935129640

To self-host:
```bash
git clone https://github.com/k8thekat/Kuma_Kuma.git
cd Kuma_Kuma
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

# Configuration
Kuma Kuma reads its credentials from a `local.ini` file in the project root:

```ini
[DISCORD]
token = <your bot token>
logging_webhook = <discord webhook url for log uploads>

[SENTRY_IO]
dsn = <sentry dsn>

[GITHUB]
owner = <github username>
token = <github personal access token>

[SONARR]
url = http://localhost:8989
api_key = <sonarr api key>
url_base =

[RADARR]
url = http://localhost:7878
api_key = <radarr api key>
url_base =
```

The `[SONARR]` and `[RADARR]` sections are optional — omitting one disables that half of the `sonarr_radarr` cog without affecting the other.

The bot creates its SQLite tables (`prefix`, `owners`) automatically on first startup in `kuma_kuma.sqlite`.

# Modules
Extensions are auto-discovered from the `extensions/` directory (and the nested `extensions/private/` directory). Any module prefixed with `_` is skipped, making it easy to toggle cogs on and off.

See [`extensions/README.MD`](extensions/README.MD) for the public cogs.

| Extension | Purpose |
| --- | --- |
| `automod` | Mention-spam protection using Discord's native AutoMod with custom escalation. |
| `claude` | Interact with the Claude Code CLI from Discord (sandboxed to the project directory). |
| `ffxiv` | FFXIV item lookup, marketboard data, and watch lists via my [moogles_intuition](https://github.com/k8thekat/moogles_intuition) library. |
| `gatekeeper` | CubeCoders AMP server management and whitelisting (Minecraft, etc.). |
| `moderator` | Moderation utilities — `who_is`, `clear`, `trust`, per-guild `prefixes`, `sync`, and spam-message tracking. |
| `reddit` | Subreddit image crawler with duplicate-hash detection, posting via webhooks. |
| `repl_cog` | In-Discord Python REPL and eval sessions for development. |
| `sonarr_radarr` | Sonarr and Radarr media management — browse, search, add, remove, and monitor series/movies via [a_sonarr_radarr](https://github.com/k8thekat/a_sonarr_radarr). |
| `utility` | General utilities, including "yoinking" emojis/stickers across guilds. |

# Usage
- Default prefix is `kuma` or mentioning the bot; per-guild prefixes can be added with the `prefixes` command.
- Run the bot manually:
    ```bash
    .venv/bin/python kuma_kuma.py
    ```
- Or use the included `Kuma.bash` script (crontab friendly) — it checks the PID file and starts the bot detached under the venv:
    ```bash
    ./Kuma.bash
    ```
- Command invocations are auto-deleted after a timeout, command errors are DM'd to the owner (long tracebacks are uploaded to [mystb.in](https://mystb.in)), and logs rotate nightly in `logs/`.

# Project Layout
| Path | Description |
| --- | --- |
| `kuma_kuma.py` | Bot entry point — `Kuma_Kuma` (the `commands.Bot` subclass), `KumaCommandTree`, `LogHandler`, and config loading. |
| `extensions/` | Auto-discovered public cogs. |
| `extensions/private/` | Private cogs (git submodule). |
| `utils/` | Shared helpers — `KumaCog`, `KumaContext`, `KumaEmbed`, UI views, converters, fuzzy matching, timezones, and emoji resources. |
| `resources/` | Fonts, application emoji assets, and templates. |

# Credits
- [Rapptz](https://github.com/Rapptz) — discord.py & asqlite.
- [AbstractUmbra](https://github.com/AbstractUmbra) — mystbin.py and countless dpy patterns.
- Licensed under the [GNU GPL v3](COPYING).

[Repo]: https://github.com/k8thekat/Kuma_Kuma
[Issues]: https://github.com/k8thekat/Kuma_Kuma/issues
[Changelog]: https://github.com/k8thekat/Kuma_Kuma/blob/development/CHANGELOG.md
