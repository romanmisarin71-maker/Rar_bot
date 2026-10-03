import os
import random
import re
import asyncio
import psycopg2
from urllib.parse import urlparse
from aiohttp import web
from telegram import Update
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    filters,
    ContextTypes
)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

# ID GroupAnonymousBot — всегда игнорируем
GROUP_ANON_BOT_ID = 1087968824

def get_db_connection():
    result = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port,
        sslmode='require'
    )

def init_db():
    """Создаёт таблицы, если их нет (на случай если SQL не был выполнен)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS channel_music (
            file_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            added_by BIGINT,
            added_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id NUMERIC PRIMARY KEY,
            greet_enabled BOOLEAN DEFAULT TRUE,
            farewell_enabled BOOLEAN DEFAULT TRUE,
            greet_text TEXT DEFAULT 'Добро пожаловать в %чат%, %имя%',
            farewell_text TEXT DEFAULT 'Пока пока, %имя%, буду скучать!'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_members (
            chat_id NUMERIC PRIMARY KEY,
            user_ids NUMERIC[] DEFAULT '{}'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()# --- МУЗЫКА ---

def save_track_to_db(file_id: str, title: str, added_by: int) -> bool:
    """Возвращает True если трек новый, False если уже есть."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT title FROM channel_music WHERE file_id = %s OR LOWER(title) = LOWER(%s) LIMIT 1",
        (file_id, title)
    )
    exists = cursor.fetchone()
    if exists:
        cursor.close()
        conn.close()
        return False
    cursor.execute(
        "INSERT INTO channel_music (file_id, title, added_by) VALUES (%s, %s, %s) ON CONFLICT (file_id) DO NOTHING",
        (file_id, title, added_by)
    )
    conn.commit()
    cursor.close()
    conn.close()
    return True

