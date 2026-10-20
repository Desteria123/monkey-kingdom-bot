'''Non-invasive archival mirror + database snapshots for the Monkey Kingdom bot.

Existing Discord workflows remain authoritative. This module archives messages from
selected channels; it does not silently migrate their business logic to SQLite.
'''
import asyncio
import io
import json
import os
import sqlite3
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import discord
from discord import app_commands

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '1515095050995105862'))
DB_PATH = Path(os.getenv('MK_DATABASE_PATH', '/data/monkey_kingdom.sqlite3'))
BACKUP_ID = int(os.getenv('MK_BACKUP_CHANNEL_ID', '0'))
MANAGER_IDS = {1515096134824099941,1515437209308758216,1515432899095298198,1515163342606438530}
LEADERSHIP_IDS = MANAGER_IDS | {1554289393374732308}
DEFAULT_NAMES = ['recruitment-review','leadership-audit','loa-review','family-activity-log']
SOURCE_ID_KEYS = ['RECRUITMENT_REVIEW_CHANNEL_ID','LEADERSHIP_AUDIT_CHANNEL_ID','LOA_REVIEW_CHANNEL_ID','ACTIVITY_LOG_CHANNEL_ID','FAMILY_ACTIVITY_LOG_CHANNEL_ID','RULES_STORAGE_CHANNEL_ID']
BATCH = 500
INTERVAL = 3600
_lock = asyncio.Lock()
_task = None


