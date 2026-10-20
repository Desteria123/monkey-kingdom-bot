"""Monkey Kingdom V8 private leadership dashboard, read-only by design."""
import asyncio
import os
import re
import sqlite3
from pathlib import Path
import discord
from discord import app_commands

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '1515095050995105862'))
KINGDOM_MEMBER_ID = 1515317581794513007
LIEUTENANT_ID = 1554289393374732308
LEADERSHIP_IDS = {LIEUTENANT_ID,1515163342606438530,1515432899095298198,1515437209308758216,1515096134824099941}
RANKS = [
    ('MONKEY KING',1515096134824099941),('Right-Hand-King',1515437209308758216),
    ('Co-King',1515432899095298198),('Family Director',1515163342606438530),
    ('Lieutenant',LIEUTENANT_ID),('Veterano',1554136610352668782),
    ('Soldier',1538752292109680793),('Young Gun',1538750586927849510),
    ('Prospect',1554924737178767430),('Potential recruit',1532600141486489690)
]
DB_PATH = Path(os.getenv('MK_DATABASE_PATH','/data/monkey_kingdom.sqlite3'))
GOLD = discord.Color.from_rgb(241,196,15)

def allowed(user):
    return isinstance(user,discord.Member) and any(r.id in LEADERSHIP_IDS for r in user.roles)

def channel_for(guild, env_var, default):
    raw = os.getenv(env_var,'').strip()
    if raw.isdecimal() and int(raw):
        ch = guild.get_channel(int(raw))
        if isinstance(ch,discord.TextChannel): return ch
    return discord.utils.get(guild.text_channels,name=default)

async def read_recent(guild, env, fallback):
    channel = channel_for(guild,env,fallback)
    if channel is None: return None,None
    me=guild.me
    p=channel.permissions_for(me)
    if not (p.view_channel and p.read_message_history):return channel,None
    try:
        messages=[m async for m in channel.history(limit=300)]
        return channel,messages
    except (discord.HTTPException,discord.Forbidden):
        return channel,None

def local_archive_count():
    if not DB_PATH.is_file(): return None
    try:
        with sqlite3.connect(f'file:{DB_PATH.as_posix()}?mode=ro',uri=True,timeout=4) as c:
            return c.execute('SELECT COUNT(*) FROM messages').fetchone()[0]
    except (sqlite3.Error,OSError):return None

async def build_dashboard(guild):
    embed=discord.Embed(title='👑🐒 MONKEY KINGDOM — LEADERSHIP DASHBOARD',
                        description='Private command overview • counts are based on accessible records, not full historical exports.',
                        color=GOLD,timestamp=discord.utils.utcnow())
    member_role=guild.get_role(KINGDOM_MEMBER_ID)
    # member cache may be incomplete without a full member fetch
    live_member_count=sum(1 for m in guild.members if member_role in m.roles) if member_role else None
    cached_total=len(guild.members)
    embed.add_field(name='👑 Family members',value=f'{live_member_count} currently cached' if live_member_count is not None else 'Role not found',inline=True)
    embed.add_field(name='🏰 Discord server',value=f'{guild.member_count or cached_total} total members',inline=True)
    counts=[]
    for name,rid in RANKS:
        role=guild.get_role(rid)
        if role:
            n=sum(1 for m in guild.members if role in m.roles)
            counts.append(f'**{name}:** {n}')
    embed.add_field(name='🎖️ Ranks (cached membership)',value='\n'.join(counts)[:1024] or 'No ranks available',inline=False)
    errors=[]
    recruitment,appmsgs=await read_recent(guild,'RECRUITMENT_REVIEW_CHANNEL_ID','recruitment-review')
    if appmsgs is None:errors.append('Recruitment review unavailable')
    else:
        statuses={'Pending':0,'Accepted':0,'Rejected':0}
        for m in appmsgs:
            for e in m.embeds:
                match=re.fullmatch(r'Applicant ID: (\d+) \| Status: (Pending|Accepted|Rejected)',e.footer.text or '')
                if match: statuses[match.group(2)]+=1
        embed.add_field(name='📝 Recruitment (latest 300 messages)',value=' | '.join(f'{k}: **{v}**' for k,v in statuses.items()),inline=False)
    loa,loamsgs=await read_recent(guild,'LOA_REVIEW_CHANNEL_ID','loa-review')
    if loamsgs is None: errors.append('LOA reviews unavailable')
    else:
        statuses={'PENDING':0,'APPROVED':0,'DENIED':0}
        for m in loamsgs:
            for e in m.embeds:
                foot=(e.footer.text or '').upper()
                if foot.startswith('MK-LOA:'):
                    for k in statuses:
                        if foot.endswith('|'+k):statuses[k]+=1;break
        embed.add_field(name='✈️ LOA (latest 300 messages)',value=' | '.join(f'{k.title()}: **{v}**' for k,v in statuses.items()),inline=False)
    audit,auditmsgs=await read_recent(guild,'LEADERSHIP_AUDIT_CHANNEL_ID','leadership-audit')
    if auditmsgs is None:errors.append('Leadership audit unavailable')
    else:
        recent=[]
        for m in auditmsgs:
            for e in m.embeds:
                if (e.title or '').startswith('👑 Kingdom rank action:'):
                    recent.append(f'• {(e.title or "").split(":",1)[-1].strip()} — {discord.utils.format_dt(m.created_at,"R")}')
            if len(recent)>=3:break
        embed.add_field(name='🔱 Recent rank decisions',value='\n'.join(recent) or 'No recorded actions in recent history',inline=False)
    activity,actmsgs=await read_recent(guild,'FAMILY_ACTIVITY_LOG_CHANNEL_ID','family-activity-log')
    if actmsgs is None:errors.append('Family activity log unavailable')
    else:
        attendance=sum(any(e.title=='📅 Family Meeting Attendance' for e in m.embeds) for m in actmsgs)
        events=sum(any(e.title=='🐒 Family Activity Record' for e in m.embeds) for m in actmsgs)
        embed.add_field(name='📊 Activity (latest 300 messages)',value=f'Activity records: **{events}** | Attendance records: **{attendance}**',inline=False)
    archived=await asyncio.to_thread(local_archive_count)
    embed.add_field(name='💾 Archive',value=(f'{archived} archived messages' if archived is not None else 'Not available or not configured'),inline=False)
    embed.set_footer(text=('⚠️ '+ '; '.join(errors)[:180]) if errors else 'Private view • Read-only • Refresh with /dashboard')
    return embed

def install(tree):
    guild_scope=discord.Object(id=GUILD_ID)
    @tree.command(name='dashboard',description='Private family leadership overview (Lieutenant+)',guild=guild_scope)
    async def dashboard(interaction:discord.Interaction):
        if not allowed(interaction.user):
            await interaction.response.send_message('🔒 Lieutenant or higher is required.',ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            report=await build_dashboard(interaction.guild)
            await interaction.followup.send(embed=report,ephemeral=True,allowed_mentions=discord.AllowedMentions.none())
        except Exception as exc:
            print(f'DASHBOARD ERROR: {type(exc).__name__}: {exc}',flush=True)
            await interaction.followup.send('Could not prepare dashboard. Check Railway logs and channel permissions.',ephemeral=True)