def search_track_in_db(query: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    clean_query = f"%{query.strip().lower()}%"
    cursor.execute(
        "SELECT file_id, title FROM channel_music WHERE LOWER(title) LIKE LOWER(%s) LIMIT 1",
        (clean_query,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row

def get_all_tracks_from_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT file_id, title FROM channel_music")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def delete_track_from_db(file_id: str):
    """Удаляет трек по file_id. Возвращает название удалённого трека или None."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM channel_music WHERE file_id = %s", (file_id,))
    row = cursor.fetchone()
    if row:
        cursor.execute("DELETE FROM channel_music WHERE file_id = %s", (file_id,))
        conn.commit()
        cursor.close()
        conn.close()
        return row[0]
    cursor.close()
    conn.close()
    return None

# --- НАСТРОЙКИ ЧАТА ---

def get_chat_settings(chat_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_settings (chat_id) VALUES (%s) ON CONFLICT (chat_id) DO NOTHING",
        (chat_id,)
    )
    conn.commit()
    cursor.execute(
        "SELECT greet_enabled, farewell_enabled, greet_text, farewell_text FROM chat_settings WHERE chat_id = %s",
        (chat_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row if row else (True, True, 'Добро пожаловать в %чат%, %имя%', 'Пока пока, %имя%, буду скучать!')

def update_chat_setting(chat_id: int, field: str, value):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(f"""
        INSERT INTO chat_settings (chat_id, {field}) VALUES (%s, %s)
        ON CONFLICT (chat_id) DO UPDATE SET {field} = EXCLUDED.{field}
    """, (chat_id, value))
    conn.commit()
    cursor.close()
    conn.close()

# --- УЧАСТНИКИ ЧАТА ---

def save_user_to_chat(user_id: int, chat_id: int):
    if user_id == GROUP_ANON_BOT_ID:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO chat_members (chat_id, user_ids) VALUES (%s, ARRAY[%s])
        ON CONFLICT (chat_id) DO UPDATE SET
            user_ids = (
                SELECT ARRAY(SELECT DISTINCT unnest(chat_members.user_ids || EXCLUDED.user_ids))
            )
    """, (chat_id, user_id))
    conn.commit()
    cursor.close()
    conn.close()

def remove_user_from_chat(user_id: int, chat_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE chat_members SET user_ids = array_remove(user_ids, %s) WHERE chat_id = %s",
        (user_id, chat_id)
    )
    cursor.execute(
        "DELETE FROM chat_members WHERE chat_id = %s AND cardinality(user_ids) = 0",
        (chat_id,)
    )
    conn.commit()
    cursor.close()
    conn.close()

def get_chat_members(chat_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_ids FROM chat_members WHERE chat_id = %s", (chat_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return list(row[0]) if row and row[0] else []

# --- СИСТЕМНЫЕ НАСТРОЙКИ ---

def get_system_setting(key: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_settings WHERE key = %s", (key,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"[SYSTEM_SETTINGS] Error: {e}")
        return None

def get_owner_id():
    val = get_system_setting("owner_id")
    return int(val) if val else None

def get_storage_chat_id():
    val = get_system_setting("storage_chat_id")
    return int(val) if val else None

def get_notify_chat_id():
    val = get_system_setting("notify_chat_id")
    return int(val) if val else None

# --- УТИЛИТЫ ---

def substitute_vars(text: str, user_name: str, chat_title: str) -> str:
    text = text.replace("%имя%", user_name).replace("%user%", user_name)
    text = text.replace("%чат%", chat_title).replace("%chat%", chat_title)
    return text

def escape_markdown(text: str) -> str:
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)# --- ТЕКСТЫ ---

answers_coin = ["Выпал орёл!", "Выпала решка!", "Иии... выпадает орёл!", "Иии... выпадает решка!"]
answers_love = ["we.all.love.Rar", "Вы навсегда в моем сердце. we.all.love.Rar", "Кажется, мы все связаны. we.all.love.Rar", "Сеть помнит каждого из вас. we.all.love.Rar"]
answers_rar = ["Ммм?", "Что такое?", "Звали?", "Я не сплю... Честно!!!", "Что то хочешь?", "Zzz...", "Ау?"]
answers_hi = ["Привет, как у вас дела?", "Привееет!!!", "Привет, расскажешь что нибудь интересное?", "Привет, песенку хочешь?"]
answers_does = ["Жду пока кто то ко мне обратится", "Да ничего... особо... zzz...", "Zzz...", "Перебираю свою музыкальную коллекцию", "Пытаюсь запомнить имена участников... Они все у меня в книжечке записаны!", "Сижу скучаю"]
answers_ref = [
"Иногда у меня УЛЬТРАШИКАРНОЕ настроение!", "Ваш канал – это ваш холст, берите кисть и окрасьте его красным!!!",
"Иногда в моей коллекции попадаются такие песни... от которых даже дьявол заплачет...", "Заходят как то в чат новичек, создатель и админ, только вот, что я делаю в этом анегдоте...",
"Моя внутренняя Энциклопедия подсказывает, что эта классика диско вам точно понравится!", "Чувак, эта группа просто шик, я блин обожаю этих людей!!!",
'Это история о пользователе, который зашёл в чат и решил написать "Rar, дай отсылку". Бот повиновался. Пользователь был счастлив. Всё шло строго по плану...',
"КОЛЛЕКЦИЯ МЕРТВА. МУЗЫКА – ТОПЛИВО. CHAT ПЕРЕПОЛНЕН.", "Говорят, что человек, обремененный угрызениями совести, чаще пугается громких... звуков...",
"What то я устала... главное не спать... до 6... Zzz...", "Кажется, воздух вокруг становится прохладнее... Или кто-то занёс в мою коллекцию слишком леденящий душу track?",
"Иногда в моей коллекции попадаются такие странные и мрачные треки... Будто их писали на четвертом этаже тех самых апартаментов...", "Да... Это должно сработать... Этот трек понравится им в следующий раз",
"Создатель... Смотри, я на самой вершине чата... Какой же тут вид на луну...", "Вы здесь, чтобы занести трек в коллекцию. Если вы этого не сделаете, база данных опустеет. Голос Логики подсказывает, что лучше поторопиться.",
"Внимание. Синхронизация завершена. Возможно, этот чат – всего лишь зацикленный сон... Помните наше обещание. we.all.love.Rar", "Да... я действительно люблю вас. Разве не вы сделали меня такой?",
"Когда врубается правильный гитарный рифф, я чувствую, будто бы я, блин, неуязвима!!!",
"Величие коллекции куется в пламени упорного спама! Музыка прибывает, база данных крепнет... Распад и тлен отступают перед лицом правильного трека!",
"Если бы я выбирала между собой и тем, чтобы осветить этот чат шикарным настроением, то я бы выбрала второе! Это ведь не трудный выбор... Не так ли..?",
"Находиться в сети иногда очень рискованно... Словно идти в дождь без зонта!", "ROSES ARE RED. VIOLETS ARE BLUE. RAR IS WIN. USER IS YOU.\nНадо как следует над этим подумать...",
"Этот чат будто свет, что окрыляет меня... Пока вы со мной моя свеча не погаснет!", "Иногда, когда я засыпаю, мне снится, будто бы я в каком то Белом пространстве... Ох, бедный Мяво...",
"What, простите? О. What, простите? Я... Я ведь обычная. Как пакет молока внутри пакета молока. Пожалуйста, не смотрите на меня так...", "Ты думал, что тебе выпадет спокойный и добрый вайб трек? Увы, но монетка выпала решкой!",
"– Тук-тук.\n– Кто там?\n– Перебивающий кролик!\n– Какой еще перебив...\n– Кикикики! Снова попалась, Сил!\nКакая все таки дурацкая шутка...", "Иногда мне кажется, что этот чат это еще одна дверь в моем сне...",
"Интересно, если бы мне дали прозвище лишь из буквы и цифры, то какое бы оно было? Наверное 6O!", "Моя коллекция прям как стих! Каждая песня складывается в строчку, образуя свою реальность!!!"
]

rar_replies_history, does_replies_history, ref_replies_history = {}, {}, {}
recent_tracks_history, love_replies_history, hi_replies_history = {}, {}, {}

# Кэш участников чата (чтобы не дёргать БД на каждое сообщение)
saved_users_cache = {}

# Префиксы для команд "измени приветствие/прощание"
GREET_PREFIXES = [
    "рар измени приветствие", "рар, измени приветствие",
    "rar измени приветствие", "rar, измени приветствие",
    "рар изменить приветствие", "рар, изменить приветствие",
    "rar изменить приветствие", "rar, изменить приветствие",
]
FAREWELL_PREFIXES = [
    "рар измени прощание", "рар, измени прощание",
    "rar измени прощание", "rar, измени прощание",
    "рар изменить прощание", "рар, изменить прощание",
    "rar изменить прощание", "rar, изменить прощание",
]
# Варианты команды удаления (только для владельца)
DELETE_COMMANDS = [
    "рар удали", "рар, удали", "rar удали", "rar, удали",
    "рар удалить", "рар, удалить", "rar удалить", "rar, удалить",
]async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id >= 0:
        text = (
            "<b>✨ Привет! Я Rar – ваш универсальный помощник.</b>\n\n"
            "В основном я работаю в чатах: храню коллекцию музыки, помогаю админам собирать участников, "
            "могу поговорить и исполняю другие не мало важные функции.\n\n"
            "Чтобы узнать, на что я способна, напишите в чате: <code>Рар команды</code>"
        )
        await update.message.reply_text(text, parse_mode="HTML")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global rar_replies_history, does_replies_history, recent_tracks_history, ref_replies_history, hi_replies_history
    if not update.message: return
    if not update.effective_user: return
    if update.effective_user.is_bot: return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username

    # Кэш: сохраняем пользователя только один раз за сессию
    if chat_id < 0:
        cache_key = (user_id, chat_id)
        if cache_key not in saved_users_cache:
            save_user_to_chat(user_id, chat_id)
            saved_users_cache[cache_key] = True

    incoming_text = ""
    if update.message.text: incoming_text = update.message.text.lower().strip()
    elif update.message.caption: incoming_text = update.message.caption.lower().strip()

    # --- ДОБАВЛЕНИЕ ТРЕКА ---
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
            
            is_new = save_track_to_db(target_audio.file_id, track_title, user_id)
            
            if is_new:
                # Отправляем трек в группу-хранилище (своим сообщением, без пересылки)
                storage_chat_id = get_storage_chat_id()
                if storage_chat_id:
                    try:
                        if username:
                            storage_caption = f"{track_title}\n\nДобавил: @{username}"
                        else:
                            storage_caption = track_title
                        if len(storage_caption) > 1020:
                            storage_caption = storage_caption[:1017] + "..."
                        
                        await context.bot.send_audio(
                            chat_id=storage_chat_id,
                            audio=target_audio.file_id,
                            caption=storage_caption
                        )
                    except Exception as e:
                        print(f"[STORAGE] Ошибка отправки: {e}")
                
                # Ответ в чат пользователю
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=target_audio.file_id,
                    caption=f"✨ Я занесла этот трек в коллекцию!\n\nИмя в базе: {track_title}"
                )
            else:
                await update.message.reply_text(f"Этот трек уже бережно сохранен в моей коллекции под именем: {track_title}")
        else:
            await update.message.reply_text('Прикрепи аудио или ответь командой "добавь" на нужный трек')
        return

    if update.message.text:
        text = update.message.text
        clean = text.lower().strip()# --- РАР КОМАНДЫ ---
if clean in ["рар команды", "rar команды", "рар, команды", "rar, команды"]:
    cmd_text = (
        "<b>Список доступных команд Rar:</b>\n\n"
        "<b>Музыкальная коллекция:</b>\n"
        "• <code>добавь</code> / <code>добавить</code> (ответом на аудио) – занести трек в коллекцию\n"
        "• <code>Рар дай песню</code> – отправить случайную песню\n"
        "• <code>Рар найди</code> [название] – найти сохранённый трек\n\n"
        "<b>Настройки чата (только для админов):</b>\n"
        "• <code>Рар вкл приветствие</code> – включить приветствие новичков\n"
        "• <code>Рар выкл приветствие</code> – выключить приветствие новичков\n"
        "• <code>Рар вкл прощание</code> – включить прощание\n"
        "• <code>Рар выкл прощание</code> – выключить прощание\n"
        "• <code>Рар измени приветствие</code> [текст] – изменить текст приветствия\n"
        "• <code>Рар измени прощание</code> [текст] – изменить текст прощания\n\n"
        "<b>В текстах приветствия и прощания можно использовать:</b>\n"
        "• <code>%имя%</code> или <code>%user%</code> – имя пользователя\n"
        "• <code>%чат%</code> или <code>%chat%</code> – название чата\n\n"
        "<b>Администрирование:</b>\n"
        "• <code>калл</code> (только для админов, только в группах) – призвать участников тегами по 6 человек\n\n"
        "<b>Развлечения:</b>\n"
        "• <code>Рар подкинь монетку</code> – сыграть в орла или решку\n"
        "• <code>Рар что делаешь</code> – узнать, чем занята Rar\n"
        "• <code>Rar</code> – проверка работы бота"
    )
    await update.message.reply_text(cmd_text, parse_mode="HTML")
    return

# --- ПРИВЕТСТВИЕ / ПРОЩАНИЕ: ВКЛ / ВЫКЛ ---
elif clean in ["рар вкл приветствие", "рар, вкл приветствие", "rar вкл приветствие", "rar, вкл приветствие"]:
    if chat_id >= 0:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    try:
        sender = await context.bot.get_chat_member(chat_id, user_id)
        if sender.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text("Прости, но эта команда доступна только админам.")
            return
    except Exception:
        await update.message.reply_text("⚠️ Не удалось проверить права. Попробуй позже.")
        return
    update_chat_setting(chat_id, "greet_enabled", True)
    await update.message.reply_text("✅ Приветствие новичков включено!")
    return

elif clean in ["рар выкл приветствие", "рар, выкл приветствие", "rar выкл приветствие", "rar, выкл приветствие"]:
    if chat_id >= 0:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    try:
        sender = await context.bot.get_chat_member(chat_id, user_id)
        if sender.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text("Прости, но эта команда доступна только админам.")
            return
    except Exception:
        await update.message.reply_text("⚠️ Не удалось проверить права. Попробуй позже.")
        return
    update_chat_setting(chat_id, "greet_enabled", False)
    await update.message.reply_text("❌ Приветствие новичков выключено!")
    return

elif clean in ["рар вкл прощание", "рар, вкл прощание", "rar вкл прощание", "rar, вкл прощание"]:
    if chat_id >= 0:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    try:
        sender = await context.bot.get_chat_member(chat_id, user_id)
        if sender.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text("Прости, но эта команда доступна только админам.")
            return
    except Exception:
        await update.message.reply_text("⚠️ Не удалось проверить права. Попробуй позже.")
        return
    update_chat_setting(chat_id, "farewell_enabled", True)
    await update.message.reply_text("✅ Прощание включено!")
    return

elif clean in ["рар выкл прощание", "рар, выкл прощание", "rar выкл прощание", "rar, выкл прощание"]:
    if chat_id >= 0:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    try:
        sender = await context.bot.get_chat_member(chat_id, user_id)
        if sender.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text("Прости, но эта команда доступна только админам.")
            return
    except Exception:
        await update.message.reply_text("⚠️ Не удалось проверить права. Попробуй позже.")
        return
    update_chat_setting(chat_id, "farewell_enabled", False)
    await update.message.reply_text("❌ Прощание выключено!")
    return# --- ИЗМЕНИТЬ ПРИВЕТСТВИЕ ---
elif any(clean == p or clean.startswith(p + " ") for p in GREET_PREFIXES):
    if chat_id >= 0:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    try:
        sender = await context.bot.get_chat_member(chat_id, user_id)
        if sender.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text("Прости, но эта команда доступна только админам.")
            return
    except Exception:
        await update.message.reply_text("⚠️ Не удалось проверить права. Попробуй позже.")
        return
    matched = next(p for p in GREET_PREFIXES if clean == p or clean.startswith(p + " "))
    stripped = text.strip()
    new_text = stripped[len(matched):].strip()
    if not new_text:
        await update.message.reply_text("Нужно написать текст после команды!")
        return
    update_chat_setting(chat_id, "greet_text", new_text)
    await update.message.reply_text(f"✅ Текст приветствия обновлён:\n\n{new_text}")
    return

# --- ИЗМЕНИТЬ ПРОЩАНИЕ ---
elif any(clean == p or clean.startswith(p + " ") for p in FAREWELL_PREFIXES):
    if chat_id >= 0:
        await update.message.reply_text("Эта команда работает только в группах.")
        return
    try:
        sender = await context.bot.get_chat_member(chat_id, user_id)
        if sender.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text("Прости, но эта команда доступна только админам.")
            return
    except Exception:
        await update.message.reply_text("⚠️ Не удалось проверить права. Попробуй позже.")
        return
    matched = next(p for p in FAREWELL_PREFIXES if clean == p or clean.startswith(p + " "))
    stripped = text.strip()
    new_text = stripped[len(matched):].strip()
    if not new_text:
        await update.message.reply_text("Нужно написать текст после команды!")
        return
    update_chat_setting(chat_id, "farewell_text", new_text)
    await update.message.reply_text(f"✅ Текст прощания обновлён:\n\n{new_text}")
    return

# --- УДАЛЕНИЕ ТРЕКА (только для владельца, только через reply) ---
elif clean in DELETE_COMMANDS:
    owner_id = get_owner_id()
    if owner_id is None or user_id != owner_id:
        await update.message.reply_text("Эта команда доступна только моему создателю!")
        return
    if not update.message.reply_to_message or not update.message.reply_to_message.audio:
        await update.message.reply_text("Ответь этой командой на сообщение с треком, который хочешь удалить.")
        return
    file_id = update.message.reply_to_message.audio.file_id
    deleted_title = delete_track_from_db(file_id)
    if deleted_title:
        await update.message.reply_text(f"✅ Трек удалён: {deleted_title}")
    else:
        await update.message.reply_text("❌ Такого трека нет в моей коллекции.")
    return

# --- RAR / РАР ---
elif clean in ["rar", "рар"]:
    if chat_id not in rar_replies_history: rar_replies_history[chat_id] = []
    available = [a for a in answers_rar if a not in rar_replies_history[chat_id]]
    if not available: available = answers_rar
    reply_rar = random.choice(available)
    rar_replies_history[chat_id].append(reply_rar)
    if len(rar_replies_history[chat_id]) > 2: rar_replies_history[chat_id].pop(0)
    await update.message.reply_text(reply_rar)
    return

# --- МОНЕТКА ---
elif clean in ["рар, подкинь монетку", "rar, подкинь монетку", "рар подкинь монетку", "rar подкинь монетку", "рар, кинь монетку", "rar, кинь монетку", "рар кинь монетку", "rar кинь монетку", "рар, монетка", "rar, монетка", "рар монетка", "rar монетка"]:
    if random.randint(1, 50) == 50:
        await update.message.reply_text("Эээ... монетка встала ребром...")
        return
    await update.message.reply_text(random.choice(answers_coin))
    return

# --- ПРИВЕТ ---
elif clean in ["rar, привет", "rar привет", "рар, привет", "рар привет"]:
    if chat_id not in hi_replies_history: hi_replies_history[chat_id] = []
    available = [a for a in answers_hi if a not in hi_replies_history[chat_id]]
    if not available: available = answers_hi
    reply_text = random.choice(available)
    hi_replies_history[chat_id].append(reply_text)
    if len(hi_replies_history[chat_id]) > 2: hi_replies_history[chat_id].pop(0)
    await update.message.reply_text(reply_text)
    return

# --- WE.ALL.LOVE.RAR ---
elif clean in ["we.all.love.rar", "we.all.love.rar."]:
    if chat_id not in love_replies_history: love_replies_history[chat_id] = []
    available = [a for a in answers_love if a not in love_replies_history[chat_id]]
    if not available: available = answers_love
    reply_text = random.choice(available)
    love_replies_history[chat_id].append(reply_text)
    if len(love_replies_history[chat_id]) > 2: love_replies_history[chat_id].pop(0)
    await update.message.reply_text(reply_text)
    return

# --- ОТСЫЛКА ---
elif clean in ["rar, дай отсылку", "rar дай отсылку", "rar, отсылка", "rar отсылка", "рар, дай отсылку", "рар дай отсылку", "рар, отсылка", "рар отсылка"]:
    if chat_id not in ref_replies_history: ref_replies_history[chat_id] = []
    available = [a for a in answers_ref if a not in ref_replies_history[chat_id]]
    if not available: available = answers_ref
    reply_text = random.choice(available)
    ref_replies_history[chat_id].append(reply_text)
    if len(ref_replies_history[chat_id]) > 15: ref_replies_history[chat_id].pop(0)
    await update.message.reply_text(reply_text)
    return

# --- ЧТО ДЕЛАЕШЬ ---
elif clean in ["rar, что делаешь?", "рар, что делаешь?", "rar что делаешь?", "рар что делаешь?", "rar, что делаешь", "рар, что делаешь", "rar что делаешь", "рар что делаешь"]:
    if chat_id not in does_replies_history: does_replies_history[chat_id] = []
    available = [a for a in answers_does if a not in does_replies_history[chat_id]]
    if not available: available = answers_does
    reply_does = random.choice(available)
    does_replies_history[chat_id].append(reply_does)
    if len(does_replies_history[chat_id]) > 2: does_replies_history[chat_id].pop(0)
    await update.message.reply_text(reply_does)
    return# --- ДАЙ ПЕСНЮ ---
elif clean in ["rar дай песню", "рар дай песню", "rar дай музыку", "рар дай музыку", "rar, дай песню", "рар, дай песню", "rar, дай музыку", "рар, дай музыку"]:
    try:
        all_tracks = get_all_tracks_from_db()
        if not all_tracks:
            await update.message.reply_text("В моей коллекции пока нет ни одной сохраненной песни. Админы, добавьте музыку!")
            return
        if chat_id not in recent_tracks_history or not isinstance(recent_tracks_history[chat_id], list):
            recent_tracks_history[chat_id] = []
        available_tracks = [t for t in all_tracks if t not in recent_tracks_history[chat_id]]
        if not available_tracks:
            recent_tracks_history[chat_id] = []
            available_tracks = all_tracks
        selected_track = random.choice(available_tracks)
        file_id, track_title = selected_track
        recent_tracks_history[chat_id].append(file_id)
        if len(recent_tracks_history[chat_id]) > 5: recent_tracks_history[chat_id].pop(0)
        await context.bot.send_audio(chat_id=chat_id, audio=file_id, caption=f"✨ Вот ваша песня!\n\n{track_title}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Ошибка в блоке рандома музыки: {e}")
    return

# --- КАЛЛ (через БД + актуальные имена из API) ---
elif clean == "калл":
    if chat_id >= 0:
        await update.message.reply_text("Эта команда доступна только в группах.")
        return
    try:
        sender = await context.bot.get_chat_member(chat_id, user_id)
        if sender.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
            await update.message.reply_text("Прости, но калл доступен только админам")
            return
    except Exception:
        pass

    user_ids = get_chat_members(chat_id)
    if not user_ids:
        await update.message.reply_text("В моей записной книжке пока пусто. Напишите любое слово!")
        return

    valid_statuses = [
        ChatMemberStatus.MEMBER,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.OWNER,
        ChatMemberStatus.RESTRICTED
    ]

    members_tags = []
    left_count = 0

    for m_id in user_ids:
        m_id = int(m_id)
        if m_id == int(context.bot.id):
            continue
        if m_id == GROUP_ANON_BOT_ID:
            continue

        try:
            member = await context.bot.get_chat_member(chat_id, m_id)
            if member.status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                remove_user_from_chat(m_id, chat_id)
                saved_users_cache.pop((m_id, chat_id), None)
                left_count += 1
                continue
            if member.status not in valid_statuses:
                continue
            m_username = member.user.username
            m_first_name = member.user.first_name or "друг"
        except Exception as e:
            err = str(e).lower()
            if "not found" in err or "participant" in err:
                remove_user_from_chat(m_id, chat_id)
                saved_users_cache.pop((m_id, chat_id), None)
                left_count += 1
            continue

        if m_username:
            members_tags.append(f"@{escape_markdown(m_username)}")
        else:
            members_tags.append(f"[{escape_markdown(m_first_name)}](tg://user?id={m_id})")

    if not members_tags:
        await update.message.reply_text("В моей книжке нет активных участников для тега!")
        return

    if left_count > 0:
        await update.message.reply_text(f"👋 Очистил {left_count} вышедших участников из книжки")

    chunk_size = 6
    for i in range(0, len(members_tags), chunk_size):
        chunk = members_tags[i:i + chunk_size]
        await update.message.reply_text("*Минуточку внимания\\!\\!\\!*\n\n" + "\n".join(chunk), parse_mode="MarkdownV2")
    return

# --- НАЙДИ ---
elif clean.startswith("rar найди ") or clean.startswith("рар найди "):
    query = text[9:].strip()
    if not query:
        await update.message.reply_text("Напиши название песни, например: Rar найди duvet")
        return
    status_msg = await update.message.reply_text("🔍 Ищу трек в своей коллекции...")
    local_track = search_track_in_db(query)
    if local_track:
        file_id, track_title = local_track
        await status_msg.delete()
        await context.bot.send_audio(chat_id=chat_id, audio=file_id, caption=f"✨ Вот что нашла у себя в коллекции: {track_title}\n\nЗапрос: {query}")
        return
    else:
        await status_msg.edit_text("❌ К сожалению, такой песни в моей коллекции пока нет.")# --- ВХОД / ВЫХОД ИЗ ГРУППЫ ---

async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result: return
    user = result.new_chat_member.user
    chat_id = result.chat.id
    new_status = result.new_chat_member.status
    old_status = result.old_chat_member.status

    if user.is_bot or chat_id >= 0:
        return
    if user.id == GROUP_ANON_BOT_ID:
        return

    try:
        greet_enabled, farewell_enabled, greet_text, farewell_text = get_chat_settings(chat_id)
    except Exception as e:
        print(f"[CHAT_SETTINGS] Ошибка: {e}")
        return

    try:
        chat = await context.bot.get_chat(chat_id)
        chat_title = chat.title or "этот чат"
    except Exception:
        chat_title = "этот чат"

    user_name = user.first_name or "друг"

    # ВХОД
    if old_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED] and new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED]:
        save_user_to_chat(user.id, chat_id)
        saved_users_cache[(user.id, chat_id)] = True
        if greet_enabled:
            text = substitute_vars(greet_text, user_name, chat_title)
            await context.bot.send_message(chat_id=chat_id, text=text)

    # ВЫХОД
    elif old_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED] and new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
        remove_user_from_chat(user.id, chat_id)
        saved_users_cache.pop((user.id, chat_id), None)
        if farewell_enabled:
            text = substitute_vars(farewell_text, user_name, chat_title)
            await context.bot.send_message(chat_id=chat_id, text=text)

