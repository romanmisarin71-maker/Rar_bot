import os
import random
import re
import asyncio
import psycopg2
from aiohttp import web
from ytmusicapi import YTMusic
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    MessageHandler,
    ChatMemberHandler,
    filters,
    ContextTypes
)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

ytm = YTMusic()

def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id NUMERIC,
            user_id NUMERIC,
            username TEXT,
            first_name TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS greeted (
            user_id NUMERIC PRIMARY KEY
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channel_music (
            file_id TEXT PRIMARY KEY,
            title TEXT
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()

def escape_markdown(text: str) -> str:
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)

def save_user(chat_id: int, user_id: int, username: str, first_name: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (chat_id, user_id, username, first_name)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (chat_id, user_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name
    """, (chat_id, user_id, username, first_name))
    conn.commit()
    cursor.close()
    conn.close()

def save_track_to_db(file_id: str, title: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO channel_music (file_id, title)
        VALUES (%s, %s)
        ON CONFLICT (file_id) DO NOTHING
    """, (file_id, title))
    conn.commit()
    cursor.close()
    conn.close()

def search_track_in_db(query: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT file_id, title FROM channel_music WHERE LOWER(title) LIKE LOWER(%s) LIMIT 1", (f"%{query}%",))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row

answers_rar = ["Привееет!", "Что такое?", "Звали?", "Я не сплю... Честно!!!"]
last_reply = None
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_reply
    if not update.message: return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name or "друг"

    save_user(chat_id, user_id, username, first_name)

    if not is_user_greeted(user_id):
        hi_text = f"Здравствуйте, {first_name}! Я Rar - ваш универсальный помощник, приятно познакомиться!"
        await update.message.reply_text(hi_text)
        mark_user_as_greeted(user_id)

    # ИСПРАВЛЕННАЯ ЛОГИКА КОМАНДЫ "ДОБАВЬ"
    if update.message.reply_to_message and update.message.reply_to_message.audio:
        text_clean = update.message.text.lower().strip() if update.message.text else ""
        if text_clean in ["добавь", "добавить"]:
            member_status = await context.bot.get_chat_member(chat_id, user_id)
            if member_status.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                audio = update.message.reply_to_message.audio
                performer = audio.performer.strip() if audio.performer else ""
                title = audio.title.strip() if audio.title else ""
                if performer and title:
                    track_title = f"{performer} - {title}"
                else:
                    track_title = audio.file_name or "Неизвестный трек"
                
                save_track_to_db(audio.file_id, track_title)
                # Обычный текст ответа, защищенный от падения из-за спецсимволов
                await update.message.reply_text(f"✅ Трек успешно добавлен в архив канала: {track_title}")
                return

    # ОБРАБОТКА ТЕКСТА И КОМАНД ПОИСКА
    if update.message.text:
        text = update.message.text
        clean = text.lower().strip()

        # ФУНКЦИЯ ОБХОДА БАЗЫ: Принудительный поиск в YouTube Music через Reply (На русском)
        if clean in ["поищи в ютм", "поищи в youtube music"] and update.message.reply_to_message:
            reply_msg = update.message.reply_to_message
            if reply_msg.from_user.id == context.bot.id and reply_msg.caption and "Запрос:" in reply_msg.caption:
                try:
                    orig_query = reply_msg.caption.split("Запрос:")[1].strip()
                except Exception:
                    orig_query = None

                if orig_query:
                    status_msg = await update.message.reply_text("⏳ Ищу этот трек напрямую в YouTube Music...")
                    try:
                        search_results = ytm.search(orig_query, filter="songs", limit=1)
                        if not search_results:
                            await status_msg.edit_text("❌ На YouTube Music этот трек найти не удалось.")
                            return
                        
                        # Исправлено: берем первый элемент списка [0]
                        track = search_results[0]
                        video_id = track['videoId']
                        title = track['title']
                        artists = ", ".join([a['name'] for a in track['artists']])
                        
                        # Прямой API-конвертер в MP3 файл
                        download_url = f"https://vexdh.com{video_id}"
                        
                        await status_msg.delete()
                        # Отправляем аудиофайл напрямую в плеер
                        await context.bot.send_audio(
                            chat_id=chat_id,
                            audio=download_url,
                            title=title,
                            performer=artists,
                            caption=f"🎵 Глобальный поиск: {artists} — {title}"
                        )
                    except Exception as e:
                        await status_msg.edit_text(f"⚠️ Ошибка при загрузке из YTM: {e}")
                    return
        if clean == "rar":
            if last_reply is not None:
                available = [a for a in answers_rar if a != last_reply]
            else:
                available = answers_rar
            reply_rar = random.choice(available)
            last_reply = reply_rar
            await update.message.reply_text(reply_rar)
            return

        elif clean == "калл":
            sender = await context.bot.get_chat_member(chat_id, user_id)
            if sender.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                await update.message.reply_text("Прости, но калл доступен только админам")
                return
            try:
                saved_members = get_chat_members(chat_id)
                members_tags = []
                for m_id, m_username, m_first_name in saved_members:
                    if int(m_id) == context.bot.id: continue
                    if m_username:
                        members_tags.append(f"@{escape_markdown(m_username)}")
                    else:
                        members_tags.append(f"[{escape_markdown(m_first_name)}](tg://user?id={int(m_id)})")

                if not members_tags:
                    await update.message.reply_text("В этой группе я пока никого не запомнила.")
                    return

                chunk_size = 5
                for i in range(0, len(members_tags), chunk_size):
                    chunk = members_tags[i:i + chunk_size]
                    await update.message.reply_text("*Минуточку внимания\\!\\!\\!*\n\n" + "\n".join(chunk), parse_mode="MarkdownV2")
            except Exception as e:
                await update.message.reply_text(f"Ошибка команды: {e}")
            return

        # ИСПРАВЛЕННЫЙ ПОИСК МУЗЫКИ: Имя Rar на английском, команда на русском
        elif clean.startswith("rar найди ") or clean.startswith("рар найди "):
            query = text[9:].strip()
            if not query:
                await update.message.reply_text("Напиши название песни, например: Rar найди duvet")
                return
            
            status_msg = await update.message.reply_text("🔍 Ищу трек в нашем архиве...")

            # ПРИОРИТЕТ 1: Поиск по базе твоего музыкального канала
            local_track = search_track_in_db(query)
            if local_track:
                file_id, track_title = local_track
                await status_msg.delete()
                caption_text = f"✨ Найдено в архиве канала: {track_title}\n\nЗапрос: {query}"
                await context.bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption_text)
                return

            # ПРИОРИТЕТ 2: Глобальный поиск в YouTube Music со скачиванием MP3
            try:
                await status_msg.edit_text("⏳ В архиве нет. Скачиваю аудиофайл из YouTube Music...")
                search_results = ytm.search(query, filter="songs", limit=1)
                
                if not search_results:
                    await status_msg.edit_text("❌ Ничего не нашлось ни в архиве, ни на YouTube Music.")
                    return
                
                # Исправлено: берем первый элемент списка [0]
                track = search_results[0]
                video_id = track['videoId']
                title = track['title']
                artists = ", ".join([a['name'] for a in track['artists']])
                
                # Запрос к конвертеру, отдающему прямой аудиофайл
                download_url = f"https://vexdh.com{video_id}"
                
                await status_msg.delete()
                # Отправляем полноценный .mp3 файл в плеер чата
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=download_url,
                    title=title,
                    performer=artists,
                    caption=f"🎵 Найдено в YouTube Music: {artists} — {title}"
                )
            except Exception as e:
                await status_msg.edit_text(f"⚠️ Ошибка при загрузке аудиофайла: {e}")

async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result: return
    user = result.new_chat_member.user
    chat_id = result.chat.id
    new_status = result.new_chat_member.status
    if user.is_bot: return

    if new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        user_name = user.first_name or "друг"
        save_user(chat_id, user.id, user.username, user_name)
        if not is_user_greeted(user.id):
            hi_text = f"Здравствуйте, {user_name}! Я Rar - ваш универсальный помощник, приятно познакомиться!"
            await context.bot.send_message(chat_id=chat_id, text=hi_text)
            mark_user_as_greeted(user.id)
    elif new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
        remove_user(chat_id, user.id)

def get_chat_members(chat_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, first_name FROM users WHERE chat_id = %s", (chat_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def is_user_greeted(user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM greeted WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row is not None

def mark_user_as_greeted(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO greeted (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()

async def handle_http(request):
    return web.Response(text="Бот Rar активен!")

async def start_webhook():
    app = web.Application()
    app.router.add_get("/", handle_http)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Фейковый веб-сервер успешно запущен на порту {port}")

async def on_startup(application: Application):
    asyncio.create_task(start_webhook())

def main():
    if not TOKEN or not DATABASE_URL:
        print("Ошибка: Переменные окуржения не заданы!")
        return
    init_db()
    
    app = Application.builder().token(TOKEN).post_init(on_startup).build()
    
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Запуск бота...")
    app.run_polling()

if __name__ == "__main__":
    main()
    
