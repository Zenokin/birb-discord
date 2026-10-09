#!/usr/bin/env python3

"""
One-off cleanup: removes registered slash commands from Discord.

Usage (run from the same folder as your .env):
    python cleanup_commands.py            # clear GLOBAL commands only
    python cleanup_commands.py --guild    # clear the guild commands (DISCORD_GUILD_ID) only
    python cleanup_commands.py --all      # clear both

After running, restart bot.py: it re-registers its commands in whichever
mode your .env selects (guild if DISCORD_GUILD_ID is set, otherwise global).
"""

import argparse
import asyncio
import os

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = (os.getenv("DISCORD_TOKEN") or "").strip()
GUILD_ID = os.getenv("DISCORD_GUILD_ID")


async def show(tree, label, guild=None):
    cmds = await tree.fetch_commands(guild=guild)
    names = ", ".join(f"/{c.name}" for c in cmds) or "(none)"
    print(f"  {label}: {names}")


async def main(clear_global: bool, clear_guild: bool):
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN not set in .env")
    if clear_guild and not GUILD_ID:
        raise SystemExit("DISCORD_GUILD_ID not set in .env, can't clear guild commands")

    guild = discord.Object(id=int(GUILD_ID)) if GUILD_ID else None

    client = discord.Client(intents=discord.Intents.none())
    tree = app_commands.CommandTree(client)  # intentionally empty

    async with client:
        await client.login(TOKEN)

        print("Before:")
        await show(tree, "global")
        if guild:
            await show(tree, f"guild {GUILD_ID}", guild)

        # Syncing an empty tree overwrites Discord's list with nothing.
        if clear_global:
            await tree.sync()
        if clear_guild:
            await tree.sync(guild=guild)

        print("After:")
        await show(tree, "global")
        if guild:
            await show(tree, f"guild {GUILD_ID}", guild)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--guild", action="store_true", help="clear guild commands only")
    group.add_argument("--all", action="store_true", help="clear global and guild commands")
    args = parser.parse_args()

    clear_guild = args.guild or args.all
    clear_global = args.all or not args.guild
    asyncio.run(main(clear_global, clear_guild))