"""Private Monkey Kingdom applications. No privileged message-content intent required."""
import asyncio
import os
import re
from pathlib import Path
import discord

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '1515095050995105862'))
RECRUITMENT_ID = int(os.getenv('RECRUITMENT_CHANNEL_ID', '1530372808129904851'))
REVIEW_ID = int(os.getenv('RECRUITMENT_REVIEW_CHANNEL_ID', '0'))
MANAGEMENT_CATEGORY_ID = 1521764991592829059
KINGDOM_ROLE = 1515317581794513007
PROSPECT_ROLE = 1554924737178767430
POTENTIAL_ROLE = 1532600141486489690
LEADERSHIP_ROLE_IDS = [1554289393374732308, 1515163342606438530, 1515432899095298198,
                       1515437209308758216, 1515096134824099941]
GOLD = discord.Color.from_rgb(241, 196, 15)
BANNER_PATH = Path(__file__).resolve().parent / 'monkey_banner.png'
BANNER_NAME = 'monkey_banner.png'
PANEL_ID = 'mk_apply_v1'
ACCEPT_ID = 'mk_accept_v1'
REJECT_ID = 'mk_reject_v1'


def reviewer(member: discord.Member) -> bool:
    return member.guild_permissions.administrator or any(role.id in LEADERSHIP_ROLE_IDS for role in member.roles)


def review_channel(guild: discord.Guild) -> discord.TextChannel | None:
    if REVIEW_ID:
        channel = guild.get_channel(REVIEW_ID)
        return channel if isinstance(channel, discord.TextChannel) else None
    cat = guild.get_channel(MANAGEMENT_CATEGORY_ID)
    if isinstance(cat, discord.CategoryChannel):
        return next((c for c in cat.text_channels if c.name == 'recruitment-review'), None)
    return None


async def ensure_review_channel(guild: discord.Guild) -> discord.TextChannel:
    existing = review_channel(guild)
    if existing:
        return existing
    if REVIEW_ID:
        raise RuntimeError('RECRUITMENT_REVIEW_CHANNEL_ID does not point to a valid text channel')
    cat = guild.get_channel(MANAGEMENT_CATEGORY_ID)
    if not isinstance(cat, discord.CategoryChannel):
        raise RuntimeError('Management category missing; create a private review channel manually')
    me = guild.me
    if me is None:
        raise RuntimeError('Bot member unavailable')
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        me: discord.PermissionOverwrite(view_channel=True, send_messages=True, embed_links=True,
                                        read_message_history=True),
    }
    for rid in LEADERSHIP_ROLE_IDS:
        role = guild.get_role(rid)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, read_message_history=True,
                                                            send_messages=True)
    channel = await guild.create_text_channel('recruitment-review', category=cat, overwrites=overwrites,
                                               reason='Monkey Kingdom private recruitment review')
    print(f'Created private #{channel.name} ({channel.id}). Set RECRUITMENT_REVIEW_CHANNEL_ID={channel.id} in Railway Variables.')
    return channel


def panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title='👑🐒 MONKEY KINGDOM — RECRUITMENT',
        description=('**READY TO JOIN THE KINGDOM?**\n\n'
                     'We seek loyalty, respect, teamwork, and strong FiveM roleplay.\n\n'
                     '📝 Click **Apply to the Kingdom** below to submit a **private application**.\n'
                     '🔱 Kingdom leadership will review it and contact you with a decision.\n\n'
                     '**NO ALLIANCES. JUST FAMILY.**\n'
                     '*Come broke. Come unknown. Become known by everyone.*'),
        color=GOLD,
    )
    if BANNER_PATH.is_file():
        embed.set_image(url=f'attachment://{BANNER_NAME}')
    return embed


async def duplicate_exists(channel: discord.TextChannel, user_id: int) -> bool:
    # Bounded lookup. Large servers should replace with persistent database storage.
    async for msg in channel.history(limit=500):
        if msg.author.id != channel.guild.me.id or not msg.embeds:
            continue
        footer = msg.embeds[0].footer.text or ''
        if f'Applicant ID: {user_id} | Status: Pending' in footer:
            return True
    return False


