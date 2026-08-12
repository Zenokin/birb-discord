"""
Discord Reminder Bot ("birb") — slash command version
=======================================================

All commands are Discord slash commands under a `/reminder` group, plus a
top-level `/timezone`. There is no more `!` prefix / message-content parsing,
so the bot doesn't need the privileged Message Content intent at all.

Commands
--------
- /reminder create <when> [interval] [stddev] [message]
- /reminder list
- /reminder delete <reminder_id>      (autocompletes with your current reminders)
- /timezone [tz_name]                 (view, or set with Manage Server permission)

See README.md for full usage details and examples.

Data is persisted to config.json (per-guild settings) and reminders.json
(per-guild reminders) in the working directory, so reminders survive restarts.
"""

import discord
from discord import app_commands
from discord.ext import tasks
import logging
from dotenv import load_dotenv
import os
import re
import json
import random
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

load_dotenv()
token = (os.getenv('DISCORD_TOKEN') or '').strip()

# Optional: set DISCORD_GUILD_ID in .env while developing for instant command
# sync to a single test server. Leave unset for a global sync (can take up to
# ~1 hour to propagate to all servers the first time, then it's instant).
_guild_id_env = os.getenv('DISCORD_GUILD_ID')
GUILD_ID = int(_guild_id_env) if _guild_id_env else None

os.environ['TZ'] = 'Etc/UTC'

handler = logging.FileHandler(filename='birb.log', encoding='utf-8', mode='w')

CONFIG_FILE = 'birb-config.json'
REMINDERS_FILE = 'birb-reminders.json'

DURATION_RE = re.compile(r'^(\d+)([smhdw])$', re.IGNORECASE)
DURATION_UNITS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}

DATETIME_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})$')
DATETIME_WINDOW_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})-(\d{2}):(\d{2})$'
)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    # Write-then-rename so a crash or container kill mid-write can't leave a
    # truncated/corrupt file behind — os.replace() is atomic on POSIX.
    tmp_path = f"{path}.tmp"
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


config = load_json(CONFIG_FILE, {"guilds": {}})
reminders = load_json(REMINDERS_FILE, {"guilds": {}})
config.setdefault("guilds", {})
reminders.setdefault("guilds", {})


def get_guild_config(guild_id):
    gid = str(guild_id)
    if gid not in config["guilds"]:
        config["guilds"][gid] = {"timezone": "UTC", "next_id": 1}
        save_json(CONFIG_FILE, config)
    return config["guilds"][gid]


def get_guild_tz(guild_id):
    gc = get_guild_config(guild_id)
    try:
        return ZoneInfo(gc["timezone"])
    except Exception:
        return ZoneInfo("UTC")


def get_guild_reminders(guild_id):
    gid = str(guild_id)
    return reminders["guilds"].setdefault(gid, [])


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def parse_duration(token):
    """'28d' -> 2419200 (seconds). Returns None if it doesn't look like a duration."""
    if token is None:
        return None
    m = DURATION_RE.match(token)
    if not m:
        return None
    value, unit = int(m.group(1)), m.group(2).lower()
    return value * DURATION_UNITS[unit]


def format_duration(seconds):
    if not seconds:
        return "0s"
    remaining = int(seconds)
    parts = []
    for suffix, size in (('w', 604800), ('d', 86400), ('h', 3600), ('m', 60), ('s', 1)):
        if remaining >= size:
            val, remaining = divmod(remaining, size)
            parts.append(f"{val}{suffix}")
    return " ".join(parts) if parts else "0s"


def parse_datetime_token(token, tz):
    """
    Parses the 'when' argument.
    Returns (anchor_utc: datetime, window_start_seconds: int|None, window_end_seconds: int|None)
    Raises ValueError on bad input.
    """
    m = DATETIME_WINDOW_RE.match(token)
    if m:
        date_str, sh, sm, eh, em = m.groups()
        year, month, day = (int(x) for x in date_str.split('-'))
        start_h, start_m, end_h, end_m = int(sh), int(sm), int(eh), int(em)
        window_start = start_h * 3600 + start_m * 60
        window_end = end_h * 3600 + end_m * 60
        if window_end <= window_start:
            raise ValueError("window end must be after window start")
        local_dt = datetime(year, month, day, start_h, start_m, tzinfo=tz)
        return local_dt.astimezone(timezone.utc), window_start, window_end

    m = DATETIME_RE.match(token)
    if m:
        date_str, hh, mm = m.groups()
        year, month, day = (int(x) for x in date_str.split('-'))
        local_dt = datetime(year, month, day, int(hh), int(mm), tzinfo=tz)
        return local_dt.astimezone(timezone.utc), None, None

    raise ValueError(
        "expected format YYYY-MM-DDTHH:MM or YYYY-MM-DDTHH:MM-HH:MM"
    )


