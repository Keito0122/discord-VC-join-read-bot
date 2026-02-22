import discord
from discord.ext import commands
import json
import os
import asyncio
from gtts import gTTS

TOKEN = "YOUR_BOT_TOKEN"

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

CONFIG_FILE = "config.json"

# =========================
# 設定関連
# =========================

def load_config():
    if not os.path.exists(CONFIG_FILE):
        return {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def get_guild_config(guild_id):
    config = load_config()
    guild_id = str(guild_id)

    if guild_id not in config:
        config[guild_id] = {}

    guild_data = config[guild_id]


    if "read_roles" not in guild_data:
        guild_data["read_roles"] = []

    if "admin_roles" not in guild_data:
        guild_data["admin_roles"] = []

    # 変更を保存
    config[guild_id] = guild_data
    save_config(config)

    return guild_data


def save_guild_config(guild_id, guild_data):
    config = load_config()
    config[str(guild_id)] = guild_data
    save_config(config)


def has_permission(member: discord.Member):
    guild_data = get_guild_config(member.guild.id)

    if not guild_data["admin_roles"]:
        return member.guild_permissions.administrator

    return any(role.id in guild_data["admin_roles"] for role in member.roles)


# =========================
# bot起動&コンソール
# =========================


@bot.event
async def on_ready():
    print("CONFIG PATH:", os.path.abspath(CONFIG_FILE))
    await bot.tree.sync()
    print(f"起動完了: {bot.user}")

# =========================
# VC参加者読み上げ
# =========================

@bot.event
async def on_voice_state_update(member, before, after):

    if member.bot:
        return

    if before.channel is None and after.channel is not None:

        guild_data = get_guild_config(member.guild.id)

        if not any(role.id in guild_data["read_roles"] for role in member.roles):
            return

        vc = member.guild.voice_client
        if not vc or not vc.is_connected():
            return

        text = f"{member.display_name} さんが参加しました"
        tts = gTTS(text=text, lang="ja")

        # 各サーバーごとにファイル分離
        filename = f"join_{member.guild.id}.mp3"
        tts.save(filename)

        # 再生待ち
        while vc.is_playing():
            await asyncio.sleep(0.5)

        # 再生後にファイルを削除
        def after_play(error):
            if os.path.exists(filename):
                os.remove(filename)

        vc.play(
            discord.FFmpegPCMAudio(filename),
            after=after_play
        )


# =========================
# /join
# =========================

class JoinView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.add_item(VCSelect())


@bot.tree.command(name="join", description="VCに参加")
async def join(interaction: discord.Interaction):

    if not has_permission(interaction.user):
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return

    await interaction.response.send_message(
        "参加するVCを選択してください。",
        view=JoinView(),
        ephemeral=True
    )

class VCSelect(discord.ui.ChannelSelect):
    def __init__(self):
        super().__init__(
            channel_types=[discord.ChannelType.voice],
            placeholder="参加するVCを選択",
            min_values=1,
            max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        # 選択されたチャンネルID取得
        selected = self.values[0]

        # メモ:discord.pyのバージョンによっては直接VoiceChannelが返る場合がある。
        if isinstance(selected, discord.VoiceChannel):
            channel = selected
        else:
            # AppCommandChannel対策
            channel = interaction.guild.get_channel(selected.id)

        if not isinstance(channel, discord.VoiceChannel):
            await interaction.followup.send(
                "ボイスチャンネルではありません。",
                ephemeral=True
            )
            return

        try:
            if interaction.guild.voice_client:
                await interaction.guild.voice_client.move_to(channel)
            else:
                await channel.connect(timeout=10)

            await interaction.followup.send(
                f"{channel.name} に参加しました。",
                ephemeral=True
            )

        except Exception as e:
            await interaction.followup.send(
                f"接続エラー: {e}",
                ephemeral=True
            )

# =========================
# /stop
# =========================

@bot.tree.command(name="stop", description="VCから退出")
async def stop(interaction: discord.Interaction):

    if not has_permission(interaction.user):
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return

    if interaction.guild.voice_client:
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("VCから退出しました。", ephemeral=True)
    else:
        await interaction.response.send_message("接続していません。", ephemeral=True)


# =========================
# /setting (UI)
# =========================

class CategorySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="読み上げロール設定", value="read"),
            discord.SelectOption(label="コマンド使用ロール設定", value="admin"),
        ]
        super().__init__(placeholder="設定カテゴリを選択", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="操作を選択してください",
            view=ActionView(self.values[0])
        )


class ActionSelect(discord.ui.Select):
    def __init__(self, category):
        self.category = category
        options = [
            discord.SelectOption(label="ロールを追加", value="add"),
            discord.SelectOption(label="ロールを削除", value="remove"),
        ]
        super().__init__(placeholder="操作を選択", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(
            content="ロールを選択してください",
            view=RoleSelectView(self.category, self.values[0])
        )


class RoleSelect(discord.ui.RoleSelect):
    def __init__(self, category, action):
        super().__init__(placeholder="ロールを選択", min_values=1, max_values=1)
        self.category = category
        self.action = action

    async def callback(self, interaction: discord.Interaction):

        guild_data = get_guild_config(interaction.guild.id)
        role = self.values[0]

        key = "read_roles" if self.category == "read" else "admin_roles"

        if self.action == "add":
            if role.id not in guild_data[key]:
                guild_data[key].append(role.id)

        elif self.action == "remove":
            if role.id in guild_data[key]:
                guild_data[key].remove(role.id)

        save_guild_config(interaction.guild.id, guild_data)

        await interaction.response.edit_message(
            content="設定を更新しました。",
            view=None
        )


class SettingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(CategorySelect())


class ActionView(discord.ui.View):
    def __init__(self, category):
        super().__init__(timeout=120)
        self.add_item(ActionSelect(category))


class RoleSelectView(discord.ui.View):
    def __init__(self, category, action):
        super().__init__(timeout=120)
        self.add_item(RoleSelect(category, action))


@bot.tree.command(name="setting", description="Bot設定")
async def setting(interaction: discord.Interaction):

    if not has_permission(interaction.user):
        await interaction.response.send_message("権限がありません。", ephemeral=True)
        return

    await interaction.response.send_message(
        "設定カテゴリを選択してください。",
        view=SettingView(),
        ephemeral=True
    )


bot.run(TOKEN)