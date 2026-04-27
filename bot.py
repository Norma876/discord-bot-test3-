import discord
from discord import app_commands
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import asyncio
import os
from collections import deque

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
SPOTIFY_CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
SPOTIFY_CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]

sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

intents = discord.Intents.default()
intents.message_content = True

class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("Slash-команды синхронизированы")

bot = Bot()

queues = {}
now_playing = {}

YTDL_OPTIONS = {
    "format": "bestaudio/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}


def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = deque()
    return queues[guild_id]


def spotify_track_to_query(url: str) -> list:
    queries = []
    if "track" in url:
        track = sp.track(url)
        artist = track["artists"][0]["name"]
        name = track["name"]
        queries.append(f"{artist} - {name}")
    elif "playlist" in url:
        results = sp.playlist_tracks(url)
        for item in results["items"][:25]:
            track = item["track"]
            if track:
                artist = track["artists"][0]["name"]
                name = track["name"]
                queries.append(f"{artist} - {name}")
    elif "album" in url:
        results = sp.album_tracks(url)
        for track in results["items"][:25]:
            artist = track["artists"][0]["name"]
            name = track["name"]
            queries.append(f"{artist} - {name}")
    return queries


async def get_audio_url(query: str):
    loop = asyncio.get_event_loop()

    def fetch():
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            if query.startswith("http"):
                info = ydl.extract_info(query, download=False)
            else:
                info = ydl.extract_info(f"ytsearch:{query}", download=False)
                if "entries" in info:
                    info = info["entries"][0]
            return {
                "url": info["url"],
                "title": info.get("title", query),
                "duration": info.get("duration", 0),
                "thumbnail": info.get("thumbnail", None),
            }

    try:
        return await loop.run_in_executor(None, fetch)
    except Exception as e:
        print(f"Ошибка при поиске: {e}")
        return None


async def play_next(interaction: discord.Interaction):
    guild_id = interaction.guild.id
    queue = get_queue(guild_id)

    if not queue:
        now_playing.pop(guild_id, None)
        return

    track_query = queue.popleft()
    track = await get_audio_url(track_query)

    if not track:
        await interaction.channel.send(f"❌ Не удалось найти: `{track_query}`")
        await play_next(interaction)
        return

    vc = interaction.guild.voice_client
    if not vc:
        return

    now_playing[guild_id] = track

    def after_play(error):
        if error:
            print(f"Ошибка воспроизведения: {error}")
        asyncio.run_coroutine_threadsafe(play_next(interaction), bot.loop)

    source = discord.FFmpegPCMAudio(track["url"], **FFMPEG_OPTIONS)
    vc.play(source, after=after_play)

    embed = discord.Embed(
        title="▶ Сейчас играет",
        description=f"**{track['title']}**",
        color=0x1DB954,
    )
    if track["thumbnail"]:
        embed.set_thumbnail(url=track["thumbnail"])
    mins, secs = divmod(track["duration"], 60)
    embed.add_field(name="Длительность", value=f"{mins}:{secs:02d}")
    embed.add_field(name="В очереди", value=str(len(queue)))
    await interaction.channel.send(embed=embed)


@bot.event
async def on_ready():
    print(f"✅ Бот запущен: {bot.user}")


@bot.tree.command(name="play", description="Играть музыку — Spotify/YouTube или название трека")
@app_commands.describe(query="Ссылка на Spotify/YouTube или название трека")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.user.voice:
        return await interaction.response.send_message("❌ Ты не в голосовом канале!", ephemeral=True)

    await interaction.response.defer()

    if not interaction.guild.voice_client:
        await interaction.user.voice.channel.connect()
    elif interaction.guild.voice_client.channel != interaction.user.voice.channel:
        await interaction.guild.voice_client.move_to(interaction.user.voice.channel)

    queue = get_queue(interaction.guild.id)

    if "spotify.com" in query:
        try:
            queries = spotify_track_to_query(query)
            if not queries:
                return await interaction.followup.send("❌ Не удалось получить треки со Spotify.")
            for q in queries:
                queue.append(q)
            await interaction.followup.send(f"➕ Добавлено в очередь: **{len(queries)}** трек(ов) со Spotify")
        except Exception as e:
            return await interaction.followup.send(f"❌ Ошибка Spotify: {e}")
    else:
        queue.append(query)
        await interaction.followup.send(f"➕ Добавлено в очередь: `{query}`")

    if not interaction.guild.voice_client.is_playing() and not interaction.guild.voice_client.is_paused():
        await play_next(interaction)


@bot.tree.command(name="skip", description="Пропустить текущий трек")
async def skip(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("⏭ Пропущено")
    else:
        await interaction.response.send_message("❌ Ничего не играет", ephemeral=True)


@bot.tree.command(name="pause", description="Поставить на паузу")
async def pause(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
        interaction.guild.voice_client.pause()
        await interaction.response.send_message("⏸ Пауза")
    else:
        await interaction.response.send_message("❌ Ничего не играет", ephemeral=True)


@bot.tree.command(name="resume", description="Продолжить воспроизведение")
async def resume(interaction: discord.Interaction):
    if interaction.guild.voice_client and interaction.guild.voice_client.is_paused():
        interaction.guild.voice_client.resume()
        await interaction.response.send_message("▶ Продолжаю")
    else:
        await interaction.response.send_message("❌ Нечего продолжать", ephemeral=True)


@bot.tree.command(name="stop", description="Остановить и очистить очередь")
async def stop(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        get_queue(interaction.guild.id).clear()
        now_playing.pop(interaction.guild.id, None)
        interaction.guild.voice_client.stop()
        await interaction.response.send_message("⏹ Остановлено, очередь очищена")


@bot.tree.command(name="queue", description="Показать очередь")
async def queue_cmd(interaction: discord.Interaction):
    queue = get_queue(interaction.guild.id)
    current = now_playing.get(interaction.guild.id)

    if not current and not queue:
        return await interaction.response.send_message("📭 Очередь пуста")

    embed = discord.Embed(title="📋 Очередь", color=0x1DB954)
    if current:
        embed.add_field(name="▶ Сейчас играет", value=current["title"], inline=False)
    if queue:
        items = list(queue)[:10]
        text = "\n".join(f"`{i+1}.` {t}" for i, t in enumerate(items))
        if len(queue) > 10:
            text += f"\n... и ещё {len(queue) - 10}"
        embed.add_field(name="Далее", value=text, inline=False)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="np", description="Что сейчас играет")
async def now_playing_cmd(interaction: discord.Interaction):
    track = now_playing.get(interaction.guild.id)
    if not track:
        return await interaction.response.send_message("❌ Ничего не играет", ephemeral=True)
    embed = discord.Embed(
        title="▶ Сейчас играет",
        description=f"**{track['title']}**",
        color=0x1DB954,
    )
    if track["thumbnail"]:
        embed.set_thumbnail(url=track["thumbnail"])
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="leave", description="Выйти из голосового канала")
async def leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        get_queue(interaction.guild.id).clear()
        now_playing.pop(interaction.guild.id, None)
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("👋 Вышел")
    else:
        await interaction.response.send_message("❌ Я не в канале", ephemeral=True)


bot.run(DISCORD_TOKEN)
