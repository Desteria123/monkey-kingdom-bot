"""Leadership-only branded Discord announcement command."""
from pathlib import Path
import os
import discord
from discord import app_commands

GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', '1515095050995105862'))
LEADERSHIP_IDS = {1554289393374732308, 1515163342606438530, 1515432899095298198,
                  1515437209308758216, 1515096134824099941}
BANNER = Path(__file__).resolve().parent / 'monkey_banner.png'

def install(tree: app_commands.CommandTree):
    @tree.command(name='announce', description='Post a branded Kingdom announcement', guild=discord.Object(id=GUILD_ID))
    @app_commands.describe(channel='Destination channel', headline='Announcement title', message='Announcement text')
    async def announce(interaction: discord.Interaction, channel: discord.TextChannel, headline: str, message: str):
        if interaction.guild_id != GUILD_ID or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message('Monkey Kingdom only.', ephemeral=True)
            return
        if not (interaction.user.guild_permissions.administrator or any(r.id in LEADERSHIP_IDS for r in interaction.user.roles)):
            await interaction.response.send_message('Lieutenant+ only.', ephemeral=True)
            return
        if not headline.strip() or not message.strip():
            await interaction.response.send_message('Provide a title and message.', ephemeral=True)
            return
        if len(headline) > 256 or len(message) > 3900:
            await interaction.response.send_message('Title or message too long.', ephemeral=True)
            return
        me = interaction.guild.me
        if not me:
            await interaction.response.send_message('Cannot resolve bot member.', ephemeral=True)
            return
        perms = channel.permissions_for(me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.response.send_message('Bot cannot send embeds to that channel.', ephemeral=True)
            return
        if not perms.mention_everyone:
            await interaction.response.send_message(
                'I need the **Mention @everyone, @here, and All Roles** permission in that channel to ping everyone.',
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = discord.Embed(title='👑 ' + headline, description=message, color=discord.Color.from_rgb(241,196,15))
        embed.set_footer(text=f'Monkey Kingdom • Posted by {interaction.user.display_name}')
        try:
            if BANNER.is_file() and perms.attach_files:
                embed.set_image(url='attachment://monkey_banner.png')
                with BANNER.open('rb') as f:
                    posted = await channel.send(content='@everyone', embed=embed, file=discord.File(f, filename='monkey_banner.png'),
                                                allowed_mentions=discord.AllowedMentions(everyone=True, users=False, roles=False))
            else:
                posted = await channel.send(content='@everyone', embed=embed,
                                            allowed_mentions=discord.AllowedMentions(everyone=True, users=False, roles=False))
        except discord.HTTPException as exc:
            await interaction.followup.send(f'Announcement failed: {type(exc).__name__}', ephemeral=True)
            return
        await interaction.followup.send(f'Announcement published: {posted.jump_url}', ephemeral=True)
