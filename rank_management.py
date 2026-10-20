"""Monkey Kingdom role management: guild-only slash commands and private audit events."""
import asyncio
import os
from datetime import datetime, timezone
import discord
from discord import app_commands

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '1515095050995105862'))
KINGDOM_MEMBER = 1515317581794513007
POTENTIAL = 1532600141486489690
# Automatic promotion ladder stops at Lieutenant. Leadership titles require manual owner approval.
RANKS = [
    ('Prospect', 1554924737178767430),
    ('Young Gun', 1538750586927849510),
    ('Soldier', 1538752292109680793),
    ('Veterano', 1554136610352668782),
    ('Lieutenant', 1554289393374732308),
]
DIRECTOR = 1515163342606438530
SENIOR = {DIRECTOR, 1515432899095298198, 1515437209308758216, 1515096134824099941}
COMMAND = SENIOR | {1554289393374732308}
PROTECTED = SENIOR
MANAGEMENT_CATEGORY_ID = 1521764991592829059
AUDIT_NAME = 'leadership-audit'
AUDIT_ID = int(os.getenv('LEADERSHIP_AUDIT_CHANNEL_ID', '0'))
CHANGE_LOCK = asyncio.Lock()


def is_director(member: discord.Member) -> bool:
    return any(r.id in SENIOR for r in member.roles)


def is_command(member: discord.Member) -> bool:
    return is_director(member) or any(r.id == 1554289393374732308 for r in member.roles)


def rank_index(member: discord.Member):
    matches = [i for i, (_, rid) in enumerate(RANKS) if any(r.id == rid for r in member.roles)]
    return max(matches) if matches else None


def guild_member(guild: discord.Guild, user: discord.Member) -> bool:
    return user.guild.id == guild.id


def audit_channel(guild: discord.Guild):
    if AUDIT_ID:
        ch = guild.get_channel(AUDIT_ID)
        return ch if isinstance(ch, discord.TextChannel) else None
    return discord.utils.get(guild.text_channels, name=AUDIT_NAME)


def actor_can_change(actor: discord.Member, target: discord.Member, destination: int | None, removing=False):
    if actor.id == target.id:
        return 'You cannot change your own rank.'
    if not is_command(actor):
        return 'Lieutenant or higher is required.'
    if any(r.id in PROTECTED for r in target.roles):
        return 'Senior leadership roles are protected. Manage them manually as server owner.'
    if removing and not is_director(actor):
        return 'Only Family Director or higher can remove members from the Kingdom.'
    if not is_director(actor):
        if rank_index(target) is not None and rank_index(target) >= 4:
            return 'Lieutenants cannot change other Lieutenants.'
        if destination is not None and destination > 2:
            return 'Lieutenants may promote/demote up to Soldier. Higher ranks need Director approval.'
    elif destination is not None and destination >= len(RANKS):
        return 'Senior leadership titles require manual owner approval.'
    # Discord hierarchy still governs writes even if named role grants command access.
    if actor.id != actor.guild.owner_id and target.top_role >= actor.top_role:
        return 'You cannot manage someone with an equal or higher Discord role.'
    return None


async def resolve_audit(guild: discord.Guild, create=False):
    existing = audit_channel(guild)
    if existing or not create:
        return existing
    if AUDIT_ID:
        raise RuntimeError('LEADERSHIP_AUDIT_CHANNEL_ID does not match a text channel.')
    category = guild.get_channel(MANAGEMENT_CATEGORY_ID)
    if not isinstance(category, discord.CategoryChannel):
        raise RuntimeError('Management category not found.')
    me = guild.me
    ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
          me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True, read_message_history=True)}
    for rid in SENIOR:
        role = guild.get_role(rid)
        if role:
            ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)
    ch = await guild.create_text_channel(AUDIT_NAME, category=category, overwrites=ow,
             topic='Private Monkey Kingdom promotion, demotion and removal audit trail')
    print(f'Created private audit channel #{ch.name} ({ch.id}); review its effective permissions.')
    return ch


