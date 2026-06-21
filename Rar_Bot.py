import os
import random
import re
import asyncio
import psycopg2
from aiohttp import web
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

# УМНАЯ ФУНКЦИЯ: Проверяет дубликаты перед записью трека
def save_track_to_db(file_id: str, title: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Проверяем, есть ли трек с таким же file_id или похожим названием
    cursor.execute("SELECT title FROM channel_music WHERE file_id = %s OR LOWER(title) = LOWER(%s) LIMIT 1", (file_id, title))
    exists = cursor.fetchone()
    
    if exists:
        cursor.close()
        conn.close()
        return False  # Трек уже есть, сохранять не нужно
        
    cursor.execute("INSERT INTO channel_music (file_id, title) VALUES (%s, %s) ON CONFLICT (file_id) DO NOTHING", (file_id, title))
    conn.commit()
    cursor.close()
    conn.close()
    return True  # Трек успешно добавлен

def search_track_in_db(query: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    clean_query = f"%{query.strip().lower()}%"
    cursor.execute("SELECT file_id, title FROM channel_music WHERE LOWER(title) LIKE LOWER(%s) LIMIT 1", (clean_query,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row

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

def remove_user(chat_id: int, user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    conn.commit()
    cursor.close()
    conn.close()

def get_chat_members(chat_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, first_name FROM users WHERE chat_id = %s", (chat_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

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

    # ИСПРАВЛЕННАЯ ЛОГИКА КОМАНДЫ "ДОБАВЬ" С ЗАЩИТОЙ ОТ ДУБЛИКАТОВ
    incoming_text = ""
    if update.message.text:
        incoming_text = update.message.text.lower().strip()
    elif update.message.caption:
        incoming_text = update.message.caption.lower().strip()

    if incoming_text in ["добавь", "добавить"]:
        target_audio = None
        if update.message.reply_to_message and update.message.reply_to_message.audio:
            target_audio = update.message.reply_to_message.audio
        elif update.message.audio:
            target_audio = update.message.audio

        if target_audio:
            performer = target_audio.performer.strip() if target_audio.performer else ""
            title = target_audio.title.strip() if target_audio.title else ""
            track_title = f"{performer} - {title}" if performer and title else (target_audio.file_name or "Неизвестный трек")
            
            # Пробуем сохранить в базу данных
            is_new = save_track_to_db(target_audio.file_id, track_title)
            
            if is_new:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=target_audio.file_id,
                    caption=f"✅ Rar успешно занесла этот трек в аудио-архив!\n\nИмя в базе: {track_title}"
                )
            else:
                await update.message.reply_text(f"⚠️ Этот трек уже бережно сохранен в нашем архиве под именем: {track_title}")
            return

    # ОБРАБОТКА ВСЕХ ОСТАЛЬНЫХ ТЕКСТОВЫХ КОМАНД
    if update.message.text:
        text = update.message.text
        clean = text.lower().strip()

        # Функция принудительного обхода базы через Reply
        if clean in ["поищи в ютм", "поищи в youtube music", "поищи везде"] and update.message.reply_to_message:
            reply_msg = update.message.reply_to_message
            if reply_msg.from_user.id == context.bot.id and reply_msg.caption and "Запрос:" in reply_msg.caption:
                try:
                    orig_query = reply_msg.caption.split("Запрос:").strip()
                except Exception:
                    orig_query = None

                if orig_query:
                    await update.message.reply_text(
                        f"🔍 Чтобы запустить глубокий поиск по всему Telegram, нажмите на ссылку:\n"
                        f"👉 @vkmusic_bot {orig_query}"
                    )
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

        # ИСПРАВЛЕННАЯ КОМАНДА КАЛЛ С ЖЕСТКИМ ИСКЛЮЧЕНИЕМ ВЫШЕДШИХ И КИКНУТЫХ УЧАСТНИКОВ
        elif clean == "калл":
            try:
                sender = await context.bot.get_chat_member(chat_id, user_id)
                if sender.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                    await update.message.reply_text("Прости, но калл доступен только админам")
                    return
            except Exception:
                pass

            try:
                saved_members = get_chat_members(chat_id)
                members_tags = []
                
                for row in saved_members:
                    m_id, m_username, m_first_name = row
                    if int(m_id) == context.bot.id: continue
                    
                    try:
                        current_status = await context.bot.get_chat_member(chat_id, int(m_id))
                        # Если участник исключен (kicked) или вышел (left), немедленно вырезаем из Postgres
                        if current_status.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED, "left", "kicked"]:
                            remove_user(chat_id, int(m_id))
                            continue
                    except Exception:
                        remove_user(chat_id, int(m_id))
                        continue
                        
                    if m_username:
                        members_tags.append(f"@{escape_markdown(m_username)}")
                    else:
                        members_tags.append(f"[{escape_markdown(m_first_name)}](tg://user?id={int(m_id)})")

                if not members_tags:
                    await update.message.reply_text("В этой группе я пока никого не запомнила. Напишите любое слово в чат!")
                    return

                chunk_size = 5
                for i in range(0, len(members_tags), chunk_size):
                    chunk = members_tags[i:i + chunk_size]
                    await update.message.reply_text("*Минуточку внимания\\!\\!\\!*\n\n" + "\n".join(chunk), parse_mode="MarkdownV2")
            except Exception as e:
                await update.message.reply_text(f"Ошибка команды калл: {e}")
            return

        # ПОЛНОСТЬЮ РАБОЧИЙ МУЗЫКАЛЬНЫЙ ПОИСК (Rar найди duvet)
        elif clean.startswith("rar найди ") or clean.startswith("рар найди "):
            query = text[9:].strip()
            if not query:
                await update.message.reply_text("Напиши название песни, например: Rar найди duvet")
                return
            
            status_msg = await update.message.reply_text("🔍 Ищу трек в нашем архиве...")

            local_track = search_track_in_db(query)
            if local_track:
                file_id, track_title = local_track
                await status_msg.delete()
                caption_text = f"✨ Найдено в архиве канала: {track_title}\n\nЗапрос: {query}"
                await context.bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption_text)
                return

            try:
                await status_msg.delete()
                await update.message.reply_text(
                    f"⏳ В нашем архиве трека нет.\n"
                    f"Чтобы мгновенно получить .mp3 файл, нажмите на ссылку ниже и введите название песни:\n\n"
                    f"👉 @vkmusic_bot {query}"
                )
            except Exception as e:
                await update.message.reply_text(f"⚠️ Ошибка при поиске: {e}")

async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result: return
    user = result.new_chat_member.user
    chat_id = result.chat.id
    new_status = result.new_chat_member.status
    if user.is_bot: return

    # Ловим вход и выход участников в реальном времени
    if new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        user_name = user.first_name or "друг"
        save_user(chat_id, user.id, user.username, user_name)
        if not is_user_greeted(user.id):
            hi_text = f"Здравствуйте, {user_name}! Я Rar - ваш универсальный помощник, приятно познакомиться!"
            await context.bot.send_message(chat_id=chat_id, text=hi_text)
            mark_user_as_greeted(user.id)
    elif new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
        remove_user(chat_id, user.id)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"Системный гаситель: {context.error}")

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
    
    # ИСПРАВЛЕНО: Добавлен легальный сбор апдейтов о составе группы (allowed_updates)
    app = Application.builder().token(TOKEN).post_init(on_startup).build()
    
    app.add_error_handler(error_handler)
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    
    print("Запуск бота...")
    # Разрешаем боту получать события об изменении участников
    app.run_polling(allowed_updates=["message", "chat_member"])

if __name__ == "__main__":
    main()
    