class ApplicationForm(discord.ui.Modal, title='Monkey Kingdom Application'):
    city_name = discord.ui.TextInput(label='In-city character name', max_length=80)
    experience = discord.ui.TextInput(label='Experience in FiveM / RP', style=discord.TextStyle.paragraph, max_length=400)
    reason = discord.ui.TextInput(label='Why do you want to join?', style=discord.TextStyle.paragraph, max_length=700)
    activity = discord.ui.TextInput(label='How active are you weekly?', max_length=150)
    contribution = discord.ui.TextInput(label='What would you bring to the family?', style=discord.TextStyle.paragraph, max_length=600)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        if guild is None or guild.id != GUILD_ID or not isinstance(interaction.user, discord.Member):
            await interaction.followup.send('This application is only available in Monkey Kingdom.', ephemeral=True)
            return
        if any(role.id == KINGDOM_ROLE for role in interaction.user.roles):
            await interaction.followup.send('You already have Kingdom-Member access. Contact leadership if needed.', ephemeral=True)
            return
        async with submit_lock:
            channel = review_channel(guild)
            if channel is None:
                await interaction.followup.send('Recruitment review is not configured. Please contact leadership.', ephemeral=True)
                return
            me = guild.me
            perms = channel.permissions_for(me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links and perms.read_message_history):
                await interaction.followup.send('Recruitment review permissions need attention. Please contact leadership.', ephemeral=True)
                return
            try:
                if await duplicate_exists(channel, interaction.user.id):
                    await interaction.followup.send('You already have a pending application. Please wait for leadership.', ephemeral=True)
                    return
                embed = discord.Embed(title='📝 New Monkey Kingdom Application', color=GOLD)
                embed.add_field(name='Discord member', value=interaction.user.mention, inline=False)
                for title, answer in [
                    ('In-city name', self.city_name.value), ('FiveM experience', self.experience.value),
                    ('Why join?', self.reason.value), ('Weekly activity', self.activity.value),
                    ('What they bring', self.contribution.value),
                ]:
                    embed.add_field(name=title, value=discord.utils.escape_mentions(answer)[:1024], inline=False)
                embed.set_footer(text=f'Applicant ID: {interaction.user.id} | Status: Pending')
                await channel.send(embed=embed, view=DecisionView(), allowed_mentions=discord.AllowedMentions.none())
            except discord.Forbidden:
                await interaction.followup.send('The bot cannot post to the private review channel. Notify leadership.', ephemeral=True)
                return
            except Exception as exc:
                print(f'Recruitment application error: {type(exc).__name__}: {exc}')
                await interaction.followup.send('Could not submit the application. Please try again later.', ephemeral=True)
                return
        await interaction.followup.send('👑 Your application was submitted privately! Leadership will review it.', ephemeral=True)


submit_lock = asyncio.Lock()


class ApplyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='Apply to the Kingdom', emoji='👑', style=discord.ButtonStyle.primary, custom_id=PANEL_ID)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild_id != GUILD_ID:
            await interaction.response.send_message('This application is for Monkey Kingdom only.', ephemeral=True)
            return
        if interaction.guild and review_channel(interaction.guild) is None:
            await interaction.response.send_message('Applications are temporarily unavailable. Please contact leadership.', ephemeral=True)
            return
        await interaction.response.send_modal(ApplicationForm())


