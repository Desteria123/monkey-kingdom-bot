"""Monkey Kingdom: private LOA reviews, attendance and activity logs."""
import asyncio
import os
from datetime import datetime, date, timezone
import discord
from discord import app_commands

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '1515095050995105862'))
KINGDOM = 1515317581794513007
LEADERSHIP = (1554289393374732308, 1515163342606438530, 1515432899095298198,
              1515437209308758216, 1515096134824099941)
MANAGEMENT_CATEGORY = 1521764991592829059
LOA_CHANNEL_ID = int(os.getenv('LOA_REVIEW_CHANNEL_ID', '0'))
ACTIVITY_CHANNEL_ID = int(os.getenv('ACTIVITY_LOG_CHANNEL_ID', '0'))
GOLD = discord.Color.from_rgb(241, 196, 15)
LOCK = asyncio.Lock()


def leader(member):
    return isinstance(member, discord.Member) and any(r.id in LEADERSHIP for r in member.roles)


def family(member):
    return isinstance(member, discord.Member) and any(r.id == KINGDOM for r in member.roles)


def configured_channel(guild, identifier, name):
    ch = guild.get_channel(identifier) if identifier else discord.utils.get(guild.text_channels, name=name)
    return ch if isinstance(ch, discord.TextChannel) else None


def loa_channel(guild):
    return configured_channel(guild, LOA_CHANNEL_ID, 'loa-review')


def activity_channel(guild):
    return configured_channel(guild, ACTIVITY_CHANNEL_ID, 'family-activity-log')