def compute_trigger(rem, occurrence):
    """Computes the UTC datetime for the given occurrence index of a reminder."""
    anchor = datetime.fromisoformat(rem["anchor_utc"])
    interval = rem["interval_seconds"] or 0
    base = anchor + timedelta(seconds=interval * occurrence)
    tz = ZoneInfo(rem["timezone"])

    if rem["window_start"] is not None and rem["window_end"] is not None:
        local_date = base.astimezone(tz).date()
        ws, we = rem["window_start"], rem["window_end"]
        rand_seconds = random.uniform(ws, we)
        local_midnight = datetime(local_date.year, local_date.month, local_date.day, tzinfo=tz)
        trigger_local = local_midnight + timedelta(seconds=rand_seconds)
        trigger = trigger_local.astimezone(timezone.utc)
    elif rem["stddev_seconds"]:
        # Clamp so a single occurrence can't swing wildly from its scheduled
        # slot (which could otherwise flip the order of consecutive
        # occurrences when stddev is large relative to the interval).
        cap_candidates = [2 * rem["stddev_seconds"]]
        if rem["interval_seconds"]:
            cap_candidates.append(rem["interval_seconds"] / 2)
        max_jitter = min(cap_candidates)
        jitter = random.gauss(0, rem["stddev_seconds"])
        jitter = max(-max_jitter, min(max_jitter, jitter))
        trigger = base + timedelta(seconds=jitter)
    else:
        trigger = base

    return trigger


# ---------------------------------------------------------------------------
# Bot setup (slash-command only — no message content intent needed)
# ---------------------------------------------------------------------------

class ReminderBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
        else:
            synced = await self.tree.sync()
        print(f"Synced {len(synced)} slash command(s)")


bot = ReminderBot()


@bot.event
async def on_ready():
    print("birb is ready for duty!")
    if not check_reminders.is_running():
        check_reminders.start()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.NoPrivateMessage):
        msg = "This command only works inside a server."
    elif isinstance(error, app_commands.MissingPermissions):
        msg = "You need the `Manage Server` permission to do that."
    else:
        logging.getLogger('discord').exception("App command error", exc_info=error)
        msg = f"⚠️ Something went wrong running that command: {error}"

    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# ---------------------------------------------------------------------------
# /reminder command group
# ---------------------------------------------------------------------------

reminder_group = app_commands.Group(name="reminder", description="Create, list, and delete reminders")


@reminder_group.command(name="create", description="Create a reminder, optionally repeating with random jitter")
@app_commands.describe(
    when="Fixed time '2026-08-10T13:00' or window '2026-08-10T13:00-18:00'",
    interval="Repeat every N units, e.g. 28d, 12h, 45m (optional)",
    stddev="Random jitter per repeat, e.g. 5d (optional, needs interval)",
    message="What to remind you about (optional)",
)
@app_commands.guild_only()
async def reminder_create(
    interaction: discord.Interaction,
    when: str,
    interval: str = None,
    stddev: str = None,
    message: str = "⏰ Reminder!",
):
    tz = get_guild_tz(interaction.guild_id)

    try:
        anchor_utc, window_start, window_end = parse_datetime_token(when, tz)
    except ValueError as e:
        await interaction.response.send_message(
            f"Couldn't understand `{when}` ({e}). Use `YYYY-MM-DDTHH:MM` or "
            f"a window `YYYY-MM-DDTHH:MM-HH:MM`.",
            ephemeral=True,
        )
        return

    interval_seconds = parse_duration(interval)
    if interval and interval_seconds is None:
        await interaction.response.send_message(
            f"Couldn't understand interval `{interval}`. Use a number plus a unit: "
            f"`s`, `m`, `h`, `d`, or `w`, e.g. `28d`.",
            ephemeral=True,
        )
        return

    stddev_seconds = None
    if stddev:
        if not interval_seconds:
            await interaction.response.send_message(
                "`stddev` only makes sense together with `interval`.", ephemeral=True
            )
            return
        stddev_seconds = parse_duration(stddev)
        if stddev_seconds is None:
            await interaction.response.send_message(
                f"Couldn't understand stddev `{stddev}`. Use a number plus a unit, e.g. `5d`.",
                ephemeral=True,
            )
            return

    gc = get_guild_config(interaction.guild_id)
    reminder_id = gc["next_id"]
    gc["next_id"] += 1
    save_json(CONFIG_FILE, config)

    rem = {
        "id": reminder_id,
        "channel_id": interaction.channel_id,
        "user_id": interaction.user.id,
        "message": message,
        "anchor_utc": anchor_utc.isoformat(),
        "interval_seconds": interval_seconds,
        "stddev_seconds": stddev_seconds,
        "window_start": window_start,
        "window_end": window_end,
        "timezone": str(tz),
        "occurrence": 0,
    }
    rem["next_trigger"] = compute_trigger(rem, 0).isoformat()

    get_guild_reminders(interaction.guild_id).append(rem)
    save_json(REMINDERS_FILE, reminders)

    local_next = datetime.fromisoformat(rem["next_trigger"]).astimezone(tz)
    extra = ""
    if interval_seconds:
        extra += f", repeating every {format_duration(interval_seconds)}"
    if stddev_seconds:
        extra += f" ±{format_duration(stddev_seconds)}"
    if window_start is not None:
        extra += ", randomized within the daily window"

    await interaction.response.send_message(
        f"✅ Reminder **#{reminder_id}** created. Next trigger: "
        f"`{local_next.strftime('%Y-%m-%d %H:%M %Z')}`{extra}."
    )


