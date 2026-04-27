import discord
from discord.ext import commands
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
bot = commands.Bot(command_prefix="!", intents=intents)

queues = {}  # guild_id -> deque of tracks
now_playing = {}  # guild_id -> track info

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


def spotify_track_to_query(url: str) -> list[str]:
    """Получает поисковые запросы из ссылки Spotify."""
    queries = []

    if "track" in url:
        track = sp.track(url)
        artist = track["artists"][0]["name"]
        name = track["name"]
        queries.append(f"{artist} - {name}")

    elif "playlist" in url:
        results = sp.playlist_tracks(url)
        for item in results["items"][:25]:  # максимум 25 треков
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


async def get_audio_url(query: str) -> dict | None:
    """Ищет аудио на YouTube по запросу."""
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


async def play_next(ctx):
    """Играет следующий трек из очереди."""
    guild_id = ctx.guild.id
    queue = get_queue(guild_id)

    if not queue:
        now_playing.pop(guild_id, None)
        return

    track_query = queue.popleft()
    track = await get_audio_url(track_query)

    if not track:
        await ctx.send(f"❌ Не удалось найти: `{track_query}`")
        await play_next(ctx)
        return

    vc = ctx.voice_client
    if not vc:
        return

    now_playing[guild_id] = track

    def after_play(error):
        if error:
            print(f"Ошибка воспроизведения: {error}")
        asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)

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
    await ctx.send(embed=embed)


# ========================
# КОМАНДЫ
# ========================

@bot.event
async def on_ready():
    print(f"✅ Бот запущен: {bot.user}")


@bot.command(name="play", aliases=["p"])
async def play(ctx, *, query: str):
    """!play <ссылка Spotify/YouTube или название трека>"""

    # Подключение к голосовому каналу
    if not ctx.author.voice:
        return await ctx.send("❌ Ты не в голосовом канале!")

    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()
    elif ctx.voice_client.channel != ctx.author.voice.channel:
        await ctx.voice_client.move_to(ctx.author.voice.channel)

    queue = get_queue(ctx.guild.id)
    msg = await ctx.send("🔍 Ищу...")

    # Spotify ссылка
    if "spotify.com" in query:
        try:
            queries = spotify_track_to_query(query)
            if not queries:
                return await msg.edit(content="❌ Не удалось получить треки со Spotify.")

            for q in queries:
                queue.append(q)

            await msg.edit(content=f"➕ Добавлено в очередь: **{len(queries)}** трек(ов) со Spotify")
        except Exception as e:
            return await msg.edit(content=f"❌ Ошибка Spotify: {e}")
    else:
        # YouTube или поиск по названию
        queue.append(query)
        await msg.edit(content=f"➕ Добавлено в очередь: `{query}`")

    # Начинаем воспроизведение если ничего не играет
    if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
        await play_next(ctx)


@bot.command(name="skip", aliases=["s"])
async def skip(ctx):
    """!skip — пропустить текущий трек"""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭ Пропущено")
    else:
        await ctx.send("❌ Ничего не играет")


@bot.command(name="pause")
async def pause(ctx):
    """!pause — пауза"""
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸ Пауза")
    else:
        await ctx.send("❌ Ничего не играет")


@bot.command(name="resume")
async def resume(ctx):
    """!resume — продолжить"""
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶ Продолжаю")
    else:
        await ctx.send("❌ Нечего продолжать")


@bot.command(name="stop")
async def stop(ctx):
    """!stop — остановить и очистить очередь"""
    if ctx.voice_client:
        get_queue(ctx.guild.id).clear()
        now_playing.pop(ctx.guild.id, None)
        ctx.voice_client.stop()
        await ctx.send("⏹ Остановлено, очередь очищена")


@bot.command(name="queue", aliases=["q"])
async def queue_cmd(ctx):
    """!queue — показать очередь"""
    queue = get_queue(ctx.guild.id)
    current = now_playing.get(ctx.guild.id)

    if not current and not queue:
        return await ctx.send("📭 Очередь пуста")

    embed = discord.Embed(title="📋 Очередь", color=0x1DB954)

    if current:
        embed.add_field(
            name="▶ Сейчас играет",
            value=current["title"],
            inline=False,
        )

    if queue:
        items = list(queue)[:10]
        text = "\n".join(f"`{i+1}.` {t}" for i, t in enumerate(items))
        if len(queue) > 10:
            text += f"\n... и ещё {len(queue) - 10}"
        embed.add_field(name="Далее", value=text, inline=False)

    await ctx.send(embed=embed)


@bot.command(name="leave", aliases=["dc"])
async def leave(ctx):
    """!leave — выйти из канала"""
    if ctx.voice_client:
        get_queue(ctx.guild.id).clear()
        now_playing.pop(ctx.guild.id, None)
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Вышел")
    else:
        await ctx.send("❌ Я не в канале")


@bot.command(name="np")
async def now_playing_cmd(ctx):
    """!np — что сейчас играет"""
    track = now_playing.get(ctx.guild.id)
    if not track:
        return await ctx.send("❌ Ничего не играет")

    embed = discord.Embed(
        title="▶ Сейчас играет",
        description=f"**{track['title']}**",
        color=0x1DB954,
    )
    if track["thumbnail"]:
        embed.set_thumbnail(url=track["thumbnail"])
    await ctx.send(embed=embed)


bot.run(DISCORD_TOKEN)