async def ensure_channels(guild):
    category = guild.get_channel(MANAGEMENT_CATEGORY)
    if not isinstance(category, discord.CategoryChannel):
        raise RuntimeError('Management category missing')
    me = guild.me
    overrides = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)
    }
    for rid in LEADERSHIP:
        role = guild.get_role(rid)
        if role:
            overrides[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    for name, existing in [('loa-review', loa_channel(guild)), ('family-activity-log', activity_channel(guild))]:
        if existing:
            print(f'Existing private #{name}: {existing.id} — permissions not changed')
            continue
        if (name == 'loa-review' and LOA_CHANNEL_ID) or (name == 'family-activity-log' and ACTIVITY_CHANNEL_ID):
            raise RuntimeError(f'Configured channel ID for {name} is missing; not creating duplicate')
        ch = await guild.create_text_channel(name, category=category, overwrites=overrides,
            reason='Monkey Kingdom private leadership operations')
        print(f'Created private #{name}: {ch.id} — inspect permissions and set Railway channel ID')


def valid_dates(start, end):
    try:
        a = date.fromisoformat(start)
        b = date.fromisoformat(end)
        if a > b: return 'End date must be on or after the start date.'
        if b < date.today(): return 'End date cannot be in the past.'
        if (b-a).days > 180: return 'LOA cannot exceed 180 days. Contact leadership.'
    except ValueError:
        return 'Dates must be YYYY-MM-DD (example: 2026-11-02).'
    return None


class ReviewButton(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def decide(self, interaction, approve):
        if not interaction.guild or interaction.guild.id != GUILD_ID or not leader(interaction.user):
            await interaction.response.send_message('Only Lieutenant and above can review LOAs.', ephemeral=True)
            return
        if not isinstance(interaction.channel, discord.TextChannel) or interaction.channel.id != getattr(loa_channel(interaction.guild), 'id', None):
            await interaction.response.send_message('This action is only valid in the LOA review channel.', ephemeral=True)
            return
        async with LOCK:
            msg = interaction.message
            if not msg or not msg.embeds:
                await interaction.response.send_message('Request information missing.', ephemeral=True); return
            embed = msg.embeds[0].copy()
            footer = embed.footer.text or ''
            if not footer.startswith('MK-LOA:') or '|PENDING' not in footer:
                await interaction.response.send_message('This LOA has already been processed or is invalid.', ephemeral=True); return
            try:
                user_id = int(footer.split(':', 1)[1].split('|', 1)[0])
            except (ValueError, IndexError):
                await interaction.response.send_message('Invalid applicant ID.', ephemeral=True); return
            status = 'APPROVED' if approve else 'DENIED'
            embed.color = discord.Color.green() if approve else discord.Color.red()
            embed.add_field(name='Decision', value=f'{status} by {interaction.user.mention}', inline=False)
            embed.set_footer(text=f'MK-LOA:{user_id}|{status}')
            await interaction.response.defer(ephemeral=True)
            try:
                await msg.edit(embed=embed, view=None, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                await interaction.followup.send('Failed to save decision. Try again.', ephemeral=True); return
            try:
                user = await interaction.client.fetch_user(user_id)
                await user.send(f'🐒 Monkey Kingdom LOA request: **{status}**. Reviewed by leadership.')
            except discord.HTTPException:
                pass
            log = activity_channel(interaction.guild)
            if log:
                await log.send(f'📋 LOA **{status}** | Member: <@{user_id}> | Reviewer: {interaction.user.mention} | Request: {msg.id}',
                               allowed_mentions=discord.AllowedMentions.none())
            await interaction.followup.send(f'LOA {status.lower()} and recorded.', ephemeral=True)

    @discord.ui.button(label='Approve LOA', style=discord.ButtonStyle.success, custom_id='mk_loa_approve_v1')
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.decide(interaction, True)

    @discord.ui.button(label='Deny LOA', style=discord.ButtonStyle.danger, custom_id='mk_loa_deny_v1')
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.decide(interaction, False)


def install(tree):
    scope = discord.Object(id=GUILD_ID)

    @tree.command(name='loa', description='Submit a private leave-of-absence request', guild=scope)
    @app_commands.describe(start='YYYY-MM-DD', end='YYYY-MM-DD', reason='Why you need leave')
    async def loa(interaction: discord.Interaction, start: str, end: str, reason: str):
        if not interaction.guild or not family(interaction.user):
            await interaction.response.send_message('Only accepted Kingdom members can request LOA.', ephemeral=True); return
        issue = valid_dates(start, end)
        if issue:
            await interaction.response.send_message(issue, ephemeral=True); return
        if not reason.strip():
            await interaction.response.send_message('A reason is required.', ephemeral=True); return
        ch = loa_channel(interaction.guild)
        if ch is None:
            await interaction.response.send_message('Private LOA review channel is not configured. Contact leadership.', ephemeral=True); return
        embed = discord.Embed(title='✈️ New Monkey Kingdom LOA Request', color=GOLD)
        embed.add_field(name='Member', value=interaction.user.mention, inline=False)
        embed.add_field(name='Dates', value=f'{start} through {end}', inline=False)
        embed.add_field(name='Reason', value=discord.utils.escape_mentions(reason[:900]), inline=False)
        embed.set_footer(text=f'MK-LOA:{interaction.user.id}|PENDING')
        try:
            await ch.send(embed=embed, view=ReviewButton(), allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            await interaction.response.send_message('Could not submit LOA. Ask leadership to check bot permissions.', ephemeral=True); return
        await interaction.response.send_message('✅ LOA request sent privately to Lieutenant+ for review.', ephemeral=True)

    @tree.command(name='attendance', description='Record attendance for a family meeting (Lieutenant+)', guild=scope)
    @app_commands.describe(member='Member being recorded', meeting='Meeting name or date', status='Attendance result')
    @app_commands.choices(status=[app_commands.Choice(name='Present', value='Present'),
                                   app_commands.Choice(name='Absent', value='Absent'),
                                   app_commands.Choice(name='Excused', value='Excused')])
    async def attendance(interaction: discord.Interaction, member: discord.Member, meeting: str,
                         status: app_commands.Choice[str]):
        if not interaction.guild or not leader(interaction.user):
            await interaction.response.send_message('Lieutenant+ only.', ephemeral=True); return
        if not family(member):
            await interaction.response.send_message('That member does not have Kingdom-Member.', ephemeral=True); return
        ch = activity_channel(interaction.guild)
        if not ch:
            await interaction.response.send_message('Activity log channel not configured.', ephemeral=True); return
        embed = discord.Embed(title='📅 Family Meeting Attendance', color=GOLD, timestamp=discord.utils.utcnow())
        embed.add_field(name='Member', value=member.mention)
        embed.add_field(name='Status', value=status.value)
        embed.add_field(name='Meeting', value=discord.utils.escape_mentions(meeting[:250]), inline=False)
        embed.add_field(name='Recorded by', value=interaction.user.mention)
        await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message('✅ Attendance recorded in private activity log.', ephemeral=True)

    @tree.command(name='activity', description='Log family participation (Lieutenant+)', guild=scope)
    @app_commands.describe(member='Participating member', activity='Activity or operation', notes='Optional details')
    async def activity(interaction: discord.Interaction, member: discord.Member, activity: str, notes: str = ''):
        if not interaction.guild or not leader(interaction.user):
            await interaction.response.send_message('Lieutenant+ only.', ephemeral=True); return
        if not family(member):
            await interaction.response.send_message('That member does not have Kingdom-Member.', ephemeral=True); return
        ch = activity_channel(interaction.guild)
        if not ch:
            await interaction.response.send_message('Activity log channel not configured.', ephemeral=True); return
        embed = discord.Embed(title='🐒 Family Activity Record', color=GOLD, timestamp=discord.utils.utcnow())
        embed.add_field(name='Member', value=member.mention)
        embed.add_field(name='Activity', value=discord.utils.escape_mentions(activity[:250]), inline=False)
        if notes: embed.add_field(name='Notes', value=discord.utils.escape_mentions(notes[:900]), inline=False)
        embed.add_field(name='Recorded by', value=interaction.user.mention)
        await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await interaction.response.send_message('✅ Activity recorded privately.', ephemeral=True)

    @tree.command(name='roster', description='List the family roster by current ranks (Lieutenant+)', guild=scope)
    async def roster(interaction: discord.Interaction):
        if not interaction.guild or not leader(interaction.user):
            await interaction.response.send_message('Lieutenant+ only.', ephemeral=True); return
        guild = interaction.guild
        try:
            if not guild.chunked: await guild.chunk(cache=True)
        except discord.HTTPException:
            pass
        ranks = [
            ('MONKEY KING',1515096134824099941), ('Right-Hand-King',1515437209308758216),
            ('Co-King',1515432899095298198), ('Family Director',1515163342606438530),
            ('Lieutenant',1554289393374732308), ('Veterano',1554136610352668782),
            ('Soldier',1538752292109680793), ('Young Gun',1538750586927849510),
            ('Prospect',1554924737178767430),
        ]
        members = [m for m in guild.members if family(m)]
        lines = []
        for name, rid in ranks:
            group = [m.display_name for m in members if any(r.id == rid for r in m.roles)]
            if group:
                preview = ', '.join(group[:12])
                if len(group) > 12: preview += f', +{len(group)-12} more'
                lines.append(f'**{name} ({len(group)})** — {discord.utils.escape_markdown(preview)}')
        if not lines: lines = ['No members found; verify Server Members Intent.']
        output = '\n'.join(lines)
        if len(output) > 1800: output = output[:1750] + '\n… (truncated)'
        await interaction.response.send_message(f'👑 **MONKEY KINGDOM ROSTER** • {len(members)} Kingdom-Members\n\n{output}', ephemeral=True)
