import os
import random
import re
import psycopg2
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    MessageHandler,
    ChatMemberHandler,
    filters,
    ContextTypes
)

# Бот сам возьмет токен из переменной TELEGRAM_TOKEN, которую вы укажете в панели Render
TOKEN = os.environ.get("TELEGRAM_TOKEN")

# Бот сам возьмет ссылку на базу из переменной DATABASE_URL от Render
DATABASE_URL = os.environ.get("DATABASE_URL")

# Функция для подключения к PostgreSQL
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

# Создаем и настраиваем базу данных при запуске
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Таблица для участников чатов (для команды "калл")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id BIGINT,
            user_id BIGINT,
            username TEXT,
            first_name TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    
    # Таблица для вечного хранения поприветствованных пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS greeted (
            user_id BIGINT PRIMARY KEY
        )
    """)
    
    conn.commit()
    cursor.close()
    conn.close()

# Функция для экранирования спецсимволов Markdown
def escape_markdown(text: str) -> str:
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)

# Функция для сохранения пользователя в базу данных чата
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

# Функция для удаления пользователя из базы данных чата
def remove_user(chat_id: int, user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    conn.commit()
    cursor.close()
    conn.close()

# Функция для получения участников конкретного чата
def get_chat_members(chat_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, first_name FROM users WHERE chat_id = %s", (chat_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

# Проверка: приветствовал ли бот пользователя раньше
def is_user_greeted(user_id: int) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM greeted WHERE user_id = %s", (user_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row is not None

# Отметка в БД, что пользователь успешно поприветствован
def mark_user_as_greeted(user_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO greeted (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    conn.commit()
    cursor.close()
    conn.close()


answers_rar = [
    "Привееет!",
    "Что такое?",
    "Звали?",
    "Я не сплю... Честно!!!"
]

last_reply = None

# Обработчик изменения статусов участников в группе
async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result:
        return

    user = result.new_chat_member.user
    chat_id = result.chat.id
    new_status = result.new_chat_member.status

    if user.is_bot:
        return

    # Если пользователь вступил в чат
    if new_status == ChatMemberStatus.MEMBER:
        user_name = user.first_name or "друг"
        save_user(chat_id, user.id, user.username, user_name)
        
        if not is_user_greeted(user.id):
            hi_text = f"Здравствуйте, {user_name}! Я Rar - ваш универсальный помощник, приятно познакомиться!"
            await context.bot.send_message(chat_id=chat_id, text=hi_text)
            mark_user_as_greeted(user.id)
    
    # Если пользователь покинул чат
    elif new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
        remove_user(chat_id, user.id)

# Обработчик обычных текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global last_reply

    if not update.message or not update.message.text:
        return

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
            await update.message.reply_text(
                "Прости, но калл доступен только админам, ты можешь попросить их созвать всех"
            )
            return

        try:
            saved_members = get_chat_members(chat_id)
            members_tags = []

            for m_id, m_username, m_first_name in saved_members:
                if m_id == context.bot.id:
                    continue
                
                try:
                    current_member = await context.bot.get_chat_member(chat_id, m_id)
                    if current_member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                        remove_user(chat_id, m_id)
                        continue
                except Exception:
                    remove_user(chat_id, m_id)
                    continue

                if m_username:
                    escaped_username = escape_markdown(m_username)
                    members_tags.append(f"@{escaped_username}")
                else:
                    escaped_name = escape_markdown(m_first_name)
                    members_tags.append(f"[{escaped_name}](tg://user?id={m_id})")

            if not members_tags:
                await update.message.reply_text("Почему-то я никого не нашла в базе данных")
                return

            chunk_size = 5
            for i in range(0, len(members_tags), chunk_size):
                chunk = members_tags[i:i + chunk_size]
                await update.message.reply_text(
                    "Минуточку внимания!!!\n" + "\n".join(chunk),
                    parse_mode="MarkdownV2"
                )

        except Exception as e:
            await update.message.reply_text(f"Ошибка при сборе участников: {e}")

def main():
    if not TOKEN:
        print("Ошибка: Переменная окружения TELEGRAM_TOKEN не задана!")
        return
    if not DATABASE_URL:
        print("Ошибка: Переменная окружения DATABASE_URL не задана!")
        return

    init_db()
    
    app = Application.builder().token(TOKEN).is_chat_member(True).build()
    
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Бот запущен.")
    app.run_polling()

if __name__ == "__main__":
    main()
    
