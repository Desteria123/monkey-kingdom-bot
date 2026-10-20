"""Idempotent manual posting for Monkey Kingdom's permanent Discord panels."""
import os
from pathlib import Path
import discord
from discord import app_commands
from recruitment import panel_embed, ApplyView, RECRUITMENT_ID

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '1515095050995105862'))
RULES_ID = int(os.getenv('KINGDOM_RULES_CHANNEL_ID','1515162965257617589'))
LEADERSHIP_IDS = {1554289393374732308, 1515163342606438530, 1515432899095298198,
                  1515437209308758216, 1515096134824099941}
BANNER = Path(__file__).resolve().parent / 'monkey_banner.png'
RULES_TITLE = '📜 MONKEY KINGDOM — SERVER RULES'
WELCOME_TITLE = '👑🐒 MONKEY KINGDOM — START HERE'


def authorized(interaction):
    return (interaction.guild_id == GUILD_ID and isinstance(interaction.user, discord.Member) and
            (interaction.user.guild_permissions.administrator or
             any(r.id in LEADERSHIP_IDS for r in interaction.user.roles)))

async def existing(channel, title):
    """Check pinned posts and 1000 latest messages; fail closed if history unavailable."""
    messages = await channel.pins()
    seen = {m.id for m in messages}
    async for message in channel.history(limit=1000):
        if message.id not in seen:
            messages.append(message)
    for message in messages:
        if message.author.id == channel.guild.me.id and message.embeds and message.embeds[0].title == title:
            return message
    return None

async def post_embed(channel, embed, view=None):
    bot = channel.guild.me
    perms = channel.permissions_for(bot)
    if not (perms.view_channel and perms.send_messages and perms.embed_links and perms.read_message_history):
        raise RuntimeError(f'Insufficient bot permissions in #{channel.name}')
    if BANNER.is_file():
        if not perms.attach_files:
            raise RuntimeError(f'Bot cannot Attach Files in #{channel.name}')
        embed.set_image(url='attachment://monkey_banner.png')
        with BANNER.open('rb') as f:
            return await channel.send(embed=embed, view=view, file=discord.File(f, filename='monkey_banner.png'),
                                      allowed_mentions=discord.AllowedMentions.none())
    return await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())

def rules_embed():
    return discord.Embed(title=RULES_TITLE,
        description=('**👑 Welcome to the Kingdom.**\n\n'
                     '1. Respect members, visitors, and leadership.\n'
                     '2. No harassment, discrimination, spam, or unauthorized advertising.\n'
                     '3. Follow Discord and FiveM server rules.\n'
                     '4. Keep private family information confidential.\n'
                     '5. Use appropriate channels and cooperate with authorized moderators.\n\n'
                     '**NO ALLIANCES. JUST FAMILY.**'),color=0xF1C40F)

def static_welcome_embed():
    return discord.Embed(title=WELCOME_TITLE,
        description=('🔥 **WELCOME TO THE JUNGLE!**\n\n'
                     'Welcome to **Monkey Kingdom** — loyalty, respect, and family come first.\n\n'
                     f'📝 Start at <#{RECRUITMENT_ID}> to apply.\n'
                     f'📜 Review our rules in <#{RULES_ID}> when you have access.\n\n'
                     '**COME BROKE. COME UNKNOWN. BECOME KNOWN BY EVERYONE.**'),color=0xF1C40F)

def install(tree, get_welcome_channel):
    scope = discord.Object(id=GUILD_ID)
    @tree.command(name='setup', description='Check or restore permanent Kingdom messages (no duplicates)', guild=scope)
    @app_commands.describe(section='Which permanent message to check or restore')
    @app_commands.choices(section=[
        app_commands.Choice(name='Status (check only)', value='status'),
        app_commands.Choice(name='All missing posts', value='all'),
        app_commands.Choice(name='Welcome', value='welcome'),
        app_commands.Choice(name='Recruitment', value='recruitment'),
        app_commands.Choice(name='Rules', value='rules'),
    ])
    async def setup(interaction: discord.Interaction, section: app_commands.Choice[str]):
        if not authorized(interaction):
            await interaction.response.send_message('Lieutenant+ only.',ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True,thinking=True)
        guild = interaction.guild
        targets = {
            'welcome': (await get_welcome_channel(guild), WELCOME_TITLE),
            'recruitment': (guild.get_channel(RECRUITMENT_ID), panel_embed().title),
            'rules': (guild.get_channel(RULES_ID), RULES_TITLE),
        }
        keys = ['welcome','recruitment','rules'] if section.value in ('all','status') else [section.value]
        report = []
        for key in keys:
            ch,title=targets[key]
            if not isinstance(ch,discord.TextChannel):
                report.append(f'❌ {key}: channel not found; no action taken')
                continue
            try:
                prev=await existing(ch,title)
                if prev:
                    report.append(f'✅ {key}: exists — {prev.jump_url}')
                    continue
                if section.value == 'status':
                    report.append(f'⚠️ {key}: missing (last 1000 messages and pins checked)')
                    continue
                # Avoid posting if the bot cannot reliably inspect history.
                if key == 'welcome':
                    created=await post_embed(ch,static_welcome_embed())
                elif key == 'rules':
                    from rules_management import draft_text, formatted_rules
                    created=await post_embed(ch,formatted_rules(await draft_text(guild)))
                else:
                    created=await post_embed(ch,panel_embed(),view=ApplyView())
                try:
                    await created.pin(reason='Permanent Monkey Kingdom setup message')
                except (discord.Forbidden,discord.HTTPException):
                    report.append(f'⚠️ {key}: posted but could not pin; older history may make future checks less reliable')
                report.append(f'🆕 {key}: posted — {created.jump_url}')
            except (discord.HTTPException,RuntimeError) as exc:
                report.append(f'❌ {key}: {type(exc).__name__}: {exc}')
        await interaction.followup.send('**Setup results**\n'+'\n'.join(report),ephemeral=True)