@reminder_group.command(name="list", description="List all reminders for this server")
@app_commands.guild_only()
async def reminder_list(interaction: discord.Interaction):
    rems = get_guild_reminders(interaction.guild_id)
    if not rems:
        await interaction.response.send_message(
            "No reminders set for this server. Create one with `/reminder create`."
        )
        return

    tz = get_guild_tz(interaction.guild_id)
    lines = []
    for r in sorted(rems, key=lambda x: x["id"]):
        next_local = datetime.fromisoformat(r["next_trigger"]).astimezone(tz)
        details = [f"next: {next_local.strftime('%Y-%m-%d %H:%M %Z')}"]
        if r["interval_seconds"]:
            details.append(f"every {format_duration(r['interval_seconds'])}")
        if r["stddev_seconds"]:
            details.append(f"±{format_duration(r['stddev_seconds'])} jitter")
        if r["window_start"] is not None:
            ws = format_duration(r["window_start"])
            we = format_duration(r["window_end"])
            details.append(f"window {ws}-{we}")
        channel = interaction.guild.get_channel(r["channel_id"])
        chname = channel.mention if channel else "unknown-channel"
        lines.append(f"**#{r['id']}** in {chname}: {r['message']}  _({', '.join(details)})_")

    chunk = ""
    first = True
    for line in lines:
        if len(chunk) + len(line) + 1 > 1900:
            if first:
                await interaction.response.send_message(chunk)
                first = False
            else:
                await interaction.followup.send(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        if first:
            await interaction.response.send_message(chunk)
        else:
            await interaction.followup.send(chunk)


async def reminder_id_autocomplete(interaction: discord.Interaction, current: str):
    """Populates the dropdown for the delete command's reminder_id parameter."""
    if interaction.guild_id is None:
        return []
    rems = get_guild_reminders(interaction.guild_id)
    choices = []
    for r in sorted(rems, key=lambda x: x["id"]):
        label = f"#{r['id']} — {r['message'][:40]}"
        if current == "" or current.lower() in label.lower():
            choices.append(app_commands.Choice(name=label, value=r["id"]))
    return choices[:25]  # Discord caps autocomplete results at 25


@reminder_group.command(name="delete", description="Delete a reminder by ID")
@app_commands.describe(reminder_id="Which reminder to delete")
@app_commands.autocomplete(reminder_id=reminder_id_autocomplete)
@app_commands.guild_only()
async def reminder_delete(interaction: discord.Interaction, reminder_id: int):
    rems = get_guild_reminders(interaction.guild_id)
    for i, r in enumerate(rems):
        if r["id"] == reminder_id:
            rems.pop(i)
            save_json(REMINDERS_FILE, reminders)
            await interaction.response.send_message(f"🗑️ Deleted reminder #{reminder_id}.")
            return

    await interaction.response.send_message(
        f"No reminder found with ID #{reminder_id}.", ephemeral=True
    )


bot.tree.add_command(reminder_group)


# ---------------------------------------------------------------------------
# /timezone — top-level command (view for anyone, set requires Manage Server)
# ---------------------------------------------------------------------------

@bot.tree.command(name="timezone", description="View or set this server's default timezone for new reminders")
@app_commands.describe(tz_name="IANA timezone to set, e.g. Europe/Brussels (leave empty to just view)")
@app_commands.guild_only()
async def timezone_cmd(interaction: discord.Interaction, tz_name: str = None):
    gc = get_guild_config(interaction.guild_id)

    if tz_name is None:
        await interaction.response.send_message(
            f"Current default timezone: `{gc['timezone']}`. "
            f"Use `/timezone tz_name:<IANA timezone>` to change it, e.g. `Europe/Brussels`."
        )
        return

    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "You need the `Manage Server` permission to change this.", ephemeral=True
        )
        return

    try:
        ZoneInfo(tz_name)
    except Exception:
        await interaction.response.send_message(
            f"Unknown timezone `{tz_name}`. Use an IANA name like `Europe/Brussels`, "
            f"`America/New_York`, or `UTC`.",
            ephemeral=True,
        )
        return

    gc["timezone"] = tz_name
    save_json(CONFIG_FILE, config)
    await interaction.response.send_message(
        f"✅ Default timezone set to `{tz_name}`. This affects newly created reminders only."
    )


