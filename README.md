# birb — Brilliant Interval Reminder Bot (for Discord)

<img src="assets/birb.png" alt="birb icon" width="160" style="border-radius: 10px"/>

A self-hosted Discord bot for one-off and repeating reminders, with optional
random "jitter" so repeating reminders don't fire at the exact same time
every time. All commands are native Discord slash commands.

## Setup

1. Create a bot application at https://discord.com/developers/applications
   and add a bot user. You do **not** need to enable the Message Content
   intent — slash commands don't read message content.
2. Invite it to your server with the `bot` and `applications.commands`
   scopes, plus `Send Messages` permission via the OAuth2 menu.

Then pick one of the two ways to run it:

### Option A: Run with Python

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Copy `.env.example` to `.env` and fill it in:
   ```bash
   cp .env.example .env
   ```
   - `DISCORD_TOKEN` — your bot token (required)
   - `DISCORD_GUILD_ID` — optional, your test server's ID. While set, slash
     commands sync instantly to that one server. Remove it once you're happy,
     to do a global sync (visible in every server the bot is in — the first
     global sync can take up to ~1 hour to propagate, syncs after that are
     instant).
3. Run it:
   ```bash
   python bot.py
   ```

Reminders and per-server settings are saved to `birb-reminders.json` and
`birb-config.json` next to `bot.py`, so they survive restarts. Both files are
created automatically on first use. Logs are written to `birb.log` in the
same directory.

### Option B: Run with Docker

Uses the prebuilt image from GHCR — no local Python install needed.

1. Create `docker/birb.env` with your bot's config:
   ```
   DISCORD_TOKEN=your-token-here
   # DISCORD_GUILD_ID=123456789012345678
   ```
   Same two variables as the Python setup above (`DISCORD_GUILD_ID` is optional).
2. Start it:
   ```bash
   cd docker
   docker compose up -d
   ```
   This pulls `ghcr.io/zenokin/birb-discord:latest` and runs it as a
   non-root user inside the container.

Reminders, config, and the log file persist to `docker/data/birb` on the
host (mounted to `/app/data` in the container), so they survive
`docker compose restart` and image updates.

To build the image locally instead of pulling from GHCR — e.g. to test a
local change to `bot.py` before it's published — run `make build-image`
from the `docker/` directory (see `docker/Makefile`), then point
`compose.yml`'s `image:` at your local tag.

## Commands

### `/reminder create` — create a reminder
Parameters:
- **when** (required) — either a fixed date/time, or a daily time **window**:
  - `2026-08-10T13:00` — fires once at 13:00 on Aug 10, 2026 (server's default timezone)
  - `2026-08-10T13:00-18:00` — fires at a *random* time between 13:00 and
    18:00 on that date
- **interval** (optional) — repeats the reminder. Format is a number plus a
  unit: `s`, `m`, `h`, `d`, `w` (seconds/minutes/hours/days/weeks), e.g.
  `28d`, `12h`, `45m`.
- **stddev** (optional, requires **interval**) — adds gaussian
  ("semi-random") jitter around each future occurrence, in the same
  duration format, e.g. `5d` means each occurrence is normally distributed
  around the scheduled time with a standard deviation of ~5 days.
- **message** (optional) — the reminder text. Defaults to "⏰ Reminder!".

Examples:
```
/reminder create when:2026-08-15T13:00 message:Guild raid event!
/reminder create when:2026-08-15T13:00 interval:7d message:Weekly guild raid event!
/reminder create when:2026-08-15T13:00 interval:14d stddev:3d message:Spontaneous guild event on a random day!
/reminder create when:2026-08-15T13:00-18:00 message:Spontaneous guild event in the afternoon!
```

### `/reminder list` — list reminders
Shows every reminder for the server, numbered by ID, with its next trigger
time, repeat interval, jitter, and window (if any).

### `/reminder delete` — delete a reminder
Parameter: **reminder_id** — start typing and Discord will show an
autocomplete dropdown of your server's current reminders (ID + message
preview) to pick from.

### `/timezone` — default timezone
```
/timezone                                # show current timezone
/timezone tz_name:Europe/Brussels        # set it (requires Manage Server permission)
```
Defaults to `UTC`. Only affects reminders created *after* the change.

### `/help` — command reference
Posts an ephemeral summary of every command above, including the
`/reminder create` examples, so you don't have to leave Discord to look
them up.

## How scheduling works

- The bot checks for due reminders every 30 seconds.
- One-off reminders (no interval) are deleted after firing.
- Repeating reminders compute their *next* occurrence from a fixed anchor
  time + `interval * occurrence_count`, so they don't drift even with
  jitter applied.
- If a time window was given, each occurrence picks a fresh random time
  inside that window (uniform distribution) on the appropriate date.
- If a stddev was given (without a window), each occurrence is offset from
  its exact scheduled time by a random gaussian amount, clamped to at most
  2 standard deviations or half the interval (whichever is smaller). This
  keeps a single occurrence from swinging so far that it lands before the
  previous one or long after the next one — without the clamp, a stddev
  that's large relative to the interval (e.g. 15m stddev on a 30m interval)
  can otherwise produce gaps between consecutive reminders that look much
  bigger than expected, since each occurrence is jittered independently.

## License

[MIT](LICENSE)
