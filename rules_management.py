"""Persistent Discord-hosted draft rules; no ephemeral Railway filesystem storage."""
import os
import discord
from discord import app_commands
from setup_commands import (GUILD_ID, RULES_ID, RULES_TITLE, authorized, existing,
                            post_embed, rules_embed)

STORAGE_ID = int(os.getenv('RULES_STORAGE_CHANNEL_ID', '1521765219880402975'))  # Monkey-Management
DRAFT_TITLE = '🔒 MK RULES DRAFT — BOT STORAGE (DO NOT DELETE)'


def storage_channel(guild):
    channel = guild.get_channel(STORAGE_ID)
    if not isinstance(channel, discord.TextChannel):
        raise RuntimeError('Rules storage channel not found; set RULES_STORAGE_CHANNEL_ID to a private leadership text channel.')
    bot = guild.me
    p = channel.permissions_for(bot)
    if not all((p.view_channel,p.send_messages,p.read_message_history,p.embed_links)):
        raise RuntimeError('Bot needs View/Send/Read History/Embed Links in the private rules storage channel.')
    return channel


async def draft_message(guild):
    return await existing(storage_channel(guild), DRAFT_TITLE)


async def draft_text(guild):
    message = await draft_message(guild)
    if message:
        return message.embeds[0].description or ''
    return rules_embed().description or ''


async def save_draft(guild, content):
    if not content.strip() or len(content)>4000:
        raise ValueError('Rules must contain 1–4000 characters.')
    channel = storage_channel(guild)
    message = await draft_message(guild)
    embed = discord.Embed(title=DRAFT_TITLE,description=content,color=0xF1C40F)
    embed.set_footer(text='Persistent rules draft. Keep this message pinned and private.')
    if message:
        await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    else:
        message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        try:
            await message.pin(reason='Persistent Monkey Kingdom rules draft')
        except (discord.Forbidden,discord.HTTPException):
            # Saving is still durable, but pin it manually to survive a busy history.
            pass
    return message


def formatted_rules(text):
    embed=discord.Embed(title=RULES_TITLE,description=text,color=0xF1C40F)
    embed.set_footer(text='Monkey Kingdom • No Alliances. Just Family.')
    return embed


class RulesModal(discord.ui.Modal):
    def __init__(self, action, initial):
        super().__init__(title=f'{action.title()} Monkey Kingdom Rules',timeout=600)
        self.action=action
        self.body=discord.ui.TextInput(label='Rules text (Markdown allowed)',style=discord.TextStyle.paragraph,
                                       min_length=1,max_length=4000,default=initial,required=True)
        self.add_item(self.body)

    async def on_submit(self, interaction):
        if not authorized(interaction):
            await interaction.response.send_message('Lieutenant+ only.',ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            await save_draft(interaction.guild,str(self.body))
            await interaction.followup.send('✅ Rules draft saved in private leadership storage. Use `/rules preview` and `/rules publish` to update the public rules post.',ephemeral=True)
        except (RuntimeError, ValueError, discord.HTTPException) as exc:
            await interaction.followup.send(f'❌ Could not save rules: {type(exc).__name__}: {exc}',ephemeral=True)


def install(tree):
    group=app_commands.Group(name='rules', description='Create, edit, preview, and publish Kingdom rules')

    @group.command(name='create',description='Create the private persistent rules draft')
    async def create(interaction: discord.Interaction):
        if not authorized(interaction):
            await interaction.response.send_message('Lieutenant+ only.',ephemeral=True); return
        try:
            if await draft_message(interaction.guild):
                await interaction.response.send_message('A rules draft already exists. Use `/rules edit` instead.',ephemeral=True); return
            await interaction.response.send_modal(RulesModal('create',rules_embed().description or ''))
        except (RuntimeError,discord.HTTPException) as exc:
            await interaction.response.send_message(f'❌ {exc}',ephemeral=True)

    @group.command(name='edit',description='Edit the saved rules draft through a Discord form')
    async def edit(interaction: discord.Interaction):
        if not authorized(interaction):
            await interaction.response.send_message('Lieutenant+ only.',ephemeral=True); return
        try:
            await interaction.response.send_modal(RulesModal('edit',await draft_text(interaction.guild)))
        except (RuntimeError,discord.HTTPException) as exc:
            await interaction.response.send_message(f'❌ {exc}',ephemeral=True)

    @group.command(name='preview',description='Privately preview the latest rules draft')
    async def preview(interaction: discord.Interaction):
        if not authorized(interaction):
            await interaction.response.send_message('Lieutenant+ only.',ephemeral=True); return
        await interaction.response.defer(ephemeral=True)
        try:
            msg=await draft_message(interaction.guild)
            text=msg.embeds[0].description if msg else rules_embed().description
            await interaction.followup.send(content='**Saved draft**' if msg else '**Default rules (no custom draft yet)**',
                                            embed=formatted_rules(text),ephemeral=True,
                                            allowed_mentions=discord.AllowedMentions.none())
        except (RuntimeError,discord.HTTPException) as exc:
            await interaction.followup.send(f'❌ {exc}',ephemeral=True)

    @group.command(name='publish',description='Publish/update the existing Kingdom Rules post without duplicates')
    async def publish(interaction: discord.Interaction):
        if not authorized(interaction):
            await interaction.response.send_message('Lieutenant+ only.',ephemeral=True); return
        await interaction.response.defer(ephemeral=True,thinking=True)
        try:
            channel=interaction.guild.get_channel(RULES_ID)
            if not isinstance(channel, discord.TextChannel):
                raise RuntimeError('Kingdom Rules channel not found')
            # Require usable draft storage before publishing. Fail closed on unreadable history.
            text=await draft_text(interaction.guild)
            old=await existing(channel,RULES_TITLE)
            embed=formatted_rules(text)
            if old:
                if old.embeds[0].image and old.embeds[0].image.url:
                    embed.set_image(url=old.embeds[0].image.url)
                await old.edit(embed=embed,allowed_mentions=discord.AllowedMentions.none())
                await interaction.followup.send(f'✅ Updated existing rules post — {old.jump_url}',ephemeral=True)
            else:
                new=await post_embed(channel,embed)
                try:
                    await new.pin(reason='Permanent Kingdom Rules')
                except (discord.Forbidden,discord.HTTPException):
                    pass
                await interaction.followup.send(f'✅ Published rules — {new.jump_url}',ephemeral=True)
        except (RuntimeError,discord.HTTPException,discord.Forbidden) as exc:
            await interaction.followup.send(f'❌ Publish failed: {type(exc).__name__}: {exc}',ephemeral=True)

    @group.command(name='reset',description='Restore default rules to the saved draft (does not publish)')
    @app_commands.describe(confirm='Set true to replace your saved draft with the default rules')
    async def reset(interaction: discord.Interaction,confirm:bool=False):
        if not authorized(interaction):
            await interaction.response.send_message('Lieutenant+ only.',ephemeral=True);return
        if not confirm:
            await interaction.response.send_message('No changes made. Run `/rules reset confirm:true` to replace the draft. Existing published rules stay unchanged until `/rules publish`.',ephemeral=True);return
        await interaction.response.defer(ephemeral=True)
        try:
            await save_draft(interaction.guild,rules_embed().description or '')
            await interaction.followup.send('✅ Default rules saved as your draft. Run `/rules publish` when ready.',ephemeral=True)
        except (RuntimeError,discord.HTTPException) as exc:
            await interaction.followup.send(f'❌ {exc}',ephemeral=True)

    tree.add_command(group,guild=discord.Object(id=GUILD_ID))