# ---------------------------------------------------------------------------
# /help — top-level command
# ---------------------------------------------------------------------------

HELP_TEXT = (
    "📖 **birb commands**\n\n"
    "**/reminder create** — create a reminder, optionally repeating with random jitter\n"
    "• `when` (required) — fixed `YYYY-MM-DDTHH:MM`, or a daily window "
    "`YYYY-MM-DDTHH:MM-HH:MM` for a random time in that range\n"
    "• `interval` (optional) — repeat every `s`/`m`/`h`/`d`/`w`, e.g. `28d`, `12h`, `45m`\n"
    "• `stddev` (optional, needs `interval`) — gaussian jitter around each occurrence, e.g. `5d`\n"
    "• `message` (optional) — reminder text, defaults to \"⏰ Reminder!\"\n\n"
    "Examples:\n"
    "`/reminder create when:2026-08-15T13:00 message:Guild raid event!`\n"
    "`/reminder create when:2026-08-15T13:00 interval:7d message:Weekly guild raid event!`\n"
    "`/reminder create when:2026-08-15T13:00 interval:14d stddev:3d message:Spontaneous guild event on a random day!`\n"
    "`/reminder create when:2026-08-15T13:00-18:00 message:Spontaneous guild event in the afternoon!`\n\n"
    "**/reminder list** — list all reminders for this server\n"
    "**/reminder delete** — delete a reminder by ID (autocompletes with your current reminders)\n"
    "**/timezone** — view or set this server's default timezone (setting requires Manage Server permission)"
)


@bot.tree.command(name="help", description="Show birb's commands and usage examples")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(HELP_TEXT, ephemeral=True)


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

@tasks.loop(seconds=30)
async def check_reminders():
    now = datetime.now(timezone.utc)
    changed = False

    for gid, rems in list(reminders["guilds"].items()):
        for r in list(rems):
            next_trigger = datetime.fromisoformat(r["next_trigger"])
            if next_trigger > now:
                continue

            channel = bot.get_channel(r["channel_id"])
            if channel is not None:
                try:
                    await channel.send(f"⏰ <@{r['user_id']}> Reminder **#{r['id']}**: {r['message']}")
                except discord.DiscordException as e:
                    logging.getLogger('discord').warning(f"Failed to send reminder {r['id']}: {e}")
            else:
                logging.getLogger('discord').warning(
                    f"Reminder {r['id']}: channel {r['channel_id']} not found, skipping send"
                )

            if r["interval_seconds"]:
                r["occurrence"] += 1
                r["next_trigger"] = compute_trigger(r, r["occurrence"]).isoformat()
            else:
                rems.remove(r)

            changed = True

    if changed:
        save_json(REMINDERS_FILE, reminders)


@check_reminders.before_loop
async def before_check_reminders():
    await bot.wait_until_ready()


@check_reminders.error
async def check_reminders_error(error):
    # tasks.Loop stops silently on an unhandled exception, which would
    # otherwise kill reminder delivery for every guild until the process is
    # restarted. Log it and get the loop running again.
    logging.getLogger('discord').exception(
        "check_reminders loop crashed, restarting", exc_info=error
    )
    check_reminders.restart()


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if not token:
        raise SystemExit("DISCORD_TOKEN not set. Create a .env file (see .env.example).")
    bot.run(token, log_handler=handler, log_level=logging.DEBUG)
