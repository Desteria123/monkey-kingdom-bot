"""Monkey Kingdom: persistent welcome bot. No role or existing-channel permission changes."""
import argparse
import asyncio
import os
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv
from recruitment import ApplyView, DecisionView, configure, review_channel
from rank_management import install as install_rank_commands, resolve_audit
from activity_loa import install as install_activity_commands, ReviewButton, ensure_channels
from kingdom_announcements import install as install_announce_command
from setup_commands import install as install_setup_commands
from rules_management import install as install_rules_commands
from backup_manager import install as install_backup_commands, start as start_backup_worker
from dashboard import install as install_dashboard_command

load_dotenv(Path(__file__).resolve().parent / '.env')

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '1515095050995105862'))
RECRUITMENT_CHANNEL_ID = 1530372808129904851
INFORMATION_CATEGORY_ID = 1515095050995105863
WELCOME_CHANNEL_ID = int(os.getenv('WELCOME_CHANNEL_ID', '0'))
TOKEN = os.getenv('DISCORD_BOT_TOKEN', '').strip()
BANNER_PATH = Path(__file__).resolve().parent / 'monkey_banner.png'
BANNER_NAME = 'monkey_banner.png'

parser = argparse.ArgumentParser(description='Monkey Kingdom welcome bot')
parser.add_argument('--check', action='store_true', help='Check bot access without changing channels')
parser.add_argument('--test', action='store_true', help='Post sample embed to the welcome channel')
parser.add_argument('--setup-recruitment', action='store_true', help='Publish recruitment panel and create private review channel')
args = parser.parse_args()

if not TOKEN:
    sys.exit('Missing DISCORD_BOT_TOKEN in .env. Keep your token private.')

intents = discord.Intents.default()
intents.members = True
client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)
install_rank_commands(tree)
install_activity_commands(tree)
install_announce_command(tree)
install_setup_commands(tree, get_welcome_channel=lambda g: get_welcome_channel(g, create=False))
install_rules_commands(tree)
install_backup_commands(tree, client)
install_dashboard_command(tree)

@client.event
async def setup_hook():
    client.add_view(ApplyView())
    client.add_view(DecisionView())
    client.add_view(ReviewButton())
    scope = discord.Object(id=GUILD_ID)
    local_names = [command.name for command in tree.get_commands(guild=scope)]
    print(f"Guild commands pending sync: {', '.join(local_names)}", flush=True)
    if 'dashboard' not in local_names:
        raise RuntimeError('Dashboard was not registered; refusing to sync')
    if 'backup' not in local_names:
        raise RuntimeError('Backup command group was not registered locally; refusing to sync')
    synced = await tree.sync(guild=scope)
    synced_names = [command.name for command in synced]
    print(f"Discord guild commands synced: {', '.join(synced_names)}", flush=True)
    if 'backup' not in synced_names:
        print('WARNING: /backup was not returned by Discord command sync', flush=True)


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
    if BANNER_PATH.is_file():
        embed.set_image(url=f'attachment://{BANNER_NAME}')
    if member:
        embed.set_thumbnail(url=member.display_avatar.url)
    return embed


async def send_welcome(channel: discord.TextChannel, member: discord.Member | None, guild: discord.Guild) -> None:
    """Attach the image to EACH message, so no external image hosting is needed."""
    embed = welcome_embed(member, guild)
    mentions = (discord.AllowedMentions(users=[member], roles=False, everyone=False)
                if member else discord.AllowedMentions.none())
    if BANNER_PATH.is_file():
        # Uploading as an attachment keeps the embedded image functional on Railway.
        with BANNER_PATH.open('rb') as banner_file:
            await channel.send(embed=embed, file=discord.File(banner_file, filename=BANNER_NAME),
                               allowed_mentions=mentions)
    else:
        print(f'WARNING: Missing {BANNER_NAME} next to welcome_bot.py; sending text-only welcome.', file=sys.stderr)
        await channel.send(embed=embed, allowed_mentions=mentions)


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
    if not (args.check or args.test or args.setup_recruitment):
        start_backup_worker(client)
    if os.getenv('ACTIVITY_AUTO_SETUP', '').lower() == 'true' and not args.check and not args.test:
        try:
            await ensure_channels(guild)
        except Exception as exc:
            print(f'ERROR setting up LOA/activity channels: {type(exc).__name__}: {exc}', file=sys.stderr)
    if os.getenv('RANK_AUDIT_AUTO_SETUP', '').lower() == 'true' and not args.check and not args.test:
        try:
            await resolve_audit(guild, create=True)
        except Exception as exc:
            print(f'ERROR setting up rank audit channel: {type(exc).__name__}: {exc}', file=sys.stderr)
    if os.getenv('RECRUITMENT_AUTO_SETUP', '').lower() == 'true' and not args.check and not args.setup_recruitment and not args.test:
        try:
            await configure(guild)
        except Exception as exc:
            print(f'ERROR automatic recruitment setup: {type(exc).__name__}: {exc}', file=sys.stderr)
    # Optional one-shot refresh of the EXISTING recruitment panel with the branded image.
    if os.getenv('BRANDING_REFRESH_RECRUITMENT', '').lower() == 'true' and not (args.check or args.test or args.setup_recruitment):
        try:
            await configure(guild)
        except Exception as exc:
            print(f'ERROR refreshing recruitment artwork: {type(exc).__name__}: {exc}', file=sys.stderr)
    if args.setup_recruitment:
        try:
            await configure(guild)
        except Exception as exc:
            print(f'ERROR setting up recruitment: {type(exc).__name__}: {exc}', file=sys.stderr)
        await client.close()
        return
    if args.check:
        review = review_channel(guild)
        print(f'Recruitment review channel: {review.name if review else "Not configured"}')
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
            await send_welcome(channel, None, guild)
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
        await send_welcome(channel, member, member.guild)
        print(f'Welcomed member ID {member.id}')
    except discord.Forbidden:
        print('Cannot post welcome: missing permissions in #welcome', file=sys.stderr)
    except Exception as exc:
        print(f'Welcome failed for member {member.id}: {type(exc).__name__}: {exc}', file=sys.stderr)


try:
    client.run(TOKEN, log_handler=None)
except discord.PrivilegedIntentsRequired:
    sys.exit('Enable Server Members Intent in Discord Developer Portal > Bot, then retry.')
