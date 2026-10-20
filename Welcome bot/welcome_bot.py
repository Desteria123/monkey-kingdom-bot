"""Monkey Kingdom: persistent welcome bot. No role or existing-channel permission changes."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / '.env')

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '1515095050995105862'))
RECRUITMENT_CHANNEL_ID = 1530372808129904851
INFORMATION_CATEGORY_ID = 1515095050995105863
WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID', '0'))
TOKEN = os.getenv('DISCORD_BOT_TOKEN', '').strip()

parser = argparse.ArgumentParser(description='Monkey Kingdom welcome bot')
parser.add_argument('--check', action='store_true', help='Check bot access without changing channels')
parser.add_argument('--test', action='store_true', help='Post sample embed to the welcome channel')
args = parser.parse_args()

if not TOKEN:
    sys.exit('Missing DISCORD_BOT_TOKEN in .env. Keep your token private.')

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)


def welcome_embed(member: discord.Member | None, guild: discord.Guild) -> discord.Embed:
    mention = member.mention if member else '**New Member (test)**'
    embed = discord.Embed(
        title='👑🐒 WELCOME TO MONKEY KINGDOM!',
        description=(
            f'🔥 **WELCOME TO THE JUNGLE, {mention}!** 🔥\n\n'
            'You have entered **MONKEY KINGDOM** — where loyalty, respect, and family come first.\n\n'
            '**👑 YOUR FIRST STEPS**\n'
            f'📝 Visit <#{RECRUITMENT_CHANNEL_ID}> to learn how to join the family.\n'
            '🤝 Introduce yourself and get to know the Kingdom.\n'
            '📢 Once accepted, leadership will grant you the `Kingdom-Member` role.\n\n'
            '**🐒 NO ALLIANCES. JUST FAMILY.**\n'
            '**COME BROKE. COME UNKNOWN. BECOME KNOWN BY EVERYONE.**'
        ),
        color=discord.Color.from_rgb(241, 196, 15),
    )
    embed.set_footer(text=f'THE CITY IS YOUR PLAYGROUND. THE FAMILY IS YOUR BACKUP. • {guild.name}')
    if member:
        embed.set_thumbnail(url=member.display_avatar.url)
    return embed


async def get_welcome_channel(guild: discord.Guild, create: bool) -> discord.TextChannel | None:
    if WELCOME_CHANNEL_ID:
        channel = guild.get_channel(WELCOME_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f'WELCOME_CHANNEL_ID {WELCOME_CHANNEL_ID} is not a text channel in this guild')
        return channel
    for channel in guild.text_channels:
        if channel.name.casefold() in ('welcome', '👋・welcome', '👋┃welcome'):
            return channel
    if not create:
        return None
    category = guild.get_channel(INFORMATION_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        raise RuntimeError('Information category not found. Set WELCOME_CHANNEL_ID for a pre-created channel.')
    me = guild.me
    if me is None:
        raise RuntimeError('Bot membership not visible in the guild.')
    overrides = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True),
        me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True),
    }
    channel = await guild.create_text_channel(
        'welcome', category=category,
        overwrites=overrides, reason='Monkey Kingdom automatic welcome channel',
        topic='Welcome to Monkey Kingdom! Visit recruitment to apply.',
    )
    print(f'Created #welcome: {channel.id}')
    print(f'Optional: add WELCOME_CHANNEL_ID={channel.id} to your .env')
    return channel


@client.event
async def on_ready():
    guild = client.get_guild(GUILD_ID)
    if guild is None:
        print(f'ERROR: Bot is not in server {GUILD_ID}.', file=sys.stderr)
        await client.close()
        return
    print(f'Connected as {client.user} to {guild.name} ({guild.id})')
    if args.check:
        channel = await get_welcome_channel(guild, create=False)
        if channel:
            me = guild.me
            perms = channel.permissions_for(me)
            print(f'Welcome channel: #{channel.name} ({channel.id})')
            print(f'Bot can view={perms.view_channel}, send={perms.send_messages}, embed={perms.embed_links}')
        else:
            print('No #welcome channel yet; --run or --test will create one.')
        print('Check Developer Portal → Bot → Server Members Intent is ON.')
        await client.close()
        return
    try:
        channel = await get_welcome_channel(guild, create=True)
        if channel is None:
            raise RuntimeError('Welcome channel unavailable')
        perms = channel.permissions_for(guild.me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            raise RuntimeError('Bot needs View Channel, Send Messages and Embed Links in #welcome')
        if args.test:
            await channel.send(embed=welcome_embed(None, guild), allowed_mentions=discord.AllowedMentions.none())
            print(f'Sample sent to #{channel.name}; no member pinged.')
            await client.close()
        else:
            print(f'Listening for new joins; welcome channel is #{channel.name} ({channel.id}). Keep this process running.')
    except Exception as exc:
        print(f'ERROR preparing welcome channel: {type(exc).__name__}: {exc}', file=sys.stderr)
        await client.close()


@client.event
async def on_member_join(member: discord.Member):
    if member.guild.id != GUILD_ID:
        return
    try:
        channel = await get_welcome_channel(member.guild, create=False)
        if channel is None:
            print('Cannot welcome: #welcome channel missing', file=sys.stderr)
            return
        await channel.send(
            embed=welcome_embed(member, member.guild),
            allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False),
        )
        print(f'Welcomed member ID {member.id}')
    except discord.Forbidden:
        print('Cannot post welcome: missing permissions in #welcome', file=sys.stderr)
    except Exception as exc:
        print(f'Welcome failed for member {member.id}: {type(exc).__name__}: {exc}', file=sys.stderr)


try:
    client.run(TOKEN, log_handler=None)
except discord.PrivilegedIntentsRequired:
    sys.exit('Enable Server Members Intent in Discord Developer Portal > Bot, then retry.')
