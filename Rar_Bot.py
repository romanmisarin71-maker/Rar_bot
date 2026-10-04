import os
import random
import re
import time
import asyncio
import traceback
import psycopg2
from collections import OrderedDict
from html import escape as html_escape
from urllib.parse import urlparse
from aiohttp import web
from telegram import (
    Update, InlineQueryResultCachedAudio, InlineQueryResultArticle,
    InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    InlineQueryHandler, filters, ContextTypes
)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GROUP_ANON_BOT_ID = 1087968824
SAVED_USERS_CACHE_LIMIT = 2000
RAR_LOGO_URL = "https://raw.githubusercontent.com/romanmisarin71-maker/Rar_bot/main/RarFaceBulka.png"

STATUS_CREATOR = "creator"
STATUS_ADMINISTRATOR = "administrator"
STATUS_MEMBER = "member"
STATUS_RESTRICTED = "restricted"
STATUS_LEFT = "left"
STATUS_KICKED = "kicked"
STATUS_BANNED = "banned"

ADMIN_STATUSES = {STATUS_ADMINISTRATOR, STATUS_CREATOR}
IN_CHAT_STATUSES = {STATUS_MEMBER, STATUS_RESTRICTED, STATUS_ADMINISTRATOR, STATUS_CREATOR}
LEAVE_STATUSES = {STATUS_LEFT, STATUS_KICKED, STATUS_BANNED}

# Антиповтор для инлайна
INLINE_SEEN_LIMIT = 100
INLINE_SEEN_TTL = 300  # 5 минут
inline_seen = {}  # {user_id: {file_id: timestamp}}


def get_db_connection():
    result = urlparse(DATABASE_URL)
    return psycopg2.connect(
        database=result.path[1:], user=result.username, password=result.password,
        host=result.hostname, port=result.port, sslmode='require'
    )