async def decide(interaction: discord.Interaction, accepted: bool):
    if interaction.guild is None or interaction.guild.id != GUILD_ID or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message('Wrong server.', ephemeral=True)
        return
    if not reviewer(interaction.user):
        await interaction.response.send_message('Only Lieutenants and higher leadership can review applications.', ephemeral=True)
        return
    if not isinstance(interaction.channel, discord.TextChannel) or interaction.channel != review_channel(interaction.guild):
        await interaction.response.send_message('This button must be used in recruitment-review.', ephemeral=True)
        return
    msg = interaction.message
    if not msg or not msg.embeds:
        await interaction.response.send_message('Application data was not found.', ephemeral=True)
        return
    footer = msg.embeds[0].footer.text or ''
    match = re.fullmatch(r'Applicant ID: (\d+) \| Status: (Pending|Accepted|Rejected)', footer)
    if not match or match.group(2) != 'Pending':
        await interaction.response.send_message('This application is already decided, or is not recognized.', ephemeral=True)
        return
    applicant_id = int(match.group(1))
    await interaction.response.defer(ephemeral=True, thinking=True)
    async with decision_lock:
        # Always refetch to prevent double decisions from concurrent leadership button clicks.
        msg = await interaction.channel.fetch_message(msg.id)
        current = msg.embeds[0].footer.text if msg.embeds else ''
        if current != f'Applicant ID: {applicant_id} | Status: Pending':
            await interaction.followup.send('This application has already been reviewed.', ephemeral=True)
            return
        try:
            applicant = interaction.guild.get_member(applicant_id) or await interaction.guild.fetch_member(applicant_id)
        except discord.NotFound:
            await interaction.followup.send('Applicant is no longer in the server. No decision was recorded.', ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.followup.send('Cannot fetch the applicant right now.', ephemeral=True)
            return
        if accepted:
            me = interaction.guild.me
            add_roles = [interaction.guild.get_role(KINGDOM_ROLE), interaction.guild.get_role(PROSPECT_ROLE)]
            add_roles = [r for r in add_roles if r is not None]
            if len(add_roles) != 2 or not me or not me.guild_permissions.manage_roles or any(r >= me.top_role for r in add_roles):
                await interaction.followup.send('Cannot assign Kingdom-Member/Prospect. Check bot Manage Roles and role position.', ephemeral=True)
                return
            try:
                await applicant.add_roles(*add_roles, reason=f'Application accepted by {interaction.user}')
                potential = interaction.guild.get_role(POTENTIAL_ROLE)
                if potential and potential in applicant.roles and potential < me.top_role:
                    await applicant.remove_roles(potential, reason='Accepted into Monkey Kingdom')
            except discord.HTTPException as exc:
                print(f'Failed to assign roles: {exc}')
                await interaction.followup.send('Role assignment failed. Decision not recorded; review bot permissions.', ephemeral=True)
                return
        embed = discord.Embed.from_dict(msg.embeds[0].to_dict())
        status = 'Accepted' if accepted else 'Rejected'
        embed.color = discord.Color.green() if accepted else discord.Color.red()
        embed.set_footer(text=f'Applicant ID: {applicant_id} | Status: {status}')
        embed.add_field(name='Leadership decision', value=f'{status} by {interaction.user.mention}', inline=False)
        try:
            await msg.edit(embed=embed, view=DecisionView(locked=True))
        except discord.HTTPException as exc:
            print(f'Failed to finalize decision message: {exc}')
            await interaction.followup.send('Role action may have succeeded, but the review message could not update. Contact an administrator.', ephemeral=True)
            return
    try:
        await applicant.send(f'👑 Your Monkey Kingdom application was **{status.lower()}**.' +
                             (' Welcome to the family! You have been assigned Prospect and Kingdom-Member.' if accepted else
                              ' Thank you for applying. You may contact leadership for more information.'))
    except discord.HTTPException:
        pass
    await interaction.followup.send(f'{status} application for {applicant.mention}.', ephemeral=True)
    print(f'Application {status}: member={applicant_id} reviewer={interaction.user.id}')


decision_lock = asyncio.Lock()


class DecisionView(discord.ui.View):
    def __init__(self, locked=False):
        super().__init__(timeout=None)
        if locked:
            for child in self.children:
                child.disabled = True

    @discord.ui.button(label='Accept', emoji='✅', style=discord.ButtonStyle.success, custom_id=ACCEPT_ID)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await decide(interaction, True)

    @discord.ui.button(label='Reject', emoji='❌', style=discord.ButtonStyle.danger, custom_id=REJECT_ID)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await decide(interaction, False)


async def configure(guild: discord.Guild):
    channel = guild.get_channel(RECRUITMENT_ID)
    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError(f'Recruitment channel {RECRUITMENT_ID} does not exist')
    review = await ensure_review_channel(guild)
    me = guild.me
    for ch in (channel, review):
        p = ch.permissions_for(me)
        if not (p.view_channel and p.send_messages and p.embed_links):
            raise RuntimeError(f'Bot needs View/Send/Embed in #{ch.name}')
    # Reuse panel rather than creating duplicates. Each message embeds its own image attachment.
    async for message in channel.history(limit=100):
        if message.author.id == me.id and message.embeds and message.embeds[0].title == panel_embed().title:
            if BANNER_PATH.is_file():
                with BANNER_PATH.open('rb') as f:
                    await message.edit(embed=panel_embed(), view=ApplyView(),
                                       attachments=[discord.File(f, filename=BANNER_NAME)])
            else:
                await message.edit(embed=panel_embed(), view=ApplyView())
            print(f'Updated existing branded recruitment panel: {message.jump_url}')
            return
    if BANNER_PATH.is_file():
        with BANNER_PATH.open('rb') as f:
            msg = await channel.send(embed=panel_embed(), view=ApplyView(),
                                     file=discord.File(f, filename=BANNER_NAME))
    else:
        msg = await channel.send(embed=panel_embed(), view=ApplyView())
    print(f'Posted branded recruitment application panel: {msg.jump_url}')
