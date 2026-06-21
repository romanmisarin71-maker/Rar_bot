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
            chat_id BIGINT,
            user_id BIGINT,
            username TEXT,
            first_name TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS greeted (
            user_id BIGINT PRIMARY KEY
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

answers_rar = ["Привееет!", "Что такое?", "Звали?", "Я не сплю... Честно!!!"]
last_reply = None

async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result: return
    user = result.new_chat_member.user
    chat_id = result.chat.id
    new_status = result.new_chat_member.status
    if user.is_bot: return

    if new_status == ChatMemberStatus.MEMBER:
        user_name = user.first_name or "друг"
        save_user(chat_id, user.id, user.username, user_name)
        if not is_user_greeted(user.id):
            hi_text = f"Здравствуйте, {user_name}! Я Rar - ваш универсальный помощник, приятно познакомиться!"
            await context.bot.send_message(chat_id=chat_id, text=hi_text)
            mark_user_as_greeted(user.id)
    elif new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
        remove_user(chat_id, user.id)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_reply
    if not update.message or not update.message.text: return
    text = update.message.text
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name or "друг"

    save_user(chat_id, user_id, username, first_name)

    if not is_user_greeted(user_id):
        hi_text = f"Здравствуйте, {first_name}! Я Rar - ваш универсальный помощник, приятно познакомиться!"
        await update.message.reply_text(hi_text)
        mark_user_as_greeted(user_id)

    clean = text.lower().strip()

    if clean == "rar":
        if last_reply is not None:
            available = [a for a in answers_rar if a != last_reply]
        else:
            available = answers_rar
        reply_rar = random.choice(available)
        last_reply = reply_rar
        await update.message.reply_text(reply_rar)

    elif clean == "калл":
        chat_member = await context.bot.get_chat_member(chat_id, user_id)
        if chat_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text("Прости, но калл доступен только админам")
            return
        try:
            saved_members = get_chat_members(chat_id)
            members_tags = []
            for m_id, m_username, m_first_name in saved_members:
                if m_id == context.bot.id: continue
                try:
                    current_member = await context.bot.get_chat_member(chat_id, m_id)
                    if current_member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                        remove_user(chat_id, m_id)
                        continue
                except Exception:
                    remove_user(chat_id, m_id)
                    continue
                if m_username:
                    members_tags.append(f"@{escape_markdown(m_username)}")
                else:
                    members_tags.append(f"[{escape_markdown(m_first_name)}](tg://user?id={m_id})")

            if not members_tags:
                await update.message.reply_text("База данных пуста")
                return

            chunk_size = 5
            for i in range(0, len(members_tags), chunk_size):
                chunk = members_tags[i:i + chunk_size]
                await update.message.reply_text("Минуточку внимания!!!\n" + "\n".join(chunk), parse_mode="MarkdownV2")
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")

# Фейковый веб-сервер для обхода ограничений Render
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
    print(f"Фейковый веб-сервер запущен на порту {port}")

def main():
    if not TOKEN or not DATABASE_URL:
        print("Ошибка: Переменные окружения не заданы!")
        return
    init_db()
    
    # Исправленная универсальная инициализация без проблемного метода
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    loop = asyncio.get_event_loop()
    loop.create_task(start_webhook())
    
    print("Бот запущен на бесплатном тарифе.")
    app.run_polling()

if __name__ == "__main__":
    main()