def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS channel_music (
        file_id TEXT PRIMARY KEY, title TEXT NOT NULL,
        added_by BIGINT, added_at TIMESTAMP DEFAULT NOW())""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS chat_settings (
        chat_id BIGINT PRIMARY KEY, greet_enabled BOOLEAN DEFAULT TRUE,
        farewell_enabled BOOLEAN DEFAULT TRUE,
        greet_text TEXT DEFAULT 'Добро пожаловать в %чат%, %имя%',
        farewell_text TEXT DEFAULT 'Пока пока, %имя%, буду скучать!')""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS chat_members (
        chat_id BIGINT PRIMARY KEY, user_ids BIGINT[] DEFAULT '{}')""")
    cursor.execute("""CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY, value TEXT)""")
    conn.commit(); cursor.close(); conn.close()


def save_track_to_db(file_id, title, added_by):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT title FROM channel_music WHERE file_id = %s OR LOWER(title) = LOWER(%s) LIMIT 1", (file_id, title))
    if cursor.fetchone():
        cursor.close(); conn.close(); return False
    cursor.execute("INSERT INTO channel_music (file_id, title, added_by) VALUES (%s, %s, %s) ON CONFLICT (file_id) DO NOTHING", (file_id, title, added_by))
    conn.commit(); cursor.close(); conn.close(); return True


def search_track_in_db(query):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT file_id, title FROM channel_music WHERE LOWER(title) LIKE LOWER(%s) LIMIT 1", (f"%{query.strip().lower()}%",))
    row = cursor.fetchone(); cursor.close(); conn.close(); return row


def search_tracks_in_db(query, limit=20):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT file_id, title FROM channel_music WHERE LOWER(title) LIKE LOWER(%s) LIMIT %s", (f"%{query.strip().lower()}%", limit))
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


def get_all_tracks_from_db():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT file_id, title FROM channel_music")
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


def delete_track_from_db(file_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT title FROM channel_music WHERE file_id = %s", (file_id,))
    row = cursor.fetchone()
    if row:
        cursor.execute("DELETE FROM channel_music WHERE file_id = %s", (file_id,))
        conn.commit(); cursor.close(); conn.close(); return row[0]
    cursor.close(); conn.close(); return None


def get_chat_settings(chat_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_settings (chat_id) VALUES (%s) ON CONFLICT (chat_id) DO NOTHING", (chat_id,))
    conn.commit()
    cursor.execute("SELECT greet_enabled, farewell_enabled, greet_text, farewell_text FROM chat_settings WHERE chat_id = %s", (chat_id,))
    row = cursor.fetchone(); cursor.close(); conn.close()
    return row if row else (True, True, 'Добро пожаловать в %чат%, %имя%', 'Пока пока, %имя%, буду скучать!')


def update_chat_setting(chat_id, field, value):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute(f"INSERT INTO chat_settings (chat_id, {field}) VALUES (%s, %s) ON CONFLICT (chat_id) DO UPDATE SET {field} = EXCLUDED.{field}", (chat_id, value))
    conn.commit(); cursor.close(); conn.close()


def save_user_to_chat(user_id, chat_id):
    if user_id == GROUP_ANON_BOT_ID: return
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("""INSERT INTO chat_members (chat_id, user_ids) VALUES (%s, ARRAY[%s::BIGINT])
        ON CONFLICT (chat_id) DO UPDATE SET user_ids = (
        SELECT ARRAY(SELECT DISTINCT unnest(chat_members.user_ids || EXCLUDED.user_ids)))""", (chat_id, user_id))
    conn.commit(); cursor.close(); conn.close()


def remove_user_from_chat(user_id, chat_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE chat_members SET user_ids = array_remove(user_ids, %s::BIGINT) WHERE chat_id = %s", (user_id, chat_id))
    cursor.execute("DELETE FROM chat_members WHERE chat_id = %s AND cardinality(user_ids) = 0", (chat_id,))
    conn.commit(); cursor.close(); conn.close()


def remove_users_from_chat_batch(user_ids, chat_id):
    if not user_ids: return
    conn = get_db_connection(); cursor = conn.cursor()
    for uid in user_ids:
        cursor.execute("UPDATE chat_members SET user_ids = array_remove(user_ids, %s::BIGINT) WHERE chat_id = %s", (int(uid), chat_id))
    cursor.execute("DELETE FROM chat_members WHERE chat_id = %s AND cardinality(user_ids) = 0", (chat_id,))
    conn.commit(); cursor.close(); conn.close()


def remove_chat_data(chat_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_members WHERE chat_id = %s", (chat_id,))
    cursor.execute("DELETE FROM chat_settings WHERE chat_id = %s", (chat_id,))
    conn.commit(); cursor.close(); conn.close()


def get_chat_members(chat_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT user_ids FROM chat_members WHERE chat_id = %s", (chat_id,))
    row = cursor.fetchone(); cursor.close(); conn.close()
    return [int(uid) for uid in row[0]] if row and row[0] else []


def get_system_setting(key):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT value FROM system_settings WHERE key = %s", (key,))
        row = cursor.fetchone(); cursor.close(); conn.close()
        return row[0] if row else None
    except Exception as e:
        print(f"[SYSTEM_SETTINGS] Error: {e}"); return None


def get_owner_id():
    v = get_system_setting("owner_id"); return int(v) if v else None

def get_storage_chat_id():
    v = get_system_setting("storage_chat_id"); return int(v) if v else None

def get_notify_chat_id():
    v = get_system_setting("notify_chat_id"); return int(v) if v else None


def substitute_vars(text, user_name, chat_title):
    text = text.replace("%имя%", user_name).replace("%user%", user_name)
    return text.replace("%чат%", chat_title).replace("%chat%", chat_title)


def clean_title(full_title):
    if " - " in full_title:
        return full_title.split(" - ", 1)[1].strip()
    return full_title.strip()


def pick_random_with_antirepeat(user_id, limit):
    now = time.time()
    seen = inline_seen.get(user_id, {})
    seen = {fid: ts for fid, ts in seen.items() if now - ts < INLINE_SEEN_TTL}
    if len(seen) > INLINE_SEEN_LIMIT:
        sorted_items = sorted(seen.items(), key=lambda x: x[1])
        seen = dict(sorted_items[-INLINE_SEEN_LIMIT:])

    all_tracks = get_all_tracks_from_db()
    if not all_tracks:
        inline_seen[user_id] = seen
        return []

    available = [t for t in all_tracks if t[0] not in seen]
    if len(available) < limit:
        seen = {}
        available = all_tracks

    random.shuffle(available)
    chosen = available[:limit]
    for fid, _ in chosen:
        seen[fid] = now
    inline_seen[user_id] = seen
    return chosen


async def log_to_owner(context, text):
    try:
        owner_id = get_owner_id()
        if owner_id:
            safe = text.replace("<", "&lt;").replace(">", "&gt;")
            if len(safe) > 3500: safe = safe[:3500] + "...[обрезано]"
            await context.bot.send_message(chat_id=owner_id, text=f"🔔 <b>LOG</b>\n<code>{safe}</code>", parse_mode="HTML")
    except Exception as e:
        print(f"[LOG ERROR] {e}")


saved_users_cache = OrderedDict()

def cache_user(user_id, chat_id):
    key = (user_id, chat_id)
    if key in saved_users_cache: saved_users_cache.move_to_end(key)
    saved_users_cache[key] = True
    if len(saved_users_cache) > SAVED_USERS_CACHE_LIMIT: saved_users_cache.popitem(last=False)

def uncache_user(user_id, chat_id):
    saved_users_cache.pop((user_id, chat_id), None)

def is_user_cached(user_id, chat_id):
    return (user_id, chat_id) in saved_users_cache


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

GREET_PREFIXES = ["рар измени приветствие","рар, измени приветствие","rar измени приветствие","rar, измени приветствие","рар изменить приветствие","рар, изменить приветствие","rar изменить приветствие","rar, изменить приветствие"]
FAREWELL_PREFIXES = ["рар измени прощание","рар, измени прощание","rar измени прощание","rar, измени прощание","рар изменить прощание","рар, изменить прощание","rar изменить прощание","rar, изменить прощание"]
DELETE_COMMANDS = ["рар удали","рар, удали","rar удали","rar, удали","рар удалить","рар, удалить","rar удалить","rar, удалить"]


async def start_command(update, context):
    if update.effective_chat.id >= 0:
        text = ("<b>✨ Привет! Я Rar – ваш универсальный помощник.</b>\n\n"
                "В основном я работаю в чатах: храню коллекцию музыки, помогаю админам собирать участников, "
                "могу поговорить и исполняю другие не мало важные функции.\n\n"
                "Чтобы узнать, на что я способна, напишите в чате: <code>Рар команды</code>")
        await update.message.reply_text(text, parse_mode="HTML")


async def inline_query_handler(update, context):
    query = update.inline_query.query.strip().lower()
    user_id = update.inline_query.from_user.id
    results = []

    try:
        # 1. Пустой запрос — 10 треков + кнопка
        if query == "":
            tracks = pick_random_with_antirepeat(user_id, limit=10)
            for i, (fid, title) in enumerate(tracks):
                results.append(InlineQueryResultCachedAudio(
                    id=f"empty_{i}_{fid[:20]}",
                    audio_file_id=fid,
                    caption=clean_title(title),
                ))
            results.append(InlineQueryResultArticle(
                id="shuffle_empty",
                title="🔄 Новый набор песен",
                description="Показать другие случайные треки",
                thumbnail_url=RAR_LOGO_URL,
                input_message_content=InputTextMessageContent("🔄 Новый набор"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Новый набор", switch_inline_query_current_chat="дай песню")
                ]])
            ))
            await update.inline_query.answer(results, cache_time=0)

        # 2. Дай песню / песня / музыка — 20 треков + кнопка
        elif query in ["дай песню", "песня", "музыка"]:
            tracks = pick_random_with_antirepeat(user_id, limit=20)
            for i, (fid, title) in enumerate(tracks):
                results.append(InlineQueryResultCachedAudio(
                    id=f"rand_{i}_{fid[:20]}",
                    audio_file_id=fid,
                    caption=clean_title(title),
                ))
            results.append(InlineQueryResultArticle(
                id="shuffle",
                title="🔄 Новый набор песен",
                description="Показать другие случайные треки",
                thumbnail_url=RAR_LOGO_URL,
                input_message_content=InputTextMessageContent("🔄 Новый набор"),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Новый набор", switch_inline_query_current_chat="дай песню")
                ]])
            ))
            await update.inline_query.answer(results, cache_time=0)

        # 3. Поиск по "найди X" / "трек X"
        elif query.startswith("найди ") or query.startswith("трек "):
            search = query[6:].strip()
            if not search:
                await update.inline_query.answer([], cache_time=0); return
            tracks = search_tracks_in_db(search, limit=20)
            for i, (fid, title) in enumerate(tracks):
                results.append(InlineQueryResultCachedAudio(
                    id=f"find_{i}_{fid[:20]}",
                    audio_file_id=fid,
                    caption=clean_title(title),
                ))
            await update.inline_query.answer(results, cache_time=0)

        # 4. Монетка
        elif query.startswith("монетка") or query.startswith("подкинь монетку"):
            if random.randint(1, 50) == 50:
                coin_text = "Эээ... монетка встала ребром..."
            else:
                coin_text = random.choice(answers_coin)
            results.append(InlineQueryResultArticle(
                id="coin",
                title="🎲 Бросить монетку",
                thumbnail_url=RAR_LOGO_URL,
                input_message_content=InputTextMessageContent(coin_text)
            ))
            await update.inline_query.answer(results, cache_time=0)

        # 5. Автопоиск
        else:
            tracks = search_tracks_in_db(query, limit=20)
            for i, (fid, title) in enumerate(tracks):
                results.append(InlineQueryResultCachedAudio(
                    id=f"auto_{i}_{fid[:20]}",
                    audio_file_id=fid,
                    caption=clean_title(title),
                ))
            await update.inline_query.answer(results, cache_time=0)

    except Exception as e:
        await log_to_owner(context, f"[INLINE FATAL] q='{query}'\n{e}\n{traceback.format_exc()[:1200]}")
        try:
            await update.inline_query.answer([], cache_time=0)
        except Exception:
            pass


async def handle_message(update, context):
    global rar_replies_history, does_replies_history, recent_tracks_history, ref_replies_history, hi_replies_history
    if not update.message or not update.effective_user: return
    if update.effective_user.is_bot: return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username

    if chat_id < 0 and not is_user_cached(user_id, chat_id):
        try:
            save_user_to_chat(user_id, chat_id); cache_user(user_id, chat_id)
        except Exception as e:
            await log_to_owner(context, f"[SAVE USER ERROR] user={user_id} chat={chat_id}\n{e}")

    incoming = ""
    if update.message.text: incoming = update.message.text.lower().strip()
    elif update.message.caption: incoming = update.message.caption.lower().strip()

    if incoming in ["добавь", "добавить"]:
        target = None
        if update.message.reply_to_message and update.message.reply_to_message.audio:
            target = update.message.reply_to_message.audio
        elif update.message.audio:
            target = update.message.audio
        if target:
            perf = target.performer.strip() if target.performer else ""
            title = target.title.strip() if target.title else ""
            track_title = f"{perf} - {title}" if perf and title else (target.file_name or "Неизвестный трек")
            if save_track_to_db(target.file_id, track_title, user_id):
                storage = get_storage_chat_id()
                if storage:
                    try:
                        cap = f"{track_title}\n\nДобавил: @{username}" if username else track_title
                        if len(cap) > 1020: cap = cap[:1017] + "..."
                        await context.bot.send_audio(chat_id=storage, audio=target.file_id, caption=cap)
                    except Exception as e: print(f"[STORAGE] {e}")
                try:
                    await context.bot.send_audio(chat_id=chat_id, audio=target.file_id,
                        caption=f"✨ Я занесла этот трек в коллекцию!\n\nИмя в базе: {track_title}")
                except Exception as e: print(f"[ADD REPLY] {e}")
            else:
                await update.message.reply_text(f"Этот трек уже бережно сохранен в моей коллекции под именем: {track_title}")
        else:
            await update.message.reply_text('Прикрепи аудио или ответь командой "добавь" на нужный трек')
        return

    if not update.message.text: return
    text = update.message.text
    clean = text.lower().strip()

    if clean in ["рар команды", "rar команды", "рар, команды", "rar, команды"]:
        await update.message.reply_text(
            "<b>Список доступных команд Rar:</b>\n\n"
            "<b>Музыкальная коллекция:</b>\n"
            "• <code>добавь</code> / <code>добавить</code> (ответом на аудио) – занести трек в коллекцию\n"
            "• <code>Рар дай песню</code> – отправить случайную песню\n"
            "• <code>Рар найди</code> [название] – найти сохранённый трек\n\n"
            "<b>Настройки чата (только для админов):</b>\n"
            "• <code>Рар вкл приветствие</code> / <code>Рар выкл приветствие</code>\n"
            "• <code>Рар вкл прощание</code> / <code>Рар выкл прощание</code>\n"
            "• <code>Рар измени приветствие</code> [текст]\n"
            "• <code>Рар измени прощание</code> [текст]\n\n"
            "<b>Переменные в текстах приветствия и прощания:</b>\n"
            "• <code>%имя%</code> или <code>%user%</code> – имя пользователя\n"
            "• <code>%чат%</code> или <code>%chat%</code> – название чата\n\n"
            "<b>Администрирование:</b>\n"
            "• <code>калл</code> – тег участников по 6 человек (только для админов)\n\n"
            "<b>Инлайн-режим (работает в любом чате):</b>\n"
            "• <code>@ChRarBot </code> (пустой запрос) – 10 случайных треков + кнопка «Новый набор»\n"
            "• <code>@ChRarBot дай песню</code> / <code>песня</code> / <code>музыка</code> – 20 случайных треков + кнопка «Новый набор»\n"
            "• <code>@ChRarBot найди</code> [название] / <code>трек</code> [название] – поиск по коллекции\n"
            "• <code>@ChRarBot</code> [любой текст] – автопоиск по названию\n"
            "• <code>@ChRarBot монетка</code> / <code>подкинь монетку</code> – бросок монетки\n\n"
            "<b>Развлечения:</b>\n"
            "• <code>Рар подкинь монетку</code> – сыграть в орла или решку\n"
            "• <code>Рар что делаешь</code> – узнать, чем занята Rar\n"
            "• <code>Rar</code> – проверка работы бота",
            parse_mode="HTML"
        )
        return

    toggle_map = {
        "рар вкл приветствие": ("greet_enabled", True, "Приветствие новичков включено!"),
        "рар, вкл приветствие": ("greet_enabled", True, "Приветствие новичков включено!"),
        "rar вкл приветствие": ("greet_enabled", True, "Приветствие новичков включено!"),
        "rar, вкл приветствие": ("greet_enabled", True, "Приветствие новичков включено!"),
        "рар выкл приветствие": ("greet_enabled", False, "Приветствие новичков выключено!"),
        "рар, выкл приветствие": ("greet_enabled", False, "Приветствие новичков выключено!"),
        "rar выкл приветствие": ("greet_enabled", False, "Приветствие новичков выключено!"),
        "rar, выкл приветствие": ("greet_enabled", False, "Приветствие новичков выключено!"),
        "рар вкл прощание": ("farewell_enabled", True, "Прощание включено!"),
        "рар, вкл прощание": ("farewell_enabled", True, "Прощание включено!"),
        "rar вкл прощание": ("farewell_enabled", True, "Прощание включено!"),
        "rar, вкл прощание": ("farewell_enabled", True, "Прощание включено!"),
        "рар выкл прощание": ("farewell_enabled", False, "Прощание выключено!"),
        "рар, выкл прощание": ("farewell_enabled", False, "Прощание выключено!"),
        "rar выкл прощание": ("farewell_enabled", False, "Прощание выключено!"),
        "rar, выкл прощание": ("farewell_enabled", False, "Прощание выключено!"),
    }
    if clean in toggle_map:
        if chat_id >= 0:
            await update.message.reply_text("Эта команда работает только в группах"); return
        try:
            sender = await context.bot.get_chat_member(chat_id, user_id)
            if sender.status not in ADMIN_STATUSES:
                await update.message.reply_text("Прости, но эта команда доступна только админам"); return
        except Exception:
            await update.message.reply_text("Не удалось проверить права. Попробуй позже"); return
        field, val, msg = toggle_map[clean]
        update_chat_setting(chat_id, field, val)
        await update.message.reply_text(msg); return

    matched_greet = next((p for p in GREET_PREFIXES if clean == p or clean.startswith(p + " ")), None)
    matched_fare = next((p for p in FAREWELL_PREFIXES if clean == p or clean.startswith(p + " ")), None)
    if matched_greet or matched_fare:
        if chat_id >= 0:
            await update.message.reply_text("Эта команда работает только в группах"); return
        try:
            sender = await context.bot.get_chat_member(chat_id, user_id)
            if sender.status not in ADMIN_STATUSES:
                await update.message.reply_text("Прости, но эта команда доступна только админам"); return
        except Exception:
            await update.message.reply_text("Не удалось проверить права. Попробуй позже"); return
        matched = matched_greet or matched_fare
        new_text = text.strip()[len(matched):].strip()
        if not new_text:
            await update.message.reply_text("Нужно написать текст после команды!"); return
        if matched_greet:
            update_chat_setting(chat_id, "greet_text", new_text)
            await update.message.reply_text(f"Текст приветствия обновлён:\n\n{new_text}")
        else:
            update_chat_setting(chat_id, "farewell_text", new_text)
            await update.message.reply_text(f"Текст прощания обновлён:\n\n{new_text}")
        return

    if clean in DELETE_COMMANDS:
        owner_id = get_owner_id()
        if owner_id is None or user_id != owner_id:
            await update.message.reply_text("Эта команда доступна только моему создателю!"); return
        if not update.message.reply_to_message or not update.message.reply_to_message.audio:
            await update.message.reply_text("Ответь этой командой на сообщение с треком, который хочешь удалить"); return
        fid = update.message.reply_to_message.audio.file_id
        deleted = delete_track_from_db(fid)
        if deleted:
            await update.message.reply_text(f"✅ Трек удалён: {deleted}")
        else:
            await update.message.reply_text("❌ Такого трека нет в моей коллекции")
        return

    if clean in ["rar", "рар"]:
        if chat_id not in rar_replies_history: rar_replies_history[chat_id] = []
        avail = [a for a in answers_rar if a not in rar_replies_history[chat_id]] or answers_rar
        r = random.choice(avail)
        rar_replies_history[chat_id].append(r)
        if len(rar_replies_history[chat_id]) > 2: rar_replies_history[chat_id].pop(0)
        await update.message.reply_text(r); return

    if clean in ["рар, подкинь монетку","rar, подкинь монетку","рар подкинь монетку","rar подкинь монетку","рар, кинь монетку","rar, кинь монетку","рар кинь монетку","rar кинь монетку","рар, монетка","rar, монетка","рар монетка","rar монетка"]:
        if random.randint(1, 50) == 50:
            await update.message.reply_text("Эээ... монетка встала ребром..."); return
        await update.message.reply_text(random.choice(answers_coin)); return

    if clean in ["rar, привет","rar привет","рар, привет","рар привет"]:
        if chat_id not in hi_replies_history: hi_replies_history[chat_id] = []
        avail = [a for a in answers_hi if a not in hi_replies_history[chat_id]] or answers_hi
        r = random.choice(avail)
        hi_replies_history[chat_id].append(r)
        if len(hi_replies_history[chat_id]) > 2: hi_replies_history[chat_id].pop(0)
        await update.message.reply_text(r); return

    if clean in ["we.all.love.rar", "we.all.love.rar."]:
        if chat_id not in love_replies_history: love_replies_history[chat_id] = []
        avail = [a for a in answers_love if a not in love_replies_history[chat_id]] or answers_love
        r = random.choice(avail)
        love_replies_history[chat_id].append(r)
        if len(love_replies_history[chat_id]) > 2: love_replies_history[chat_id].pop(0)
        await update.message.reply_text(r); return

    if clean in ["rar, дай отсылку","rar дай отсылку","rar, отсылка","rar отсылка","рар, дай отсылку","рар дай отсылку","рар, отсылка","рар отсылка"]:
        if chat_id not in ref_replies_history: ref_replies_history[chat_id] = []
        avail = [a for a in answers_ref if a not in ref_replies_history[chat_id]] or answers_ref
        r = random.choice(avail)
        ref_replies_history[chat_id].append(r)
        if len(ref_replies_history[chat_id]) > 15: ref_replies_history[chat_id].pop(0)
        await update.message.reply_text(r); return

    if clean in ["rar, что делаешь?","рар, что делаешь?","rar что делаешь?","рар что делаешь?","rar, что делаешь","рар, что делаешь","rar что делаешь","рар что делаешь"]:
        if chat_id not in does_replies_history: does_replies_history[chat_id] = []
        avail = [a for a in answers_does if a not in does_replies_history[chat_id]] or answers_does
        r = random.choice(avail)
        does_replies_history[chat_id].append(r)
        if len(does_replies_history[chat_id]) > 2: does_replies_history[chat_id].pop(0)
        await update.message.reply_text(r); return

    if clean in ["rar дай песню","рар дай песню","rar дай музыку","рар дай музыку","rar, дай песню","рар, дай песню","rar, дай музыку","рар, дай музыку"]:
        try:
            tracks = get_all_tracks_from_db()
            if not tracks:
                await update.message.reply_text("В моей коллекции пока нет ни одной сохраненной песни. Админы, добавьте музыку!"); return
            if chat_id not in recent_tracks_history or not isinstance(recent_tracks_history[chat_id], list):
                recent_tracks_history[chat_id] = []
            avail = [t for t in tracks if t not in recent_tracks_history[chat_id]] or tracks
            if not avail:
                recent_tracks_history[chat_id] = []; avail = tracks
            fid, ttitle = random.choice(avail)
            recent_tracks_history[chat_id].append(fid)
            lim = max(5, len(tracks) // 3)
            if len(recent_tracks_history[chat_id]) > lim: recent_tracks_history[chat_id].pop(0)
            try:
                await context.bot.send_audio(chat_id=chat_id, audio=fid, caption=f"✨ Вот ваша песня!\n\n{ttitle}")
            except Exception as e: print(f"[SEND AUDIO] {e}")
        except Exception as e:
            await update.message.reply_text(f"⚠️ Ошибка в блоке рандома музыки: {e}")
        return

    if clean == "калл":
        if chat_id >= 0:
            await update.message.reply_text("Эта команда доступна только в группах"); return
        try:
            sender = await context.bot.get_chat_member(chat_id, user_id)
            if sender.status not in ADMIN_STATUSES:
                await update.message.reply_text("Прости, но калл доступен только админам"); return
        except Exception as e:
            await log_to_owner(context, f"[КАЛЛ ADMIN CHECK ERROR] {e}"); return

        try:
            chat_info = await context.bot.get_chat(chat_id)
            await log_to_owner(context, f"[КАЛЛ PRE-CHECK OK] chat={chat_info.title}")
        except Exception as e:
            await update.message.reply_text("Не удалось получить данные о чате. Попробуй позже")
            await log_to_owner(context, f"[КАЛЛ PRE-CHECK ERROR] {e}"); return

        try:
            user_ids = get_chat_members(chat_id)
            await log_to_owner(context, f"[КАЛЛ START]\nchat_id={chat_id}\nuser_ids={user_ids}\nlen={len(user_ids) if user_ids else 0}")
            if not user_ids:
                await update.message.reply_text("В моей записной книжке пока пусто. Напишите любое слово!"); return

            valid = [STATUS_MEMBER, STATUS_ADMINISTRATOR, STATUS_CREATOR, STATUS_RESTRICTED]
            tags, to_remove, errors = [], [], []

            for m_id in user_ids:
                m_id_int = int(m_id)
                if m_id_int in (int(context.bot.id), GROUP_ANON_BOT_ID): continue
                try:
                    member = await context.bot.get_chat_member(chat_id, m_id_int)
                except Exception as e:
                    err = str(e).lower()
                    if "not found" in err or "participant not found" in err:
                        to_remove.append(m_id_int); continue
                    errors.append(f"id={m_id_int}: {str(e)[:80]}"); continue

                if member.status == STATUS_LEFT:
                    to_remove.append(m_id_int); continue
                if member.status in (STATUS_KICKED, STATUS_BANNED):
                    errors.append(f"id={m_id_int}: skipped ({member.status})"); continue
                if member.status not in valid:
                    errors.append(f"id={m_id_int}: status={member.status}"); continue

                uname = member.user.username
                fname = member.user.first_name or "друг"
                display = f"@{uname}" if uname else fname
                tags.append(f'<a href="tg://user?id={m_id_int}">{html_escape(display)}</a>')

            if to_remove:
                try:
                    remove_users_from_chat_batch(to_remove, chat_id)
                    for uid in to_remove: uncache_user(uid, chat_id)
                except Exception as e:
                    await log_to_owner(context, f"[КАЛЛ BATCH REMOVE ERROR] {e}")

            await log_to_owner(context, f"[КАЛЛ RESULT]\ntags={len(tags)}\nleft={len(to_remove)}\nerrors={errors}")

            if not tags:
                await update.message.reply_text("В моей книжке нет активных участников для тега!"); return

            for i in range(0, len(tags), 6):
                chunk = tags[i:i+6]
                try:
                    await update.message.reply_text("<b>Минуточку внимания!!!</b>\n\n" + "\n".join(chunk), parse_mode="HTML")
                except Exception as e:
                    await log_to_owner(context, f"[КАЛЛ SEND ERROR] {e}")
        except Exception as e:
            await log_to_owner(context, f"[КАЛЛ FATAL] {e}\n{traceback.format_exc()[:1200]}")
        return

    if clean.startswith("rar найди ") or clean.startswith("рар найди "):
        query = text[9:].strip()
        if not query:
            await update.message.reply_text("Напиши название песни, например: Rar найди duvet"); return
        status_msg = await update.message.reply_text("Ищу трек в своей коллекции...")
        local_track = search_track_in_db(query)
        if local_track:
            fid, ttitle = local_track
            await status_msg.delete()
            try:
                await context.bot.send_audio(chat_id=chat_id, audio=fid, caption=f"✨ Вот что нашла у себя в коллекции: {ttitle}\n\nЗапрос: {query}")
            except Exception as e: print(f"[FIND SEND] {e}")
            return
        else:
            await status_msg.edit_text("К сожалению, такой песни в моей коллекции пока нет")


async def handle_chat_member(update, context):
    try:
        result = update.chat_member
        if not result: return
        user = result.new_chat_member.user
        chat_id = result.chat.id
        new_status = result.new_chat_member.status
        old_status = result.old_chat_member.status
        await log_to_owner(context, f"[CHAT_MEMBER]\nchat={chat_id}\nuser={user.id} name={user.first_name}\nold={old_status}\nnew={new_status}")
        if user.is_bot or chat_id >= 0: return
        if user.id == GROUP_ANON_BOT_ID: return

        greet_enabled, farewell_enabled, greet_text, farewell_text = get_chat_settings(chat_id)
        try:
            chat = await context.bot.get_chat(chat_id)
            chat_title = chat.title or "этот чат"
        except Exception:
            chat_title = "этот чат"
        user_name = user.first_name or "друг"

        if old_status in LEAVE_STATUSES and new_status in IN_CHAT_STATUSES:
            try:
                save_user_to_chat(user.id, chat_id); cache_user(user.id, chat_id)
            except Exception as e:
                await log_to_owner(context, f"[SAVE USER ERROR] {e}")
            if greet_enabled:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=substitute_vars(greet_text, user_name, chat_title))
                except Exception as e:
                    await log_to_owner(context, f"[GREET ERROR] {e}")

        elif old_status in IN_CHAT_STATUSES and new_status in LEAVE_STATUSES:
            try:
                remove_user_from_chat(user.id, chat_id); uncache_user(user.id, chat_id)
            except Exception as e:
                await log_to_owner(context, f"[REMOVE USER ERROR] {e}")
            if farewell_enabled:
                try:
                    await context.bot.send_message(chat_id=chat_id, text=substitute_vars(farewell_text, user_name, chat_title))
                except Exception as e:
                    await log_to_owner(context, f"[FAREWELL ERROR] {e}")
    except Exception as e:
        await log_to_owner(context, f"[CHAT_MEMBER FATAL] {e}\n{traceback.format_exc()[:1200]}")


async def handle_my_chat_member(update, context):
    try:
        result = update.my_chat_member
        if not result: return
        chat = result.chat
        chat_id = chat.id
        new_status = result.new_chat_member.status
        old_status = result.old_chat_member.status
        await log_to_owner(context, f"[MY_CHAT_MEMBER]\nchat={chat_id}\nold={old_status}\nnew={new_status}")
        if chat_id >= 0: return

        if new_status in LEAVE_STATUSES:
            try:
                remove_chat_data(chat_id)
                await log_to_owner(context, f"[CLEANUP] Бот покинул чат {chat_id}, данные удалены")
            except Exception as e:
                await log_to_owner(context, f"[CLEANUP ERROR] chat={chat_id}\n{e}")
            return

        if old_status not in ADMIN_STATUSES and new_status in ADMIN_STATUSES:
            try:
                await context.bot.send_message(chat_id=chat_id, text="Спасибо, теперь могу работать✨")
            except Exception as e:
                await log_to_owner(context, f"[MY_CHAT_MEMBER ERROR] {e}")
            return

        if old_status in ADMIN_STATUSES and new_status not in ADMIN_STATUSES:
            try:
                await context.bot.send_message(chat_id=chat_id, text="Эй! Верните мне админа! Я же так сломаться могу!!!")
            except Exception as e:
                await log_to_owner(context, f"[MY_CHAT_MEMBER ERROR] {e}")
            return

        if old_status not in IN_CHAT_STATUSES and new_status in IN_CHAT_STATUSES:
            text = ("Здравствуйте! Я Rar – ваш универсальный помощник.\n\n"
                    "Для моей корректной работы в чате предоставьте мне права админа, спасибо!\n\n"
                    "Чтобы узнать, на что я способна, напишите в чате <code>Рар команды</code> после выдачи мне прав.")
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
            except Exception as e:
                await log_to_owner(context, f"[MY_CHAT_MEMBER ERROR] {e}")
    except Exception as e:
        await log_to_owner(context, f"[MY_CHAT_MEMBER FATAL] {e}\n{traceback.format_exc()[:1200]}")


async def daily_track_loop(app):
    await asyncio.sleep(60)
    while True:
        try:
            notify = get_notify_chat_id()
            if notify:
                tracks = get_all_tracks_from_db()
                if tracks:
                    fid, title = random.choice(tracks)
                    try:
                        await app.bot.send_audio(chat_id=notify, audio=fid, caption=f"🎵 Ежедневный трек:\n{title}")
                        print(f"[DAILY] Отправлено в {notify}: {title}")
                    except Exception as e: print(f"[DAILY SEND] {e}")
                else: print("[DAILY] Коллекция пуста")
            else: print("[DAILY] notify_chat_id не задан")
        except Exception as e: print(f"[DAILY] {e}")
        await asyncio.sleep(86400)


async def keep_database_alive():
    await asyncio.sleep(30)
    while True:
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("SELECT 1;"); cursor.fetchone()
            cursor.close(); conn.close()
            print("=== [PING] БД активна ===")
        except Exception as e: print(f"=== [PING ERROR] {e} ===")
        await asyncio.sleep(21600)


async def error_handler(update, context):
    err_text = str(context.error)
    tb = "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__))[:1500]
    print(f"[ERROR HANDLER] {err_text}\n{tb}")
    try:
        owner_id = get_owner_id()
        if owner_id:
            is_noise = ("bot was kicked from the group chat" in err_text
                        or "bot was blocked by the user" in err_text
                        or "chat not found" in err_text)
            prefix = "ℹ️ NOISE" if is_noise else "⚠️ ERROR"
            await context.bot.send_message(chat_id=owner_id,
                text=f"{prefix}\n<code>{err_text[:600]}</code>\n\n<pre>{tb[:1500]}</pre>", parse_mode="HTML")
    except Exception as e:
        print(f"[ERROR HANDLER SEND FAIL] {e}")


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


async def on_startup(application):
    asyncio.create_task(start_webhook())
    asyncio.create_task(keep_database_alive())
    asyncio.create_task(daily_track_loop(application))


def main():
    if not TOKEN or not DATABASE_URL:
        print("Ошибка: Переменные окружения не заданы!"); return
    init_db()
    app = Application.builder().token(TOKEN).post_init(on_startup).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(InlineQueryHandler(inline_query_handler))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    print("Запуск бота...")
    app.run_polling(allowed_updates=["message", "chat_member", "my_chat_member", "inline_query"])


if __name__ == "__main__":
    main()