def _connect():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('''CREATE TABLE IF NOT EXISTS messages(
      channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, author_id INTEGER,
      created_at TEXT, edited_at TEXT, content TEXT, embeds_json TEXT,
      attachments_json TEXT, archived_at TEXT NOT NULL,
      PRIMARY KEY(channel_id,message_id))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at)')
    c.commit()
    return c


def _store(messages):
    with _connect() as c:
        for m in messages:
            c.execute('''INSERT INTO messages(channel_id,message_id,author_id,created_at,edited_at,content,embeds_json,attachments_json,archived_at)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(channel_id,message_id) DO UPDATE SET
            edited_at=excluded.edited_at,content=excluded.content,embeds_json=excluded.embeds_json,
            attachments_json=excluded.attachments_json,archived_at=excluded.archived_at''',
            (m.channel.id,m.id,m.author.id,m.created_at.isoformat(),m.edited_at.isoformat() if m.edited_at else None,
             m.content, json.dumps([e.to_dict() for e in m.embeds],ensure_ascii=False),
             json.dumps([{'id':a.id,'filename':a.filename,'url':a.url,'size':a.size} for a in m.attachments],ensure_ascii=False),
             datetime.now(timezone.utc).isoformat()))
        c.commit()


def source_channels(guild):
    ids=set()
    for name in DEFAULT_NAMES:
        ch=discord.utils.get(guild.text_channels,name=name)
        if ch: ids.add(ch.id)
    for key in SOURCE_ID_KEYS:
        value=os.getenv(key,'0')
        if value.isdecimal() and int(value): ids.add(int(value))
    return [guild.get_channel(i) for i in ids if isinstance(guild.get_channel(i),discord.TextChannel) and i!=BACKUP_ID]


async def sync(guild):
    if not guild or guild.id!=GUILD_ID:return (0,0)
    count=0; success=0
    async with _lock:
        for ch in source_channels(guild):
            try:
                msgs=[m async for m in ch.history(limit=BATCH,oldest_first=False)]
                await asyncio.to_thread(_store,msgs)
                success+=1;count+=len(msgs)
            except (discord.HTTPException,PermissionError,OSError,sqlite3.Error) as e:
                print(f'BACKUP WARNING cannot archive #{ch.name}: {type(e).__name__}: {e}',flush=True)
    return (success,count)


def _snapshot():
    if not DB_PATH.exists():raise FileNotFoundError('No archive database yet')
    with _connect() as conn:
        buffer=io.BytesIO()
        target=sqlite3.connect(':memory:')
        conn.backup(target)
        target.commit()
        with io.BytesIO() as raw:
            # SQLite backup must be copied to a temporary local file; use :memory: serialize (Python 3.11+).
            raw.write(target.serialize()); data=raw.getvalue()
        target.close()
        row_count=conn.execute('SELECT count(*) FROM messages').fetchone()[0]
    bio=io.BytesIO()
    with zipfile.ZipFile(bio,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        z.writestr('monkey_kingdom.sqlite3',data)
        z.writestr('metadata.json',json.dumps({'created_at':datetime.now(timezone.utc).isoformat(), 'records':row_count,'version':1},indent=2))
    bio.seek(0)
    return bio,row_count


def authorized(member):
    return isinstance(member,discord.Member) and any(r.id in MANAGER_IDS for r in member.roles)


async def snapshot(guild):
    if not BACKUP_ID:raise RuntimeError('MK_BACKUP_CHANNEL_ID not set. Configure a PRIVATE backup channel.')
    ch=guild.get_channel(BACKUP_ID)
    if not isinstance(ch,discord.TextChannel):raise RuntimeError('Backup channel ID invalid or not cached')
    perms=ch.permissions_for(guild.me)
    if not (perms.view_channel and perms.send_messages and perms.attach_files and perms.read_message_history):
        raise RuntimeError('Bot needs View, Send, Attach Files, and Read History permissions in backup channel')
    async with _lock:
        bio,records=await asyncio.to_thread(_snapshot)
        if len(bio.getbuffer()) > 7_500_000:raise RuntimeError('Backup ZIP exceeds conservative 7.5 MB limit; use external object storage')
        filename=f'mk-backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip'
        msg=await ch.send(f'🔒 Monkey Kingdom database backup | {records} archived messages | UTC {datetime.now(timezone.utc):%Y-%m-%d %H:%M}', file=discord.File(bio,filename=filename),allowed_mentions=discord.AllowedMentions.none())
        return msg, records


async def _backup_recent(ch):
    async for m in ch.history(limit=25):
        if m.author.id==ch.guild.me.id and any(a.filename.startswith('mk-backup-') for a in m.attachments):
            return m.created_at
    return None


async def _worker(client):
    await client.wait_until_ready()
    while not client.is_closed():
        try:
            g=client.get_guild(GUILD_ID)
            if g:
                scanned,count=await sync(g)
                print(f'ARCHIVE: scanned {count} recent messages in {scanned} source channels',flush=True)
                if BACKUP_ID:
                    ch=g.get_channel(BACKUP_ID)
                    if isinstance(ch,discord.TextChannel):
                        last=await _backup_recent(ch)
                        if last is None or (discord.utils.utcnow()-last).total_seconds()>=24*3600:
                            msg,n=await snapshot(g)
                            print(f'BACKUP: sent {n} records to #{ch.name} (message {msg.id})',flush=True)
        except Exception as exc:
            print(f'BACKUP ERROR: {type(exc).__name__}: {exc}',flush=True)
        await asyncio.sleep(INTERVAL)


def install(tree,client):
    scope=discord.Object(id=GUILD_ID)
    group=app_commands.Group(name='backup',description='Private Kingdom archival backups',guild_ids=[GUILD_ID])

    @group.command(name='status',description='Check database status and backup configuration')
    async def status(interaction:discord.Interaction):
        if not authorized(interaction.user):
            await interaction.response.send_message('Family Director+ only.',ephemeral=True);return
        try:
            def read():
                with _connect() as conn:return conn.execute('SELECT count(*) FROM messages').fetchone()[0]
            n=await asyncio.to_thread(read)
            ch=interaction.guild.get_channel(BACKUP_ID) if BACKUP_ID else None
            await interaction.response.send_message(f'Archive messages: **{n}**\nDatabase path: `{DB_PATH}`\nBackup channel: {ch.mention if ch else "NOT CONFIGURED"}\nTracked source channels: **{len(source_channels(interaction.guild))}**\nNote: This is an archive; existing Discord workflows remain the source of truth.',ephemeral=True)
        except Exception as e:await interaction.response.send_message(f'Archive unavailable: {e}',ephemeral=True)

    @group.command(name='now',description='Archive recent records and upload a manual backup')
    async def now(interaction:discord.Interaction):
        if not authorized(interaction.user):
            await interaction.response.send_message('Family Director+ only.',ephemeral=True);return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            ch,n=await sync(interaction.guild)
            msg,rows=await snapshot(interaction.guild)
            await interaction.followup.send(f'✅ Archived {n} recent messages from {ch} channels; snapshot contains **{rows}** records. Backup: {msg.jump_url}',ephemeral=True)
        except Exception as e:await interaction.followup.send(f'❌ Backup failed: {type(e).__name__}: {e}',ephemeral=True)

    tree.add_command(group,guild=scope)


def start(client):
    global _task
    if _task is None or _task.done():_task=asyncio.create_task(_worker(client))