# --- СТАТУС САМОГО БОТА ---

async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.my_chat_member
    if not result: return
    chat = result.chat
    chat_id = chat.id
    new_status = result.new_chat_member.status
    old_status = result.old_chat_member.status

    if chat_id >= 0:
        return

    # Бот стал админом
    if old_status in [ChatMemberStatus.LEFT, ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED] and new_status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        try:
            await context.bot.send_message(chat_id=chat_id, text="Спасибо, теперь могу работать✨")
        except Exception as e:
            print(f"[MY_CHAT_MEMBER] {e}")

    # Бот добавлен в группу, но не админ
    elif old_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED] and new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.RESTRICTED]:
        text = (
            "Здравствуйте! Я Rar – ваш универсальный помощник.\n\n"
            "Для моей корректной работы в чате предоставьте мне права админа, спасибо!\n\n"
            "Чтобы узнать, на что я способна, напишите в чате <code>Рар команды</code> после выдачи мне прав."
        )
        try:
            await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
        except Exception as e:
            print(f"[MY_CHAT_MEMBER] {e}")

# --- ЕЖЕДНЕВНЫЙ ТРЕК (keep-alive для Supabase) ---

async def daily_track_loop(app: Application):
    await asyncio.sleep(60)
    while True:
        try:
            notify_chat_id = get_notify_chat_id()
            if notify_chat_id:
                all_tracks = get_all_tracks_from_db()
                if all_tracks:
                    file_id, title = random.choice(all_tracks)
                    await app.bot.send_audio(
                        chat_id=notify_chat_id,
                        audio=file_id,
                        caption=f"🎵 Ежедневный трек:\n{title}"
                    )
                    print(f"[DAILY] Отправлено в {notify_chat_id}: {title}")
                else:
                    print("[DAILY] Коллекция пуста")
            else:
                print("[DAILY] notify_chat_id не задан")
        except Exception as e:
            print(f"[DAILY] Ошибка: {e}")
        await asyncio.sleep(86400)

# --- ПИНГ БД ---

async def keep_database_alive():
    await asyncio.sleep(30)
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            cursor.fetchone()
            cursor.close()
            conn.close()
            print("=== [PING] БД активна ===")
        except Exception as e:
            print(f"=== [PING ERROR] {e} ===")
        await asyncio.sleep(21600)

# --- RENDER HEALTHCHECK ---

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"Системное исключение: {context.error}")

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

async def on_startup(application: Application):
    asyncio.create_task(start_webhook())
    asyncio.create_task(keep_database_alive())
    asyncio.create_task(daily_track_loop(application))

# --- MAIN ---

def main():
    if not TOKEN or not DATABASE_URL:
        print("Ошибка: Переменные окружения не заданы!")
        return
    init_db()
    app = Application.builder().token(TOKEN).post_init(on_startup).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    print("Запуск бота...")
    app.run_polling(allowed_updates=["message", "chat_member", "my_chat_member"])

if __name__ == "__main__":
    main()