async def log_action(guild, actor, target, operation, before, after, reason):
    channel = audit_channel(guild)
    if not channel:
        print(f'WARNING audit channel not configured; action={operation} actor={actor.id} target={target.id}')
        return False
    embed = discord.Embed(title=f'👑 Kingdom rank action: {operation}', color=discord.Color.gold(), timestamp=datetime.now(timezone.utc))
    embed.add_field(name='Member', value=f'{target.mention} (`{target.id}`)', inline=False)
    embed.add_field(name='Authorized by', value=f'{actor.mention} (`{actor.id}`)', inline=False)
    embed.add_field(name='Previous rank', value=before or 'None', inline=True)
    embed.add_field(name='New rank', value=after or 'None', inline=True)
    embed.add_field(name='Reason', value=discord.utils.escape_mentions(reason[:500]), inline=False)
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True
    except discord.HTTPException as exc:
        print(f'WARNING unable to write leadership audit: {exc}')
        return False


def install(tree: app_commands.CommandTree):
    scope = discord.Object(id=GUILD_ID)
    
    @tree.command(name='memberinfo', description='View a Monkey Kingdom member rank', guild=scope)
    @app_commands.describe(member='Discord member to inspect')
    async def memberinfo(interaction: discord.Interaction, member: discord.Member):
        if not isinstance(interaction.user, discord.Member) or not is_command(interaction.user):
            await interaction.response.send_message('Lieutenant+ only.', ephemeral=True); return
        i = rank_index(member)
        ranks = [r.name for r in member.roles if r.id in SENIOR]
        rank = ', '.join(ranks) if ranks else RANKS[i][0] if i is not None else 'Unranked'
        has_family = any(r.id == KINGDOM_MEMBER for r in member.roles)
        await interaction.response.send_message(
            f'👑 {member.mention}\n**Rank:** {rank}\n**Kingdom-Member:** {"Yes" if has_family else "No"}\n**User ID:** `{member.id}`',
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    async def edit_rank(interaction: discord.Interaction, member: discord.Member, reason: str, direction: int):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message('Server use only.', ephemeral=True); return
        reason = reason.strip()
        if not reason:
            await interaction.response.send_message('A reason is required.', ephemeral=True); return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with CHANGE_LOCK:
            # Refetch for current roles; do not act on potentially stale slash-command member payload.
            try:
                member = await interaction.guild.fetch_member(member.id)
                actor = await interaction.guild.fetch_member(interaction.user.id)
            except discord.HTTPException:
                await interaction.followup.send('Could not refresh member information.', ephemeral=True); return
            audit = audit_channel(interaction.guild)
            if audit is None or not audit.permissions_for(interaction.guild.me).send_messages:
                await interaction.followup.send('Private leadership audit channel is not ready. Ask an administrator to configure it first.', ephemeral=True); return
            current = rank_index(member)
            if current is None:
                await interaction.followup.send('Member has no supported rank. Accepted applicants should start as Prospect.', ephemeral=True); return
            destination = current + direction
            if not (0 <= destination < len(RANKS)):
                await interaction.followup.send('This member is at the end of the automatic rank ladder.', ephemeral=True); return
            problem = actor_can_change(actor, member, destination)
            if problem:
                await interaction.followup.send(problem, ephemeral=True); return
            if not any(r.id == KINGDOM_MEMBER for r in member.roles):
                await interaction.followup.send('Member is missing Kingdom-Member; correct membership first.', ephemeral=True); return
            me = interaction.guild.me
            current_roles = [r for r in member.roles if r.id in [v for _, v in RANKS]]
            destination_role = interaction.guild.get_role(RANKS[destination][1])
            if not me or not me.guild_permissions.manage_roles or not destination_role or any(r >= me.top_role for r in [*current_roles, destination_role]):
                await interaction.followup.send('Bot needs Manage Roles and a higher role than all affected ranks.', ephemeral=True); return
            try:
                # Atomic member.edit with all desired role IDs avoids a half-promoted role state.
                desired = [r for r in member.roles if r.id not in {v for _, v in RANKS} and r != interaction.guild.default_role]
                desired.append(destination_role)
                await member.edit(roles=desired, reason=f'Monkey Kingdom rank update by {actor.id}: {reason[:350]}')
            except discord.HTTPException as exc:
                await interaction.followup.send(f'Role update failed: {type(exc).__name__}.', ephemeral=True); return
            action = 'Promotion' if direction > 0 else 'Demotion'
            logged = await log_action(interaction.guild, actor, member, action, RANKS[current][0], RANKS[destination][0], reason)
        await interaction.followup.send(f'✅ {member.mention}: **{RANKS[current][0]} → {RANKS[destination][0]}**.' + ('' if logged else ' ⚠️ Audit logging failed; notify the server owner.'), ephemeral=True)

    @tree.command(name='promote', description='Promote one step in the Kingdom family ladder', guild=scope)
    @app_commands.describe(member='Member to promote', reason='Required reason for audit log')
    async def promote(interaction: discord.Interaction, member: discord.Member, reason: str):
        await edit_rank(interaction, member, reason, 1)

    @tree.command(name='demote', description='Demote one step in the Kingdom family ladder', guild=scope)
    @app_commands.describe(member='Member to demote', reason='Required reason for audit log')
    async def demote(interaction: discord.Interaction, member: discord.Member, reason: str):
        await edit_rank(interaction, member, reason, -1)

    @tree.command(name='removefamily', description='Revoke family membership and rank (Director+)', guild=scope)
    @app_commands.describe(member='Member to remove', reason='Required reason for audit log')
    async def removefamily(interaction: discord.Interaction, member: discord.Member, reason: str):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message('Server use only.', ephemeral=True); return
        if not reason.strip():
            await interaction.response.send_message('A reason is required.', ephemeral=True); return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with CHANGE_LOCK:
            try:
                member = await interaction.guild.fetch_member(member.id)
                actor = await interaction.guild.fetch_member(interaction.user.id)
            except discord.HTTPException:
                await interaction.followup.send('Could not refresh member data.', ephemeral=True); return
            issue = actor_can_change(actor, member, None, removing=True)
            if issue:
                await interaction.followup.send(issue, ephemeral=True); return
            audit = audit_channel(interaction.guild)
            if audit is None or not audit.permissions_for(interaction.guild.me).send_messages:
                await interaction.followup.send('Private leadership audit channel is not ready. Ask an administrator to configure it first.', ephemeral=True); return
            removable = {KINGDOM_MEMBER, POTENTIAL} | {rid for _, rid in RANKS}
            before = ', '.join(r.name for r in member.roles if r.id in removable) or 'None'
            if before == 'None':
                await interaction.followup.send('Member has no Kingdom membership roles to remove.', ephemeral=True); return
            me = interaction.guild.me
            changed = [r for r in member.roles if r.id in removable]
            if not me or not me.guild_permissions.manage_roles or any(r >= me.top_role for r in changed):
                await interaction.followup.send('Bot cannot manage one or more membership roles.', ephemeral=True); return
            desired = [r for r in member.roles if r.id not in removable and r != interaction.guild.default_role]
            try:
                await member.edit(roles=desired, reason=f'Kingdom family removal by {actor.id}: {reason[:350]}')
            except discord.HTTPException as exc:
                await interaction.followup.send(f'Removal failed: {type(exc).__name__}', ephemeral=True); return
            logged = await log_action(interaction.guild, actor, member, 'Removal', before, 'No family access', reason)
        await interaction.followup.send(f'✅ Removed Kingdom access from {member.mention}.' + ('' if logged else ' ⚠️ Audit logging failed.'), ephemeral=True)
