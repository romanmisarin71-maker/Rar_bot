import os, random, re, time, asyncio, traceback, psycopg2
from collections import OrderedDict
from html import escape as html_escape
from urllib.parse import urlparse
from aiohttp import web
from telegram import (Update, InlineQueryResultCachedAudio, InlineQueryResultArticle,
    InputTextMessageContent, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions)
from telegram.ext import (Application, CommandHandler, MessageHandler, ChatMemberHandler,
    InlineQueryHandler, ChosenInlineResultHandler, CallbackQueryHandler, filters, ContextTypes)

TOKEN = os.environ.get("TELEGRAM_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")
GROUP_ANON_BOT_ID = 1087968824
SAVED_USERS_CACHE_LIMIT = 2000
RAR_LOGO_URL = "https://raw.githubusercontent.com/romanmisarin71-maker/Rar_bot/main/RarFaceBulka.png"
MODERATION_CHAT_ID = -1003907890387

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

RANK_NONE = 0; RANK_LOW = 1; RANK_MID = 2; RANK_SENIOR = 3
RANK_NAMES = {0: "не админ", 1: "мл. админ", 2: "ср. админ", 3: "ст. админ"}

roulette_games = {}
ROULETTE_TIMEOUT = 900

ITEM_NAMES = {
    "lupa": "Лупа", "pila": "Пила", "naruch": "Наручники", "sigara": "Сигарета",
    "tabletki": "Таблетки", "telefon": "Телефон", "b_pulya": "Б. Пуля",
    "nasadka": "Насадка", "adrenalin": "Адреналин", "zerkalo": "Зеркало",
}

ROUND2_POOL = ["lupa", "pila", "naruch", "sigara"]
ROUND3_POOL = ["tabletki", "telefon", "b_pulya", "nasadka", "adrenalin", "zerkalo"]

ROUND3_START_PHRASES = [
    "Последний раунд. Все всерьез.",
    "Удача больше не поможет.",
    "Последний раунд. Будь внимателен.",
    "Победитель будет только один.",
    "Хватит игр. Я покажу, кто здесь дилер.",
]

DUEL_START_PHRASES = ["Готов?", "На счет три.", "Не моргай."]
DUEL_FINAL_SOUNDS = ["БДЫЩ", "БАХ", "ПАФ"]
DUEL_PLAYER_WIN_PHRASES = ["Ты выиграл.", "Победа за тобой.", "Хорошая работа."]
DUEL_RAR_WIN_PHRASES = ["Победа за мной.", "Ты не успел.", "Слишком медленно."]
DUEL_ACTIONS = ["ПИУ", "ПАУ", "ПАФ"]
DUEL_COUNTERS = {"ПИУ": "ПАУ", "ПАУ": "ПИУ", "ПАФ": "ПАФ"}
duel_games = {}
DUEL_TIMEOUT = 120

_creator_cache = {}
CREATOR_CACHE_TTL = 3600

INLINE_SEEN_LIMIT = 100
INLINE_SEEN_TTL = 300
inline_seen = {}
INLINE_RESULT_CACHE_TTL = 300
inline_result_cache = {}

PLAYLIST_LIMIT = 20
RECENT_PLAYLISTS_LIMIT = 20
RECENT_PLAYLISTS_TTL = 300
recent_playlists = {}

_recent_greets = {}
GREET_DEDUP_TTL = 5


def get_db_connection():
    result = urlparse(DATABASE_URL)
    return psycopg2.connect(database=result.path[1:], user=result.username,
        password=result.password, host=result.hostname, port=result.port, sslmode='require')


def init_db():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS channel_music (file_id TEXT PRIMARY KEY, title TEXT NOT NULL, added_by BIGINT, added_at TIMESTAMP DEFAULT NOW())")
    cursor.execute("ALTER TABLE channel_music ADD COLUMN IF NOT EXISTS track_num SERIAL")
    cursor.execute("ALTER TABLE channel_music ADD COLUMN IF NOT EXISTS inline_broken BOOLEAN DEFAULT FALSE")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_music_track_num ON channel_music(track_num)")
    cursor.execute("CREATE TABLE IF NOT EXISTS track_stats (track_num INTEGER PRIMARY KEY REFERENCES channel_music(track_num) ON DELETE CASCADE, plays BIGINT DEFAULT 0, last_played TIMESTAMP DEFAULT NOW())")
    cursor.execute("CREATE TABLE IF NOT EXISTS chat_settings (chat_id BIGINT PRIMARY KEY, greet_enabled BOOLEAN DEFAULT TRUE, farewell_enabled BOOLEAN DEFAULT TRUE, greet_text TEXT DEFAULT 'Добро пожаловать в %чат%, %имя%', farewell_text TEXT DEFAULT 'Пока пока, %имя%, буду скучать!')")
    cursor.execute("ALTER TABLE chat_settings ADD COLUMN IF NOT EXISTS kall_enabled BOOLEAN DEFAULT TRUE")
    cursor.execute("CREATE TABLE IF NOT EXISTS chat_members (chat_id BIGINT PRIMARY KEY, user_ids BIGINT[] DEFAULT '{}')")
    cursor.execute("CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS usernames (user_id BIGINT PRIMARY KEY, username TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS chat_admins (chat_id BIGINT NOT NULL, user_id BIGINT NOT NULL, rank INTEGER NOT NULL, assigned_by BIGINT, assigned_at TIMESTAMP DEFAULT NOW(), PRIMARY KEY (chat_id, user_id))")
    cursor.execute("CREATE TABLE IF NOT EXISTS sleuths (id SERIAL PRIMARY KEY, chat_id BIGINT NOT NULL, target_id BIGINT NOT NULL, target_name TEXT, admin_id BIGINT NOT NULL, admin_name TEXT, required_count INTEGER NOT NULL, current_count INTEGER DEFAULT 0, duration_seconds INTEGER NOT NULL, action TEXT DEFAULT 'none', started_at TIMESTAMP DEFAULT NOW(), deadline TIMESTAMP NOT NULL)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sleuths_chat ON sleuths(chat_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sleuths_target ON sleuths(chat_id, target_id)")
    conn.commit(); cursor.close(); conn.close()


def save_username(user_id, username):
    if not username: return
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO usernames (user_id, username) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username", (user_id, username.lower()))
        conn.commit(); cursor.close(); conn.close()
    except Exception: pass


def find_user_by_username(username):
    if not username: return None
    username = username.lstrip("@").lower()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM usernames WHERE username = %s", (username,))
    row = cursor.fetchone(); cursor.close(); conn.close()
    return row[0] if row else None


async def resolve_user_from_arg(context, chat_id, target_arg):
    if not target_arg: return None
    target_arg = target_arg.strip()
    if target_arg.startswith("@"):
        uid = find_user_by_username(target_arg)
        if uid:
            try:
                m = await context.bot.get_chat_member(chat_id, uid); return m.user
            except Exception: pass
        try:
            m = await context.bot.get_chat_member(chat_id, target_arg); return m.user
        except Exception: return None
    try:
        uid = int(target_arg)
        m = await context.bot.get_chat_member(chat_id, uid); return m.user
    except Exception:
        return None


def get_admin_rank(chat_id, user_id):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT rank FROM chat_admins WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
        row = cursor.fetchone(); cursor.close(); conn.close()
        return row[0] if row else RANK_NONE
    except Exception: return RANK_NONE


def set_admin_rank(chat_id, user_id, rank, by_user):
    conn = get_db_connection(); cursor = conn.cursor()
    if rank <= 0:
        cursor.execute("DELETE FROM chat_admins WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    else:
        cursor.execute("INSERT INTO chat_admins (chat_id, user_id, rank, assigned_by, assigned_at) VALUES (%s, %s, %s, %s, NOW()) ON CONFLICT (chat_id, user_id) DO UPDATE SET rank = EXCLUDED.rank, assigned_by = EXCLUDED.assigned_by, assigned_at = NOW()", (chat_id, user_id, rank, by_user))
    conn.commit(); cursor.close(); conn.close()


def get_all_admins(chat_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT user_id, rank FROM chat_admins WHERE chat_id = %s ORDER BY rank DESC", (chat_id,))
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


def get_kall_enabled(chat_id):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT kall_enabled FROM chat_settings WHERE chat_id = %s", (chat_id,))
        row = cursor.fetchone(); cursor.close(); conn.close()
        if row is None: return True
        return row[0]
    except Exception: return True


def set_kall_enabled(chat_id, val):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_settings (chat_id, kall_enabled) VALUES (%s, %s) ON CONFLICT (chat_id) DO UPDATE SET kall_enabled = EXCLUDED.kall_enabled", (chat_id, val))
    conn.commit(); cursor.close(); conn.close()


def sleuth_create(chat_id, target_id, target_name, admin_id, admin_name, required_count, duration_seconds, action):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM sleuths WHERE chat_id = %s AND target_id = %s", (chat_id, target_id))
    deadline_ts = time.time() + duration_seconds
    cursor.execute("INSERT INTO sleuths (chat_id, target_id, target_name, admin_id, admin_name, required_count, current_count, duration_seconds, action, deadline) VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, TO_TIMESTAMP(%s))", (chat_id, target_id, target_name, admin_id, admin_name, required_count, duration_seconds, action, deadline_ts))
    conn.commit(); cursor.close(); conn.close()


def sleuth_get(chat_id, target_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, target_id, target_name, admin_id, admin_name, required_count, current_count, duration_seconds, action, deadline FROM sleuths WHERE chat_id = %s AND target_id = %s", (chat_id, target_id))
    row = cursor.fetchone(); cursor.close(); conn.close(); return row


def sleuth_get_all(chat_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, target_id, target_name, admin_id, admin_name, required_count, current_count, duration_seconds, action, deadline FROM sleuths WHERE chat_id = %s ORDER BY started_at DESC", (chat_id,))
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


def sleuth_delete(chat_id, target_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM sleuths WHERE chat_id = %s AND target_id = %s", (chat_id, target_id))
    conn.commit(); cursor.close(); conn.close()


def sleuth_delete_by_id(sid):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM sleuths WHERE id = %s", (sid,))
    conn.commit(); cursor.close(); conn.close()


def sleuth_get_expired():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT id, chat_id, target_id, target_name, admin_id, admin_name, required_count, current_count, duration_seconds, action FROM sleuths WHERE deadline <= NOW()")
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


async def get_chat_creator(chat_id, context):
    now = time.time()
    if chat_id in _creator_cache:
        cid, ts = _creator_cache[chat_id]
        if now - ts < CREATOR_CACHE_TTL: return cid
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        for a in admins:
            if a.status == STATUS_CREATOR:
                _creator_cache[chat_id] = (a.user.id, now); return a.user.id
    except Exception: pass
    return None


async def check_user_rank(update, context, chat_id, user_id, required_rank):
    if chat_id >= 0 and required_rank > RANK_NONE:
        await update.message.reply_text("Эта команда работает только в группах"); return False
    if chat_id >= 0: return True
    creator = await get_chat_creator(chat_id, context)
    if creator and user_id == creator: return True
    rank = get_admin_rank(chat_id, user_id)
    if rank < required_rank:
        await update.message.reply_text("Не дорос еще! Недостаточно прав!!!"); return False
    return True
# ==================== ДУЭЛЬ ====================

async def duel_start(update, context, hard=False):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    gid = str(int(time.time() * 1000))
    rounds = 10 if hard else 5
    timeout = 3 if hard else 4
    game = {"gid": gid, "chat_id": chat_id, "user_id": user_id, "round": 0, "max_rounds": rounds, "timeout": timeout, "active": True, "state": "intro", "last_action": time.time(), "hard": hard, "msg_id": None, "expected": None}
    duel_games[gid] = game
    start_phrase = random.choice(DUEL_START_PHRASES)
    msg = await update.message.reply_text(f"– {start_phrase}", parse_mode="HTML")
    game["msg_id"] = msg.message_id
    await asyncio.sleep(2)
    asyncio.create_task(duel_rar_turn(context, game))


async def duel_rar_turn(context, game):
    if not game.get("active"): return
    await asyncio.sleep(1)
    game["round"] += 1
    game["last_action"] = time.time()
    if game["round"] > game["max_rounds"]:
        game["active"] = False
        duel_games.pop(game["gid"], None)
        try:
            await context.bot.send_message(chat_id=game["chat_id"], text=f"– {random.choice(DUEL_PLAYER_WIN_PHRASES)}", parse_mode="HTML")
        except Exception: pass
        return
    rar_action = random.choice(DUEL_ACTIONS)
    game["expected"] = DUEL_COUNTERS[rar_action]
    game["state"] = "waiting_player"
    game["deadline"] = time.time() + game["timeout"]
    try:
        await context.bot.send_message(chat_id=game["chat_id"], text=f"<b>{rar_action}</b>", parse_mode="HTML")
    except Exception: pass
    asyncio.create_task(duel_timer(context, game, game["round"]))


async def duel_timer(context, game, round_num):
    await asyncio.sleep(game["timeout"] + 0.3)
    if not game.get("active"): return
    if game.get("round") != round_num: return
    if game.get("state") != "waiting_player": return
    game["active"] = False
    duel_games.pop(game["gid"], None)
    final_sound = random.choice(DUEL_FINAL_SOUNDS)
    try:
        await context.bot.send_message(chat_id=game["chat_id"], text=f"<b>{final_sound}</b>\n– {random.choice(DUEL_RAR_WIN_PHRASES)}", parse_mode="HTML")
    except Exception: pass


# ==================== РУЛЕТКА ====================

def rl_hp_bar(hp, max_hp, zerkalo=False):
    filled = "\u25a0" * hp
    empty = "\u25a1" * (max_hp - hp)
    bar = f"<b>{filled}</b>{empty}"
    if zerkalo: return f"({bar})"
    return bar


def rl_make_magazine(count):
    if count >= 8:
        bullets = random.randint(3, count - 1)
    else:
        bullets = random.randint(2, count - 1)
    blanks = count - bullets
    mag = ["B"] * bullets + ["H"] * blanks
    random.shuffle(mag)
    return mag, bullets, blanks


def rl_generate_items(pool, count):
    return [random.choice(pool) for _ in range(count)]


def rl_items_text(items):
    if not items: return "(нет)"
    return ", ".join(ITEM_NAMES.get(i, i) for i in items)


def rl_round_config(round_num):
    if round_num == 1: return {"hp": 2, "items": 0, "pool": []}
    if round_num == 2: return {"hp": 4, "items": 2, "pool": ROUND2_POOL}
    return {"hp": 6, "items": 4, "pool": ROUND3_POOL}


def rl_render(game):
    hp_p = rl_hp_bar(game["player_hp"], game["player_max_hp"], game.get("player_zerkalo", False))
    hp_r = rl_hp_bar(game["rar_hp"], game["rar_max_hp"], game.get("rar_zerkalo", False))
    lines = [f"<b>РАУНД {game['round']}</b>", ""]
    lines.append(f"Rar: {hp_r}    Ты: {hp_p}")
    lines.append("")
    lines.append(f"В обойме: {len(game['magazine']) - game['shot_idx']}")
    if game["round"] >= 2:
        lines.append(f"Твои предметы: {rl_items_text(game['player_items'])}")
        lines.append(f"Предметы Rar: {rl_items_text(game['rar_items'])}")
    lines.append("")
    if game.get("log"):
        lines.append(game["log"])
        lines.append("")
    status = game.get("status", "")
    if status:
        if game.get("nasadka_active"): status += ", насадка активна"
        lines.append(f"<i>{status}</i>")
    return "\n".join(lines)


def rl_keyboard(game):
    rows = []
    if game.get("finished"): return None
    if game["turn"] == "player":
        rows.append([
            InlineKeyboardButton("Выстрел в себя", callback_data=f"rl_shoot_self_{game['gid']}"),
            InlineKeyboardButton("Выстрел в Rar", callback_data=f"rl_shoot_rar_{game['gid']}"),
        ])
        if game["round"] >= 2 and game["player_items"]:
            item_buttons = [InlineKeyboardButton(ITEM_NAMES[it], callback_data=f"rl_item_{it}_{game['gid']}") for it in game["player_items"]]
            rows.append(item_buttons)
    elif game["turn"] == "rar":
        rows.append([InlineKeyboardButton("Ход Rar...", callback_data=f"rl_noop_{game['gid']}")])
    return InlineKeyboardMarkup(rows) if rows else None


async def rl_edit(context, game):
    try:
        await context.bot.edit_message_text(chat_id=game["chat_id"], message_id=game["msg_id"], text=rl_render(game), parse_mode="HTML", reply_markup=rl_keyboard(game))
    except Exception as e:
        print(f"[RL EDIT ERROR] {e}")


async def rl_finish(context, game, winner):
    game["finished"] = True
    game["turn"] = "finished"
    game["status"] = "Победа за тобой." if winner == "player" else "Победа за мной."
    game["log"] = ""
    if winner == "player":
        if game["round"] == 1:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Закрыть", callback_data=f"rl_close_{game['gid']}"), InlineKeyboardButton("Далее", callback_data=f"rl_offer2_{game['gid']}")]])
        elif game["round"] == 2:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Закрыть", callback_data=f"rl_close_{game['gid']}"), InlineKeyboardButton("Далее", callback_data=f"rl_offer3_{game['gid']}")]])
        else:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Закрыть", callback_data=f"rl_close_{game['gid']}")]])
    else:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Закрыть", callback_data=f"rl_close_{game['gid']}")]])
    try:
        await context.bot.edit_message_text(chat_id=game["chat_id"], message_id=game["msg_id"], text=rl_render(game), parse_mode="HTML", reply_markup=kb)
    except Exception: pass


async def rl_offer_round2(context, gid, chat_id):
    game = roulette_games.get(gid)
    if not game: return
    game["log"] = "– Сыграем второй раунд?"
    game["status"] = ""
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("2 Раунд", callback_data=f"rl_r2_{gid}"), InlineKeyboardButton('"Нет"', callback_data=f"rl_close_{gid}")]])
    try:
        await context.bot.edit_message_text(chat_id=game["chat_id"], message_id=game["msg_id"], text=rl_render(game), parse_mode="HTML", reply_markup=kb)
    except Exception: pass


async def rl_offer_round3(context, gid, chat_id):
    game = roulette_games.get(gid)
    if not game: return
    game["log"] = "– Сыграем последний раунд?"
    game["status"] = ""
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("3 Раунд", callback_data=f"rl_r3_{gid}"), InlineKeyboardButton('"Нет"', callback_data=f"rl_close_{gid}")]])
    try:
        await context.bot.edit_message_text(chat_id=game["chat_id"], message_id=game["msg_id"], text=rl_render(game), parse_mode="HTML", reply_markup=kb)
    except Exception: pass


async def rl_reload(context, game):
    count = random.randint(3, 8)
    mag, bullets, blanks = rl_make_magazine(count)
    game["magazine"] = mag
    game["shot_idx"] = 0
    game["status"] = "Rar перезаряжает ружьё..."
    game["log"] = ""
    await rl_edit(context, game)
    await asyncio.sleep(3)
    game["log"] = f"– {blanks} холостых, {bullets} боевых"
    await rl_edit(context, game)
    await asyncio.sleep(2)


async def rl_start(update, context):
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    gid = str(int(time.time() * 1000))
    cfg = rl_round_config(1)
    count = random.randint(3, 8)
    mag, bullets, blanks = rl_make_magazine(count)
    game = {"gid": gid, "chat_id": chat_id, "player_id": user_id, "msg_id": None, "round": 1, "player_hp": cfg["hp"], "rar_hp": cfg["hp"], "player_max_hp": cfg["hp"], "rar_max_hp": cfg["hp"], "magazine": mag, "shot_idx": 0, "player_items": [], "rar_items": [], "player_zerkalo": False, "rar_zerkalo": False, "nasadka_active": False, "turn": "player", "log": "", "status": "Твой ход", "finished": False, "last_action": time.time()}
    msg = await update.message.reply_text(rl_render(game), parse_mode="HTML")
    game["msg_id"] = msg.message_id
    game["log"] = f"– Вставляю патроны.\n– {blanks} холостых, {bullets} боевых"
    await rl_edit(context, game)
    roulette_games[gid] = game


async def rl_round2_start(context, gid, chat_id):
    old = roulette_games.get(gid)
    if not old: return
    player_id = old["player_id"]; msg_id = old["msg_id"]
    cfg = rl_round_config(2)
    count = random.randint(3, 8)
    mag, bullets, blanks = rl_make_magazine(count)
    p_items = rl_generate_items(cfg["pool"], cfg["items"])
    r_items = rl_generate_items(cfg["pool"], cfg["items"])
    game = {"gid": gid, "chat_id": chat_id, "player_id": player_id, "msg_id": msg_id, "round": 2, "player_hp": cfg["hp"], "rar_hp": cfg["hp"], "player_max_hp": cfg["hp"], "rar_max_hp": cfg["hp"], "magazine": mag, "shot_idx": 0, "player_items": [], "rar_items": [], "player_zerkalo": False, "rar_zerkalo": False, "nasadka_active": False, "turn": "player", "log": "", "status": "Подготовка...", "finished": False, "last_action": time.time()}
    roulette_games[gid] = game
    game["log"] = "<b>Появляется ящик.</b>"
    await rl_edit(context, game); await asyncio.sleep(1)
    game["log"] = "– Два предмета тебе, два мне."
    await rl_edit(context, game); await asyncio.sleep(1)
    game["player_items"].append(p_items[0]); await rl_edit(context, game); await asyncio.sleep(1)
    game["player_items"].append(p_items[1]); await rl_edit(context, game); await asyncio.sleep(1)
    game["rar_items"].append(r_items[0]); await rl_edit(context, game); await asyncio.sleep(1)
    game["rar_items"].append(r_items[1]); await rl_edit(context, game); await asyncio.sleep(1)
    game["log"] = "– Да будет нам мила Госпожа Удача."
    await rl_edit(context, game); await asyncio.sleep(4)
    game["log"] = f"– Вставляю патроны.\n– {blanks} холостых, {bullets} боевых"
    game["status"] = "Твой ход"
    await rl_edit(context, game)


async def rl_round3_start(context, gid, chat_id):
    old = roulette_games.get(gid)
    if not old: return
    player_id = old["player_id"]; msg_id = old["msg_id"]
    cfg = rl_round_config(3)
    count = random.randint(3, 8)
    mag, bullets, blanks = rl_make_magazine(count)
    p_items = rl_generate_items(cfg["pool"], cfg["items"])
    r_items = rl_generate_items(cfg["pool"], cfg["items"])
    game = {"gid": gid, "chat_id": chat_id, "player_id": player_id, "msg_id": msg_id, "round": 3, "player_hp": cfg["hp"], "rar_hp": cfg["hp"], "player_max_hp": cfg["hp"], "rar_max_hp": cfg["hp"], "magazine": mag, "shot_idx": 0, "player_items": [], "rar_items": [], "player_zerkalo": False, "rar_zerkalo": False, "nasadka_active": False, "turn": "player", "log": "", "status": "Подготовка...", "finished": False, "last_action": time.time()}
    roulette_games[gid] = game
    start_phrase = random.choice(ROUND3_START_PHRASES)
    game["log"] = f"– {start_phrase}"
    await rl_edit(context, game); await asyncio.sleep(3)
    game["log"] = "<b>Появляется ящик.</b>"
    await rl_edit(context, game); await asyncio.sleep(1)
    game["log"] = "– Четыре предмета тебе, четыре мне."
    await rl_edit(context, game); await asyncio.sleep(1)
    for i in range(4):
        game["player_items"].append(p_items[i]); await rl_edit(context, game); await asyncio.sleep(1)
    for i in range(4):
        game["rar_items"].append(r_items[i]); await rl_edit(context, game); await asyncio.sleep(1)
    game["log"] = f"– Вставляю патроны.\n– {blanks} холостых, {bullets} боевых"
    game["status"] = "Твой ход"
    await rl_edit(context, game)
async def rl_rar_turn(context, game):
    if game.get("finished"): return
    if game.get("rar_skip"):
        game["rar_skip"] = False
        game["log"] = "– Наручники. Ход пропущен."
        game["turn"] = "player"
        game["status"] = "Твой ход"
        await rl_edit(context, game)
        await asyncio.sleep(2)
        return

    await asyncio.sleep(2)
    game["turn"] = "rar"
    game["log"] = "– Ход мой."
    game["status"] = "Ход Rar"
    await rl_edit(context, game)
    await asyncio.sleep(2)

    remaining = len(game["magazine"]) - game["shot_idx"]
    if remaining == 1:
        game["rar_knows"] = game["magazine"][game["shot_idx"]]

    if game["round"] >= 2 and game["rar_items"]:
        if "tabletki" in game["rar_items"]:
            hp = game["rar_hp"]
            if hp < game["rar_max_hp"] and (hp > 1 or len(game["rar_items"]) == 1):
                game["rar_items"].remove("tabletki")
                if random.randint(1, 2) == 1:
                    game["rar_hp"] = min(game["rar_max_hp"], game["rar_hp"] + 2)
                    game["log"] = "– Таблетки. Полегчало."
                else:
                    game["rar_hp"] -= 1
                    game["log"] = "– Таблетки. Плохо."
                game["status"] = "Ход Rar"
                await rl_edit(context, game); await asyncio.sleep(2)
                if game["rar_hp"] <= 0:
                    await rl_finish(context, game, "player"); return

        if "telefon" in game["rar_items"]:
            game["rar_items"].remove("telefon")
            game["log"] = "– Ага, хм..."
            game["status"] = "Ход Rar"
            await rl_edit(context, game); await asyncio.sleep(3)
            remaining_list = game["magazine"][game["shot_idx"]:]
            if remaining_list:
                idx = random.randint(0, len(remaining_list) - 1)
                game["rar_knows"] = remaining_list[idx]

        if "lupa" in game["rar_items"] and not game.get("rar_knows"):
            game["rar_items"].remove("lupa")
            game["rar_knows"] = game["magazine"][game["shot_idx"]]
            game["log"] = "– Смотрю в ствол."
            game["status"] = "Ход Rar"
            await rl_edit(context, game); await asyncio.sleep(2)

        if "sigara" in game["rar_items"] and game["rar_hp"] < game["rar_max_hp"]:
            game["rar_items"].remove("sigara")
            game["rar_hp"] += 1
            game["log"] = "– Затянусь."
            game["status"] = "Ход Rar"
            await rl_edit(context, game); await asyncio.sleep(2)

        if "b_pulya" in game["rar_items"]:
            game["rar_items"].remove("b_pulya")
            game["magazine"].insert(game["shot_idx"], "B")
            game["rar_knows"] = "B"
            game["log"] = "– Вставляю боевой."
            game["status"] = "Ход Rar"
            await rl_edit(context, game); await asyncio.sleep(2)

        if "nasadka" in game["rar_items"] and not game.get("nasadka_active"):
            game["rar_items"].remove("nasadka")
            game["nasadka_active"] = True
            game["log"] = "– Насадка на ствол."
            game["status"] = "Ход Rar"
            await rl_edit(context, game); await asyncio.sleep(2)

        if "zerkalo" in game["rar_items"] and not game.get("rar_zerkalo"):
            game["rar_items"].remove("zerkalo")
            game["rar_zerkalo"] = True
            game["log"] = "– Ставлю зеркало."
            game["status"] = "Ход Rar"
            await rl_edit(context, game); await asyncio.sleep(2)

        if "pila" in game["rar_items"] and not game.get("nasadka_active") and game.get("rar_knows") == "B":
            game["rar_items"].remove("pila")
            game["nasadka_active"] = True
            game["log"] = "– Срезаю ствол."
            game["status"] = "Ход Rar"
            await rl_edit(context, game); await asyncio.sleep(2)

        if "adrenalin" in game["rar_items"]:
            stealable = [it for it in game["player_items"] if it != "adrenalin"]
            if stealable:
                stolen = random.choice(stealable)
                game["player_items"].remove(stolen)
                game["rar_items"].remove("adrenalin")
                game["log"] = f"– Адреналин. Забираю {ITEM_NAMES[stolen]}."
                game["status"] = "Ход Rar"
                await rl_edit(context, game); await asyncio.sleep(2)
                if stolen == "lupa" and not game.get("rar_knows"):
                    game["rar_knows"] = game["magazine"][game["shot_idx"]]
                elif stolen == "sigara" and game["rar_hp"] < game["rar_max_hp"]:
                    game["rar_hp"] += 1
                elif stolen == "b_pulya":
                    game["magazine"].insert(game["shot_idx"], "B")
                    game["rar_knows"] = "B"
                elif stolen == "nasadka":
                    game["nasadka_active"] = True
                elif stolen == "zerkalo":
                    game["rar_zerkalo"] = True
                elif stolen == "tabletki":
                    if random.randint(1, 2) == 1:
                        game["rar_hp"] = min(game["rar_max_hp"], game["rar_hp"] + 2)
                    else:
                        game["rar_hp"] -= 1
                elif stolen == "pila":
                    game["nasadka_active"] = True

        if "naruch" in game["rar_items"]:
            game["rar_items"].remove("naruch")
            game["player_skip"] = True
            game["log"] = "– Наручники на тебя."
            game["status"] = "Ход Rar"
            await rl_edit(context, game); await asyncio.sleep(2)

    knows = game.get("rar_knows")
    under_mirror = game.get("rar_zerkalo", False)
    if knows == "B":
        target = "player"
    elif knows == "H":
        target = "rar"
    else:
        if under_mirror:
            target = "player" if random.randint(1, 2) == 1 else "rar"
        else:
            if game["round"] == 3:
                target = "player" if random.randint(1, 100) <= 80 else "rar"
            else:
                target = "player" if random.randint(1, 100) <= 70 else "rar"

    game["log"] = "– Стреляю в тебя." if target == "player" else "– Стреляю в себя."
    game["status"] = "Ход Rar"
    await rl_edit(context, game)
    await asyncio.sleep(2)

    shot = game["magazine"][game["shot_idx"]]
    game["shot_idx"] += 1
    dmg = 2 if game.get("nasadka_active") else 1

    if shot == "B":
        if target == "player":
            if game.get("player_zerkalo"):
                game["player_zerkalo"] = False
                if dmg == 1:
                    game["rar_hp"] -= 1
                    game["log"] = "<b>Зеркало. Урон в Rar.</b>"
                else:
                    game["player_hp"] -= 1
                    game["rar_hp"] -= 2
                    game["log"] = "<b>Зеркало разлетелось. 1 тебе, 2 Rar.</b>"
            else:
                game["player_hp"] -= dmg
                game["log"] = "<b>Попадание.</b>"
        else:
            if game.get("rar_zerkalo"):
                game["rar_zerkalo"] = False
                if dmg == 1:
                    game["player_hp"] -= 1
                    game["log"] = "<b>Зеркало Rar. Урон в тебя.</b>"
                else:
                    game["rar_hp"] -= 1
                    game["player_hp"] -= 2
                    game["log"] = "<b>Зеркало Rar разлетелось. 1 ей, 2 тебе.</b>"
            else:
                game["rar_hp"] -= dmg
                game["log"] = "<b>Попадание.</b>"
        if game.get("nasadka_active"): game["nasadka_active"] = False
    else:
        game["log"] = "<b>Щелчок. Пусто.</b>"
        if game.get("nasadka_active"):
            game["nasadka_active"] = False
            game["rar_hp"] -= 1
            game["log"] = "<b>Разрыв насадки.</b>"

    game["rar_knows"] = None

    if game["player_hp"] <= 0: await rl_finish(context, game, "rar"); return
    if game["rar_hp"] <= 0: await rl_finish(context, game, "player"); return

    game["status"] = "Ход Rar"
    await rl_edit(context, game); await asyncio.sleep(2)

    reloaded = False
    if game["shot_idx"] >= len(game["magazine"]):
        await rl_reload(context, game)
        reloaded = True

    if shot == "H" and target == "rar":
        asyncio.create_task(rl_rar_turn(context, game)); return

    if game.get("player_skip"):
        game["player_skip"] = False
        game["log"] = "– Твой ход пропущен."
        game["status"] = "Ход Rar"
        await rl_edit(context, game); await asyncio.sleep(2)
        asyncio.create_task(rl_rar_turn(context, game)); return

    game["turn"] = "player"
    if not reloaded: game["log"] = ""
    game["status"] = "Твой ход"
    if game["player_hp"] == 1 and game["rar_hp"] == 1 and not reloaded:
        game["log"] = "– Сейчас осторожнее."
    elif game["rar_hp"] == 1 and not reloaded:
        game["log"] = "– Гх..."
    await rl_edit(context, game)


async def rl_callback(update, context):
    q = update.callback_query
    try: await q.answer()
    except Exception: pass
    data = q.data
    user_id = q.from_user.id

    if data.startswith("rl_noop_"): return
    if data.startswith("rl_close_"):
        gid = data.split("_")[-1]
        game = roulette_games.get(gid)
        if game and game["player_id"] != user_id:
            try: await q.answer("Это не твоя игра.", show_alert=True)
            except Exception: pass
            return
        roulette_games.pop(gid, None)
        try: await q.message.delete()
        except Exception: pass
        return
    if data.startswith("rl_offer2_"):
        gid = data.split("_")[-1]
        game = roulette_games.get(gid)
        if not game: return
        if game["player_id"] != user_id:
            try: await q.answer("Это не твоя игра.", show_alert=True)
            except Exception: pass
            return
        await rl_offer_round2(context, gid, q.message.chat_id); return
    if data.startswith("rl_offer3_"):
        gid = data.split("_")[-1]
        game = roulette_games.get(gid)
        if not game: return
        if game["player_id"] != user_id:
            try: await q.answer("Это не твоя игра.", show_alert=True)
            except Exception: pass
            return
        await rl_offer_round3(context, gid, q.message.chat_id); return
    if data.startswith("rl_r2_"):
        gid = data.split("_")[-1]
        game = roulette_games.get(gid)
        if not game: return
        if game["player_id"] != user_id:
            try: await q.answer("Это не твоя игра.", show_alert=True)
            except Exception: pass
            return
        await rl_round2_start(context, gid, q.message.chat_id); return
    if data.startswith("rl_r3_"):
        gid = data.split("_")[-1]
        game = roulette_games.get(gid)
        if not game: return
        if game["player_id"] != user_id:
            try: await q.answer("Это не твоя игра.", show_alert=True)
            except Exception: pass
            return
        await rl_round3_start(context, gid, q.message.chat_id); return

    if data.startswith("rl_adr_cancel_"):
        gid = data.split("_")[-1]
        game = roulette_games.get(gid)
        if game: await rl_edit(context, game)
        return
    if data.startswith("rl_adr_"):
        parts = data.split("_")
        item = parts[2]; gid = parts[3]
        game = roulette_games.get(gid)
        if not game or game["player_id"] != user_id: return
        if item not in game["rar_items"]: return
        if "adrenalin" not in game["player_items"]: return
        game["player_items"].remove("adrenalin")
        game["rar_items"].remove(item)
        game["player_items"].append(item)
        game["log"] = f"– Забрал {ITEM_NAMES[item]}."
        await rl_edit(context, game)
        return

    parts = data.split("_")
    if len(parts) < 3: return

    if parts[1] == "shoot":
        gid = parts[-1]; target = parts[2]
        game = roulette_games.get(gid)
        if not game or game["finished"]: return
        if game["player_id"] != user_id:
            try: await q.answer("Это не твоя игра.", show_alert=True)
            except Exception: pass
            return
        if game["turn"] != "player": return
        game["turn"] = "rar"
        game["last_action"] = time.time()

        shot = game["magazine"][game["shot_idx"]]
        game["shot_idx"] += 1
        dmg = 2 if game.get("nasadka_active") else 1

        if shot == "B":
            if target == "self":
                if game.get("player_zerkalo"):
                    game["player_zerkalo"] = False
                    if dmg == 1:
                        game["rar_hp"] -= 1
                        game["log"] = "<b>Зеркало. Урон в Rar.</b>"
                    else:
                        game["player_hp"] -= 1
                        game["rar_hp"] -= 2
                        game["log"] = "<b>Зеркало разлетелось. 1 тебе, 2 Rar.</b>"
                else:
                    game["player_hp"] -= dmg
                    game["log"] = "<b>Попадание.</b>"
            else:
                if game.get("rar_zerkalo"):
                    game["rar_zerkalo"] = False
                    if dmg == 1:
                        game["player_hp"] -= 1
                        game["log"] = "<b>Зеркало Rar. Урон в тебя.</b>"
                    else:
                        game["rar_hp"] -= 1
                        game["player_hp"] -= 2
                        game["log"] = "<b>Зеркало Rar разлетелось. 1 ей, 2 тебе.</b>"
                else:
                    game["rar_hp"] -= dmg
                    game["log"] = "<b>Попадание.</b>"
            if game.get("nasadka_active"): game["nasadka_active"] = False
        else:
            game["log"] = "<b>Щелчок. Пусто.</b>"
            if game.get("nasadka_active"):
                game["nasadka_active"] = False
                if game.get("player_zerkalo"):
                    game["player_zerkalo"] = False
                    game["rar_hp"] -= 1
                    game["log"] = "<b>Разрыв насадки в зеркало.</b>"
                else:
                    game["player_hp"] -= 1
                    game["log"] = "<b>Разрыв насадки.</b>"

        if game["player_hp"] <= 0: await rl_finish(context, game, "rar"); return
        if game["rar_hp"] <= 0: await rl_finish(context, game, "player"); return

        reload_needed = game["shot_idx"] >= len(game["magazine"])
        if reload_needed:
            count = random.randint(3, 8)
            mag, bullets, blanks = rl_make_magazine(count)
            game["magazine"] = mag
            game["shot_idx"] = 0

        if shot == "H" and target == "self":
            game["turn"] = "player"
            game["status"] = "Твой ход снова"
            if reload_needed:
                game["status"] = "Rar перезаряжает ружьё..."
                game["log"] = ""
                await rl_edit(context, game)
                await asyncio.sleep(3)
                game["log"] = f"– {blanks} холостых, {bullets} боевых"
                game["status"] = "Твой ход снова"
            await rl_edit(context, game)
            return

        game["status"] = "Ход Rar"
        await rl_edit(context, game)
        if reload_needed:
            game["status"] = "Rar перезаряжает ружьё..."
            game["log"] = ""
            await rl_edit(context, game)
            await asyncio.sleep(3)
            game["log"] = f"– {blanks} холостых, {bullets} боевых"
            game["status"] = "Ход Rar"
            await rl_edit(context, game)
            await asyncio.sleep(2)
        asyncio.create_task(rl_rar_turn(context, game))
        return

    if parts[1] == "item":
        gid = parts[-1]; item = parts[2]
        game = roulette_games.get(gid)
        if not game or game["finished"]: return
        if game["player_id"] != user_id:
            try: await q.answer("Это не твоя игра.", show_alert=True)
            except Exception: pass
            return
        if game["turn"] != "player": return
        if item not in game["player_items"]: return

        if item == "lupa":
            game["player_items"].remove("lupa")
            game["player_knows"] = game["magazine"][game["shot_idx"]]
            label = "боевой" if game["player_knows"] == "B" else "холостой"
            game["log"] = f"– Следующий: {label}."
        elif item == "pila":
            game["player_items"].remove("pila")
            game["nasadka_active"] = True
            game["log"] = "– Ствол срезан."
        elif item == "naruch":
            game["player_items"].remove("naruch")
            game["rar_skip"] = True
            game["log"] = "– Наручники на Rar."
        elif item == "sigara":
            game["player_items"].remove("sigara")
            if game["player_hp"] < game["player_max_hp"]:
                game["player_hp"] += 1; game["log"] = "– +1 HP."
            else: game["log"] = "– Уже максимум."
        elif item == "tabletki":
            game["player_items"].remove("tabletki")
            if random.randint(1, 2) == 1:
                game["player_hp"] = min(game["player_max_hp"], game["player_hp"] + 2)
                game["log"] = "– Таблетки. Полегчало."
            else:
                game["player_hp"] -= 1
                game["log"] = "– Таблетки. Плохо."
        elif item == "telefon":
            game["player_items"].remove("telefon")
            remaining = game["magazine"][game["shot_idx"]:]
            if remaining:
                lines_to_show = []
                for i, p in enumerate(remaining[:6]):
                    label = "боевой" if p == "B" else "холостой"
                    lines_to_show.append(f"– {i+1} патрон...")
                    game["log"] = "\n".join(lines_to_show)
                    await rl_edit(context, game); await asyncio.sleep(1)
                    lines_to_show[-1] = f"– {i+1} патрон... будет..."
                    game["log"] = "\n".join(lines_to_show)
                    await rl_edit(context, game); await asyncio.sleep(1)
                    lines_to_show[-1] = f"– {i+1} патрон... будет... {label}..."
                    game["log"] = "\n".join(lines_to_show)
                    await rl_edit(context, game); await asyncio.sleep(1)
                game["log"] = "\n".join(lines_to_show) + "\nТелефон разряжен."
                await rl_edit(context, game); await asyncio.sleep(2)
            else:
                game["log"] = "– Пусто."
        elif item == "b_pulya":
            game["player_items"].remove("b_pulya")
            game["magazine"].insert(game["shot_idx"], "B")
            game["log"] = "– Вставляю боевой."
        elif item == "nasadka":
            game["player_items"].remove("nasadka")
            game["nasadka_active"] = True
            game["log"] = "– Насадка на ствол."
        elif item == "zerkalo":
            game["player_items"].remove("zerkalo")
            game["player_zerkalo"] = True
            game["log"] = "– Ставлю зеркало."
        elif item == "adrenalin":
            stealable = [it for it in game["rar_items"] if it != "adrenalin"]
            if not stealable:
                game["log"] = "– У Rar нет предметов."
                await rl_edit(context, game); return
            rows = []
            for it in stealable:
                rows.append([InlineKeyboardButton(ITEM_NAMES[it], callback_data=f"rl_adr_{it}_{gid}")])
            rows.append([InlineKeyboardButton("Отмена", callback_data=f"rl_adr_cancel_{gid}")])
            await context.bot.edit_message_text(
                chat_id=game["chat_id"], message_id=game["msg_id"],
                text="Выбери предмет:", reply_markup=InlineKeyboardMarkup(rows))
            return
        await rl_edit(context, game)
        return


async def rl_timeout_loop(app):
    await asyncio.sleep(60)
    while True:
        try:
            now = time.time()
            for gid, game in list(roulette_games.items()):
                if now - game["last_action"] > ROULETTE_TIMEOUT:
                    try:
                        await app.bot.edit_message_text(
                            chat_id=game["chat_id"], message_id=game["msg_id"],
                            text="Сессия рулетки подошла к концу.",
                            reply_markup=None)
                    except Exception: pass
                    roulette_games.pop(gid, None)
            for gid, game in list(duel_games.items()):
                if now - game.get("last_action", 0) > DUEL_TIMEOUT:
                    game["active"] = False
                    duel_games.pop(gid, None)
        except Exception: pass
        await asyncio.sleep(30)


def save_track_to_db(file_id, title, added_by):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT title FROM channel_music WHERE file_id = %s OR LOWER(title) = LOWER(%s) LIMIT 1", (file_id, title))
    if cursor.fetchone():
        cursor.close(); conn.close(); return False
    cursor.execute("INSERT INTO channel_music (file_id, title, added_by) VALUES (%s, %s, %s) ON CONFLICT (file_id) DO NOTHING", (file_id, title, added_by))
    conn.commit(); cursor.close(); conn.close(); return True


def search_track_in_db(query):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT file_id, title, track_num FROM channel_music WHERE LOWER(title) LIKE LOWER(%s) LIMIT 1", (f"%{query.strip().lower()}%",))
    row = cursor.fetchone(); cursor.close(); conn.close(); return row


def search_track_in_db_by_title(title):
    if not title: return None
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT file_id, title, track_num FROM channel_music WHERE LOWER(title) = LOWER(%s) LIMIT 1", (title.strip(),))
    row = cursor.fetchone(); cursor.close(); conn.close(); return row


def search_tracks_in_db(query, limit=20):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT file_id, title, track_num FROM channel_music WHERE LOWER(title) LIKE LOWER(%s) AND inline_broken = FALSE LIMIT %s", (f"%{query.strip().lower()}%", limit))
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


def get_all_tracks_from_db(only_ok=False):
    conn = get_db_connection(); cursor = conn.cursor()
    if only_ok: cursor.execute("SELECT file_id, title FROM channel_music WHERE inline_broken = FALSE")
    else: cursor.execute("SELECT file_id, title FROM channel_music")
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


def mark_track_broken(file_id):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE channel_music SET inline_broken = TRUE WHERE file_id = %s", (file_id,))
        conn.commit(); cursor.close(); conn.close()
    except Exception as e: print(f"[MARK BROKEN ERROR] {e}")


def get_broken_tracks():
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT track_num, title FROM channel_music WHERE inline_broken = TRUE ORDER BY track_num")
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


def delete_track_from_db(file_id, fallback_title=None):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT title, track_num FROM channel_music WHERE file_id = %s", (file_id,))
    row = cursor.fetchone()
    if not row and fallback_title:
        cursor.execute("SELECT title, track_num FROM channel_music WHERE LOWER(title) = LOWER(%s)", (fallback_title,))
        row = cursor.fetchone()
    if row:
        title, tnum = row
        cursor.execute("UPDATE playlists SET track_nums = array_remove(track_nums, %s) WHERE %s = ANY(track_nums)", (tnum, tnum))
        cursor.execute("DELETE FROM channel_music WHERE track_num = %s", (tnum,))
        conn.commit(); cursor.close(); conn.close(); return title
    cursor.close(); conn.close(); return None


def get_track_num(file_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT track_num FROM channel_music WHERE file_id = %s", (file_id,))
    row = cursor.fetchone(); cursor.close(); conn.close(); return row[0] if row else None


def get_track_by_num(track_num):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT file_id, title FROM channel_music WHERE track_num = %s", (track_num,))
    row = cursor.fetchone(); cursor.close(); conn.close(); return row


def get_tracks_by_nums(track_nums):
    if not track_nums: return []
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT track_num, file_id, title FROM channel_music WHERE track_num = ANY(%s) AND inline_broken = FALSE", (track_nums,))
    rows = cursor.fetchall(); cursor.close(); conn.close()
    mapping = {r[0]: (r[1], r[2]) for r in rows}
    return [mapping[tn] for tn in track_nums if tn in mapping]


def increment_play(file_id):
    try:
        tn = get_track_num(file_id)
        if not tn: return
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO track_stats (track_num, plays, last_played) VALUES (%s, 1, NOW()) ON CONFLICT (track_num) DO UPDATE SET plays = track_stats.plays + 1, last_played = NOW()", (tn,))
        conn.commit(); cursor.close(); conn.close()
    except Exception as e: print(f"[INCREMENT PLAY ERROR] {e}")


def get_top_tracks(limit=10):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT cm.title, ts.plays FROM track_stats ts JOIN channel_music cm ON cm.track_num = ts.track_num WHERE ts.plays > 0 ORDER BY ts.plays DESC LIMIT %s", (limit,))
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


def create_playlist(name, owner_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT playlist_id FROM playlists WHERE LOWER(name) = LOWER(%s)", (name,))
    if cursor.fetchone():
        cursor.close(); conn.close(); return None, "exists"
    try:
        cursor.execute("INSERT INTO playlists (name, owner_id) VALUES (%s, %s) RETURNING playlist_id", (name, owner_id))
        pid = cursor.fetchone()[0]
        cursor.execute("INSERT INTO playlist_stats (playlist_id) VALUES (%s) ON CONFLICT DO NOTHING", (pid,))
        conn.commit(); cursor.close(); conn.close(); return pid, None
    except Exception as e:
        conn.rollback(); cursor.close(); conn.close(); return None, str(e)


def get_playlist_by_name(name):
    if not name: return None
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT playlist_id, name, owner_id, track_nums FROM playlists WHERE LOWER(name) = LOWER(%s)", (name,))
    row = cursor.fetchone(); cursor.close(); conn.close(); return row


def get_playlist_by_id(pid):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT playlist_id, name, owner_id, track_nums FROM playlists WHERE playlist_id = %s", (pid,))
    row = cursor.fetchone(); cursor.close(); conn.close(); return row


def get_user_playlists(owner_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT playlist_id, name, track_nums FROM playlists WHERE owner_id = %s ORDER BY created_at ASC", (owner_id,))
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


def delete_playlist(pid):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM playlists WHERE playlist_id = %s", (pid,))
    conn.commit(); cursor.close(); conn.close()


def rename_playlist(pid, new_name):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT playlist_id FROM playlists WHERE LOWER(name) = LOWER(%s) AND playlist_id != %s", (new_name, pid))
    if cursor.fetchone():
        cursor.close(); conn.close(); return "exists"
    cursor.execute("UPDATE playlists SET name = %s WHERE playlist_id = %s", (new_name, pid))
    conn.commit(); cursor.close(); conn.close(); return None


def add_track_to_playlist(pid, track_num):
    if track_num is None: return "not_found"
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT track_nums FROM playlists WHERE playlist_id = %s", (pid,))
    row = cursor.fetchone()
    if not row:
        cursor.close(); conn.close(); return "not_found"
    nums = row[0] or []
    if track_num in nums:
        cursor.close(); conn.close(); return "already"
    if len(nums) >= PLAYLIST_LIMIT:
        cursor.close(); conn.close(); return "limit"
    cursor.execute("UPDATE playlists SET track_nums = array_append(track_nums, %s) WHERE playlist_id = %s", (track_num, pid))
    conn.commit(); cursor.close(); conn.close(); return "ok"


def remove_track_from_playlist(pid, track_num):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE playlists SET track_nums = array_remove(track_nums, %s) WHERE playlist_id = %s", (track_num, pid))
    conn.commit(); cursor.close(); conn.close()


def increment_playlist_search(pid):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO playlist_stats (playlist_id, searches, last_searched) VALUES (%s, 1, NOW()) ON CONFLICT (playlist_id) DO UPDATE SET searches = playlist_stats.searches + 1, last_searched = NOW()", (pid,))
        conn.commit(); cursor.close(); conn.close()
    except Exception as e: print(f"[PLAYLIST SEARCH ERROR] {e}")


def get_top_playlists(limit=10):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT p.name, ps.searches FROM playlist_stats ps JOIN playlists p ON p.playlist_id = ps.playlist_id WHERE ps.searches > 0 ORDER BY ps.searches DESC LIMIT %s", (limit,))
    rows = cursor.fetchall(); cursor.close(); conn.close(); return rows


def get_random_playlist_with_tracks(user_id):
    now = time.time()
    seen = recent_playlists.get(user_id, {})
    seen = {pid: ts for pid, ts in seen.items() if now - ts < RECENT_PLAYLISTS_TTL}
    if len(seen) > RECENT_PLAYLISTS_LIMIT:
        seen = dict(sorted(seen.items(), key=lambda x: x[1])[-RECENT_PLAYLISTS_LIMIT:])
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT playlist_id, name, owner_id, track_nums FROM playlists WHERE cardinality(track_nums) > 0")
    all_pls = cursor.fetchall(); cursor.close(); conn.close()
    if not all_pls:
        recent_playlists[user_id] = seen; return None
    available = [p for p in all_pls if p[0] not in seen]
    if not available: seen = {}; available = all_pls
    chosen = random.choice(available)
    seen[chosen[0]] = now
    recent_playlists[user_id] = seen
    return chosen


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
    cursor.execute("INSERT INTO chat_members (chat_id, user_ids) VALUES (%s, ARRAY[%s::BIGINT]) ON CONFLICT (chat_id) DO UPDATE SET user_ids = (SELECT ARRAY(SELECT DISTINCT unnest(chat_members.user_ids || EXCLUDED.user_ids)))", (chat_id, user_id))
    conn.commit(); cursor.close(); conn.close()


def remove_user_from_chat(user_id, chat_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE chat_members SET user_ids = array_remove(user_ids, %s::BIGINT) WHERE chat_id = %s", (user_id, chat_id))
    cursor.execute("DELETE FROM chat_members WHERE chat_id = %s AND cardinality(user_ids) = 0", (chat_id,))
    cursor.execute("DELETE FROM chat_admins WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    cursor.execute("DELETE FROM sleuths WHERE chat_id = %s AND target_id = %s", (chat_id, user_id))
    conn.commit(); cursor.close(); conn.close()


def remove_users_from_chat_batch(user_ids, chat_id):
    if not user_ids: return
    conn = get_db_connection(); cursor = conn.cursor()
    for uid in user_ids:
        cursor.execute("UPDATE chat_members SET user_ids = array_remove(user_ids, %s::BIGINT) WHERE chat_id = %s", (int(uid), chat_id))
        cursor.execute("DELETE FROM chat_admins WHERE chat_id = %s AND user_id = %s", (chat_id, int(uid)))
        cursor.execute("DELETE FROM sleuths WHERE chat_id = %s AND target_id = %s", (chat_id, int(uid)))
    cursor.execute("DELETE FROM chat_members WHERE chat_id = %s AND cardinality(user_ids) = 0", (chat_id,))
    conn.commit(); cursor.close(); conn.close()


def remove_chat_data(chat_id):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_members WHERE chat_id = %s", (chat_id,))
    cursor.execute("DELETE FROM chat_settings WHERE chat_id = %s", (chat_id,))
    cursor.execute("DELETE FROM chat_admins WHERE chat_id = %s", (chat_id,))
    cursor.execute("DELETE FROM sleuths WHERE chat_id = %s", (chat_id,))
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
    if " - " in full_title: return full_title.split(" - ", 1)[1].strip()
    return full_title.strip()


def parse_duration(text):
    if not text: return None
    total = 0; found = False
    for m in re.finditer(r"(\d+)\s*(с|сек|м|мин|ч|час|д|дн|день|дней)", text.lower()):
        val = int(m.group(1)); unit = m.group(2)
        if unit in ("с", "сек"): total += val
        elif unit in ("м", "мин"): total += val * 60
        elif unit in ("ч", "час"): total += val * 3600
        elif unit in ("д", "дн", "день", "дней"): total += val * 86400
        found = True
    return total if found else None


def format_duration(seconds):
    if seconds < 60: return f"{seconds} сек"
    if seconds < 3600: return f"{seconds // 60} мин"
    if seconds < 86400: return f"{seconds // 3600} ч {seconds % 3600 // 60} мин"
    return f"{seconds // 86400} д {seconds % 86400 // 3600} ч"


async def log_to_owner(context, text):
    try:
        owner_id = get_owner_id()
        if owner_id:
            safe = text.replace("<", "&lt;").replace(">", "&gt;")
            if len(safe) > 3500: safe = safe[:3500] + "...[обрезано]"
            await context.bot.send_message(chat_id=owner_id, text=f"<b>LOG</b>\n<code>{safe}</code>", parse_mode="HTML")
    except Exception as e: print(f"[LOG ERROR] {e}")


async def notify_moderation(context, text):
    try:
        await context.bot.send_message(chat_id=MODERATION_CHAT_ID, text=text, parse_mode="HTML")
    except Exception as e: print(f"[MODERATION NOTIFY ERROR] {e}")


async def safe_answer_inline(update, context, results, cache_time=0):
    if not results:
        try: await update.inline_query.answer([], cache_time=cache_time)
        except Exception: pass
        return
    tried = list(results); removed_ids = []
    while tried:
        try:
            await update.inline_query.answer(tried, cache_time=cache_time)
            if removed_ids:
                await log_to_owner(context, f"[INLINE REMOVED] {len(removed_ids)}:\n" + "\n".join(removed_ids))
            return
        except Exception as e:
            err = str(e)
            if "content_type_invalid" in err.lower() or "Audio_content_type" in err:
                removed = tried.pop()
                fid_removed = None
                if hasattr(removed, 'id') and removed.id in inline_result_cache:
                    fid_removed, _ = inline_result_cache[removed.id]
                if fid_removed: mark_track_broken(fid_removed)
                removed_ids.append(getattr(removed, 'id', '?'))
            else:
                await log_to_owner(context, f"[INLINE FATAL] {err}\n{traceback.format_exc()[:800]}")
                try: await update.inline_query.answer([], cache_time=cache_time)
                except Exception: pass
                return
    try: await update.inline_query.answer([], cache_time=cache_time)
    except Exception: pass


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


def pick_random_with_antirepeat(user_id, limit):
    now = time.time()
    seen = inline_seen.get(user_id, {})
    seen = {fid: ts for fid, ts in seen.items() if now - ts < INLINE_SEEN_TTL}
    if len(seen) > INLINE_SEEN_LIMIT:
        seen = dict(sorted(seen.items(), key=lambda x: x[1])[-INLINE_SEEN_LIMIT:])
    all_tracks = get_all_tracks_from_db(only_ok=True)
    if not all_tracks:
        inline_seen[user_id] = seen; return []
    available = [t for t in all_tracks if t[0] not in seen]
    if len(available) < limit: seen = {}; available = all_tracks
    random.shuffle(available)
    chosen = available[:limit]
    for fid, _ in chosen: seen[fid] = now
    inline_seen[user_id] = seen
    return chosen


def cleanup_inline_result_cache():
    now = time.time()
    to_delete = [rid for rid, (_, ts) in inline_result_cache.items() if now - ts > INLINE_RESULT_CACHE_TTL]
    for rid in to_delete: del inline_result_cache[rid]


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
        text = ("<b>Привет! Я Rar – ваш универсальный помощник.</b>\n\n"
                "В основном я работаю в чатах: храню коллекцию музыки, помогаю админам собирать участников, "
                "могу поговорить и исполняю другие не мало важные функции.\n\n"
                "Чтобы узнать, на что я способна, напишите в чате: <code>Рар команды</code>")
        await update.message.reply_text(text, parse_mode="HTML")


async def inline_query_handler(update, context):
    query = update.inline_query.query.strip().lower()
    user_id = update.inline_query.from_user.id
    results = []
    cleanup_inline_result_cache()
    try:
        if query == "":
            tracks = pick_random_with_antirepeat(user_id, limit=10)
            for i, (fid, title) in enumerate(tracks):
                results.append(InlineQueryResultCachedAudio(id=f"empty_{i}_{fid[:20]}", audio_file_id=fid, caption=clean_title(title)))
            results.append(InlineQueryResultArticle(id="shuffle_empty", title="Новый набор песен", description="Показать другие случайные треки", thumbnail_url=RAR_LOGO_URL, input_message_content=InputTextMessageContent("Новый набор"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Новый набор", switch_inline_query_current_chat="дай песню")]])))
            await safe_answer_inline(update, context, results, cache_time=0)
        elif query in ["дай песню", "песня", "музыка"]:
            tracks = pick_random_with_antirepeat(user_id, limit=20)
            for i, (fid, title) in enumerate(tracks):
                results.append(InlineQueryResultCachedAudio(id=f"rand_{i}_{fid[:20]}", audio_file_id=fid, caption=clean_title(title)))
            results.append(InlineQueryResultArticle(id="shuffle", title="Новый набор песен", description="Показать другие случайные треки", thumbnail_url=RAR_LOGO_URL, input_message_content=InputTextMessageContent("Новый набор"), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Новый набор", switch_inline_query_current_chat="дай песню")]])))
            await safe_answer_inline(update, context, results, cache_time=0)
        elif query.startswith("плейлист "):
            pl_name = query[9:].strip()
            if not pl_name:
                await update.inline_query.answer([], cache_time=0); return
            pl = get_playlist_by_name(pl_name)
            if not pl:
                await update.inline_query.answer([], cache_time=0); return
            pid, pname, owner_id, track_nums = pl
            if not track_nums:
                await update.inline_query.answer([], cache_time=0); return
            increment_playlist_search(pid)
            tracks = get_tracks_by_nums(track_nums)
            for i, (fid, title) in enumerate(tracks):
                rid = f"pl_{pid}_{i}_{int(time.time()*1000)}"
                inline_result_cache[rid] = (fid, time.time())
                results.append(InlineQueryResultCachedAudio(id=rid, audio_file_id=fid, caption=clean_title(title)))
            await safe_answer_inline(update, context, results, cache_time=0)
        elif query.startswith("найди ") or query.startswith("трек "):
            search = query[6:].strip()
            if not search:
                await update.inline_query.answer([], cache_time=0); return
            tracks = search_tracks_in_db(search, limit=20)
            for i, (fid, title, tnum) in enumerate(tracks):
                rid = f"find_{i}_{int(time.time()*1000)}_{fid[:10]}"
                inline_result_cache[rid] = (fid, time.time())
                results.append(InlineQueryResultCachedAudio(id=rid, audio_file_id=fid, caption=clean_title(title)))
            await safe_answer_inline(update, context, results, cache_time=0)
        elif query.startswith("монетка") or query.startswith("подкинь монетку"):
            coin_text = "Эээ... монетка встала ребром..." if random.randint(1, 50) == 50 else random.choice(answers_coin)
            results.append(InlineQueryResultArticle(id="coin", title="Бросить монетку", thumbnail_url=RAR_LOGO_URL, input_message_content=InputTextMessageContent(coin_text)))
            await safe_answer_inline(update, context, results, cache_time=0)
        else:
            tracks = search_tracks_in_db(query, limit=20)
            for i, (fid, title, tnum) in enumerate(tracks):
                rid = f"auto_{i}_{int(time.time()*1000)}_{fid[:10]}"
                inline_result_cache[rid] = (fid, time.time())
                results.append(InlineQueryResultCachedAudio(id=rid, audio_file_id=fid, caption=clean_title(title)))
            await safe_answer_inline(update, context, results, cache_time=0)
    except Exception as e:
        await log_to_owner(context, f"[INLINE OUTER] q='{query}'\n{e}\n{traceback.format_exc()[:800]}")
        try: await update.inline_query.answer([], cache_time=0)
        except Exception: pass


async def chosen_inline_result_handler(update, context):
    try:
        result = update.chosen_inline_result
        if not result: return
        rid = result.result_id
        if rid in inline_result_cache:
            fid, ts = inline_result_cache[rid]
            if time.time() - ts < INLINE_RESULT_CACHE_TTL:
                if rid.startswith("find_") or rid.startswith("auto_") or rid.startswith("pl_"):
                    increment_play(fid)
            del inline_result_cache[rid]
    except Exception as e:
        print(f"[CHOSEN INLINE ERROR] {e}")


def build_playlist_message(pid, index):
    pl = get_playlist_by_id(pid)
    if not pl: return None
    pid_, name, owner_id, track_nums = pl
    if not track_nums: return None
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("SELECT track_num, file_id, title FROM channel_music WHERE track_num = ANY(%s) AND inline_broken = FALSE", (track_nums,))
    rows = cursor.fetchall(); cursor.close(); conn.close()
    if not rows: return None
    ok_nums = [r[0] for r in rows]
    total = len(ok_nums); index = index % total
    tn = ok_nums[index]
    track = get_track_by_num(tn)
    if not track: return None
    fid, title = track
    text = f"Плейлист: <b>{html_escape(name)}</b>\n| {index+1}/{total} | {html_escape(clean_title(title))}"
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data=f"pl_{pid}_{(index-1) % total}"), InlineKeyboardButton("Далее", callback_data=f"pl_{pid}_{(index+1) % total}")]])
    return text, fid, kb


async def playlist_nav_handler(update, context):
    query = update.callback_query
    try: await query.answer()
    except Exception: pass
    m = re.match(r"^pl_(\d+)_(\d+)$", query.data)
    if not m: return
    pid = int(m.group(1)); index = int(m.group(2))
    built = build_playlist_message(pid, index)
    if not built:
        try: await query.edit_message_text("Плейлист пуст или удалён")
        except Exception: pass
        return
    text_msg, fid, kb = built
    try: await query.message.delete()
    except Exception: pass
    try: await context.bot.send_audio(chat_id=query.message.chat_id, audio=fid, caption=text_msg, reply_markup=kb, parse_mode="HTML")
    except Exception as e: await log_to_owner(context, f"[PL NAV ERROR] {e}")


async def handle_message(update, context):
    global rar_replies_history, does_replies_history, recent_tracks_history, ref_replies_history, hi_replies_history
    if not update.message or not update.effective_user: return
    if update.effective_user.is_bot: return
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username
    save_username(user_id, username)
    owner_id = get_owner_id()
    incoming = ""
    if update.message.text: incoming = update.message.text.lower().strip()
    elif update.message.caption: incoming = update.message.caption.lower().strip()
    text = update.message.text or ""
    clean = text.lower().strip()

    if chat_id < 0:
        txt_check = (update.message.text or "").strip().lower()
        is_command = txt_check.startswith("рар ") or txt_check.startswith("rar ") or txt_check == "рар" or txt_check == "rar"
        has_photo_only = update.message.photo and not update.message.caption
        if not is_command and not has_photo_only:
            try:
                conn = get_db_connection(); cursor = conn.cursor()
                cursor.execute("UPDATE sleuths SET current_count = current_count + 1 WHERE chat_id = %s AND target_id = %s", (chat_id, user_id))
                conn.commit()
                cursor.execute("SELECT required_count, current_count, target_name, admin_name, target_id, admin_id FROM sleuths WHERE chat_id = %s AND target_id = %s", (chat_id, user_id))
                row = cursor.fetchone(); cursor.close(); conn.close()
                if row and row[1] >= row[0]:
                    req, cur, tname, aname, tid, aid = row
                    sleuth_delete(chat_id, user_id)
                    t_display = f"@{tname}" if tname else f"ID {tid}"
                    a_display = f"@{aname}" if aname else f"ID {aid}"
                    try: await context.bot.send_message(chat_id=chat_id, text=f"Слежка завершена. {t_display} набрал {cur}/{req}. Наблюдатель: {a_display}.")
                    except Exception: pass
                    await log_to_owner(context, f"[SLEUTH DONE] {t_display} {cur}/{req} chat={chat_id}")
            except Exception as e:
                print(f"[SLEUTH COUNT ERROR] {e}")

    if incoming.upper() in DUEL_ACTIONS:
        handled = False
        for gid, game in list(duel_games.items()):
            if game["chat_id"] != chat_id or game["user_id"] != user_id: continue
            if not game.get("active"): continue
            if game.get("state") != "waiting_player": continue
            expected = game.get("expected")
            if incoming.upper() != expected:
                game["active"] = False
                duel_games.pop(gid, None)
                try:
                    await context.bot.send_message(chat_id=chat_id, text=f"<b>{random.choice(DUEL_FINAL_SOUNDS)}</b>\n– {random.choice(DUEL_RAR_WIN_PHRASES)}", parse_mode="HTML")
                except Exception: pass
                handled = True
                break
            game["state"] = "answered"
            game["last_action"] = time.time()
            asyncio.create_task(duel_rar_turn(context, game))
            handled = True
            break
        if handled: return

    rl_match = re.match(r"^(рар|rar)[,.]?\s+рулетка\s*$", text, re.IGNORECASE)
    if rl_match:
        await rl_start(update, context); return

    duel_match = re.match(r"^(рар|rar)[,.]?\s+(дуэль|перестрелка)(?:\s+(хард))?\s*$", text, re.IGNORECASE)
    if duel_match:
        hard = bool(duel_match.group(3))
        await duel_start(update, context, hard=hard); return

    sleuth_match = re.match(r"^(рар|rar)[,.]?\s+слежка\s+(.+)$", text, re.IGNORECASE)
    if sleuth_match:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_MID): return
        args_raw = sleuth_match.group(2).strip()
        actor_creator = await get_chat_creator(chat_id, context)
        actor_rank = get_admin_rank(chat_id, user_id)
        actor_is_creator = (actor_creator and user_id == actor_creator)
        target_user = None; rest = args_raw
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
        else:
            parts = args_raw.split(None, 1)
            if parts and parts[0].startswith("@"):
                target_user = await resolve_user_from_arg(context, chat_id, parts[0])
                rest = parts[1] if len(parts) > 1 else ""
        if not target_user:
            await update.message.reply_text("Ответь на сообщение или укажи @username."); return
        target_creator = await get_chat_creator(chat_id, context)
        if target_creator and target_user.id == target_creator:
            await update.message.reply_text("Нельзя вешать слежку на создателя чата!"); return
        target_rank = get_admin_rank(chat_id, target_user.id)
        if target_rank >= RANK_SENIOR and not actor_is_creator:
            await update.message.reply_text("Нельзя вешать слежку на старшего админа!"); return
        dur_match = re.search(r"((?:\d+\s*[дчмсДЧМС]\s*)+)", rest, re.IGNORECASE)
        if not dur_match:
            await update.message.reply_text("Укажи срок, например: 2д 12ч"); return
        duration_seconds = parse_duration(dur_match.group(1))
        if not duration_seconds:
            await update.message.reply_text("Не понял срок"); return
        rest_after_dur = rest[dur_match.end():].strip()
        count_match = re.search(r"(\d+)", rest_after_dur)
        if not count_match:
            await update.message.reply_text("Укажи норму, например: 150"); return
        required = int(count_match.group(1))
        action_part = rest_after_dur[count_match.end():].strip().lower()
        action = "none"
        if "бан" in action_part: action = "ban"
        elif "кик" in action_part: action = "kick"
        if action in ("ban", "kick") and not actor_is_creator and actor_rank < RANK_SENIOR:
            await update.message.reply_text("Бан/кик слежку может вешать только старший админ или создатель!"); return
        sleuth_create(chat_id, target_user.id, target_user.username, user_id, username, required, duration_seconds, action)
        t_display = f"@{target_user.username}" if target_user.username else f'<a href="tg://user?id={target_user.id}">{target_user.first_name}</a>'
        dur_str = format_duration(duration_seconds)
        await update.message.reply_text(f"Слежка начата. Цель: {t_display}. Норма: {required} за {dur_str}.", parse_mode="HTML")
        await log_to_owner(context, f"[SLEUTH START] target={target_user.id} by={user_id} req={required} dur={duration_seconds} action={action} chat={chat_id}")
        return

    unsleuth_match = re.match(r"^(рар|rar)[,.]?\s+(?:снять|сними)\s+слежку\s*(.*)$", text, re.IGNORECASE)
    if unsleuth_match:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        target_arg = unsleuth_match.group(2).strip(); target_user = None
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
        elif target_arg:
            target_user = await resolve_user_from_arg(context, chat_id, target_arg)
        if not target_user:
            await update.message.reply_text("Ответь на сообщение или укажи @username."); return
        row = sleuth_get(chat_id, target_user.id)
        if not row:
            await update.message.reply_text("Нет активной слежки на этого пользователя"); return
        admin_id = row[3]
        creator = await get_chat_creator(chat_id, context)
        if user_id != admin_id and not (creator and user_id == creator):
            await update.message.reply_text("Снять слежку может только тот, кто её повесил, или создатель чата."); return
        sleuth_delete(chat_id, target_user.id)
        t_display = f"@{target_user.username}" if target_user.username else f'<a href="tg://user?id={target_user.id}">{target_user.first_name}</a>'
        await update.message.reply_text(f"Слежка снята. Цель: {t_display}.", parse_mode="HTML")
        await log_to_owner(context, f"[SLEUTH CANCEL] target={target_user.id} by={user_id} chat={chat_id}")
        return

    if clean in ["рар слежки", "rar слежки", "рар, слежки", "rar, слежки"]:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_MID): return
        rows = sleuth_get_all(chat_id)
        if not rows:
            await update.message.reply_text("Активных слежек нет"); return
        lines = ["Активные слежки:"]
        for r in rows:
            sid, tid, tname, aid, aname, req, cur, dur, action, deadline = r
            left = deadline.timestamp() - time.time() if deadline else 0
            left_str = format_duration(int(max(0, left)))
            t_display = f"@{tname}" if tname else f"ID {tid}"
            act_str = f" [{action}]" if action != "none" else ""
            lines.append(f"{t_display} — {cur}/{req}{act_str}, осталось {left_str}")
        await update.message.reply_text("\n".join(lines)); return

    if clean in ["рар вкл калл", "rar вкл калл", "рар, вкл калл", "rar, вкл калл", "рар выкл калл", "rar выкл калл", "рар, выкл калл", "rar, выкл калл"]:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_SENIOR): return
        val = "вкл" in clean
        set_kall_enabled(chat_id, val)
        await update.message.reply_text("Калл включён!" if val else "Калл выключен!"); return

    admin_match = re.match(r"^(рар|rar)[,.]?\s+админ\b(?:\s+(\+\+|--|\+|-))?\s*(.*)$", text, re.IGNORECASE)
    if admin_match:
        action = admin_match.group(2); target_arg = admin_match.group(3).strip()
        target_user = None
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
        elif target_arg:
            target_user = await resolve_user_from_arg(context, chat_id, target_arg)
        if not target_user:
            await update.message.reply_text("Не нашла. Ответь на сообщение или укажи @username."); return
        if action is None:
            rank = get_admin_rank(chat_id, target_user.id)
            creator = await get_chat_creator(chat_id, context)
            if creator and target_user.id == creator:
                await update.message.reply_text(f"{target_user.first_name} – создатель чата"); return
            try:
                tg_member = await context.bot.get_chat_member(chat_id, target_user.id)
                tg_is_admin = tg_member.status in ADMIN_STATUSES
            except Exception: tg_is_admin = False
            line = f"{target_user.first_name} – {RANK_NAMES.get(rank, 'не админ')}"
            if tg_is_admin and rank == RANK_NONE:
                line += "\n(в Telegram админ, но в БД нет – напиши Рар админ + чтобы синхронизировать)"
            await update.message.reply_text(line, parse_mode="HTML"); return
        if chat_id >= 0:
            await update.message.reply_text("Эта команда работает только в группах"); return
        actor_creator = await get_chat_creator(chat_id, context)
        actor_rank = get_admin_rank(chat_id, user_id)
        actor_is_creator = (actor_creator and user_id == actor_creator)
        if not actor_is_creator and actor_rank < RANK_SENIOR:
            await update.message.reply_text("Не дорос еще! Недостаточно прав!!!"); return
        target_creator = await get_chat_creator(chat_id, context)
        if target_creator and target_user.id == target_creator:
            await update.message.reply_text("Нельзя трогать создателя чата!"); return
        if target_user.id == user_id:
            await update.message.reply_text("Себя нельзя!"); return
        target_rank = get_admin_rank(chat_id, target_user.id)
        if not actor_is_creator and target_rank >= RANK_SENIOR:
            await update.message.reply_text("Нельзя трогать старшего админа!"); return
        new_rank = target_rank
        if action == "+":
            if target_rank == RANK_NONE: new_rank = RANK_LOW
            else: await update.message.reply_text("Он уже админ. Используй ++."); return
        elif action == "++":
            if target_rank == RANK_NONE: await update.message.reply_text("Он не админ. Используй +."); return
            elif target_rank < RANK_SENIOR: new_rank = target_rank + 1
            else: await update.message.reply_text("Уже старший!"); return
        elif action == "-":
            if target_rank > RANK_LOW: new_rank = target_rank - 1
            else: await update.message.reply_text("Уже мл. Используй --."); return
        elif action == "--": new_rank = RANK_NONE
        try:
            bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
            if not getattr(bot_member, 'can_promote_members', False):
                await update.message.reply_text("У меня нет права назначать админов! Выдайте пожалуйста!!!"); return
        except Exception as e:
            await log_to_owner(context, f"[PROMOTE CHECK ERROR] {e}")
            await update.message.reply_text("Не смогла проверить права..."); return
        try:
            if new_rank == RANK_NONE:
                await context.bot.promote_chat_member(chat_id, target_user.id, can_delete_messages=False, can_restrict_members=False, can_invite_users=False, can_manage_chat=False, can_promote_members=False, can_change_info=False, can_pin_messages=False, can_manage_topics=False)
            elif new_rank == RANK_LOW:
                await context.bot.promote_chat_member(chat_id, target_user.id, can_delete_messages=True, can_restrict_members=False, can_invite_users=False, can_manage_chat=False, can_promote_members=False, can_change_info=False, can_pin_messages=False, can_manage_topics=False)
            elif new_rank == RANK_MID:
                await context.bot.promote_chat_member(chat_id, target_user.id, can_delete_messages=True, can_restrict_members=True, can_invite_users=True, can_manage_chat=False, can_promote_members=False, can_change_info=False, can_pin_messages=False, can_manage_topics=False)
            else:
                await context.bot.promote_chat_member(chat_id, target_user.id, can_delete_messages=True, can_restrict_members=True, can_invite_users=True, can_manage_chat=True, can_promote_members=False, can_change_info=True, can_pin_messages=True, can_manage_topics=True)
        except Exception as e:
            await log_to_owner(context, f"[PROMOTE ERROR] {e}")
            await update.message.reply_text(f"Не смогла изменить права: {e}"); return
        set_admin_rank(chat_id, target_user.id, new_rank, user_id)
        await update.message.reply_text(f"{target_user.first_name} теперь {RANK_NAMES.get(new_rank, 'не админ')}")
        await log_to_owner(context, f"[RANK] {target_user.first_name} -> {RANK_NAMES.get(new_rank, 'не админ')} (by {user_id})")
        return

    if incoming in ["рар админы", "rar админы", "рар, админы", "rar, админы"]:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        admins = get_all_admins(chat_id)
        creator = await get_chat_creator(chat_id, context)
        lines = ["Список админов Rar:\n"]
        if creator:
            try:
                u = await context.bot.get_chat(creator); lines.append(f"{u.first_name} – создатель")
            except Exception: lines.append(f"ID {creator} – создатель")
        for uid, rank in admins:
            try:
                u = await context.bot.get_chat(uid); lines.append(f"{u.first_name} – {RANK_NAMES.get(rank, '?')}")
            except Exception: lines.append(f"ID {uid} – {RANK_NAMES.get(rank, '?')}")
        if len(lines) == 1: lines.append("Пока никого")
        await update.message.reply_text("\n".join(lines)); return

    ban_match = re.match(r"^(рар|rar)[,.]?\s+бан\s*(.*)$", text, re.IGNORECASE)
    if ban_match:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_SENIOR): return
        target_user = None; reason = ban_match.group(2).strip()
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
        elif reason:
            parts = reason.split(None, 1); target_arg = parts[0]
            reason = parts[1] if len(parts) > 1 else ""
            target_user = await resolve_user_from_arg(context, chat_id, target_arg)
        if not target_user:
            await update.message.reply_text("Ответь или укажи @username"); return
        creator = await get_chat_creator(chat_id, context)
        if creator and target_user.id == creator:
            await update.message.reply_text("Нельзя банить создателя!"); return
        try:
            await context.bot.ban_chat_member(chat_id, target_user.id)
            msg = f"{target_user.first_name} забанен"
            if reason: msg += f". Причина: {reason}"
            await update.message.reply_text(msg)
            await log_to_owner(context, f"[BAN] {target_user.first_name} [ID: {target_user.id}] Чат: {chat_id}. Причина: {reason or '-'}")
        except Exception as e:
            await update.message.reply_text("У меня недостаточно прав для бана, выдайте пожалуйста!!!")
            await log_to_owner(context, f"[BAN ERROR] {e}")
        return

    unban_match = re.match(r"^(рар|rar)[,.]?\s+разбан\s*(.*)$", text, re.IGNORECASE)
    if unban_match:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_SENIOR): return
        target_arg = unban_match.group(2).strip(); target_user = None
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
        elif target_arg:
            target_user = await resolve_user_from_arg(context, chat_id, target_arg)
        if not target_user:
            await update.message.reply_text("Ответь или укажи @username"); return
        try:
            await context.bot.unban_chat_member(chat_id, target_user.id, only_if_banned=True)
            await update.message.reply_text(f"{target_user.first_name} разбанен")
        except Exception as e:
            await update.message.reply_text("Не получилось разбанить")
            await log_to_owner(context, f"[UNBAN ERROR] {e}")
        return

    kick_match = re.match(r"^(рар|rar)[,.]?\s+кик\s*(.*)$", text, re.IGNORECASE)
    if kick_match:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_SENIOR): return
        target_user = None
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
        else:
            target_arg = kick_match.group(2).strip()
            if target_arg:
                target_user = await resolve_user_from_arg(context, chat_id, target_arg)
        if not target_user:
            await update.message.reply_text("Ответь или укажи @username"); return
        creator = await get_chat_creator(chat_id, context)
        if creator and target_user.id == creator:
            await update.message.reply_text("Нельзя кикать создателя!"); return
        try:
            await context.bot.ban_chat_member(chat_id, target_user.id)
            await context.bot.unban_chat_member(chat_id, target_user.id)
            await update.message.reply_text(f"{target_user.first_name} кикнут")
        except Exception as e:
            await update.message.reply_text("Не получилось кикнуть")
            await log_to_owner(context, f"[KICK ERROR] {e}")
        return

    mute_match = re.match(r"^(рар|rar)[,.]?\s+мут\s+(.+)$", text, re.IGNORECASE)
    if mute_match:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_MID): return
        args = mute_match.group(2).strip(); target_user = None; duration_seconds = None
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
            duration_seconds = parse_duration(args)
        else:
            parts = args.split(None, 1); target_arg = parts[0]
            dur_text = parts[1] if len(parts) > 1 else ""
            duration_seconds = parse_duration(dur_text)
            target_user = await resolve_user_from_arg(context, chat_id, target_arg)
        if not target_user:
            await update.message.reply_text("Ответь или укажи @username"); return
        if duration_seconds is None: duration_seconds = 600
        until = int(time.time()) + duration_seconds
        try:
            await context.bot.restrict_chat_member(chat_id, target_user.id, permissions=ChatPermissions(can_send_messages=False), until_date=until)
            await update.message.reply_text(f"{target_user.first_name} замучен на {format_duration(duration_seconds)}")
        except Exception as e:
            await update.message.reply_text("Не получилось замутить")
            await log_to_owner(context, f"[MUTE ERROR] {e}")
        return

    unmute_match = re.match(r"^(рар|rar)[,.]?\s+размут\s*(.*)$", text, re.IGNORECASE)
    if unmute_match:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_MID): return
        target_user = None
        if update.message.reply_to_message and update.message.reply_to_message.from_user:
            target_user = update.message.reply_to_message.from_user
        else:
            target_arg = unmute_match.group(2).strip()
            if target_arg:
                target_user = await resolve_user_from_arg(context, chat_id, target_arg)
        if not target_user:
            await update.message.reply_text("Ответь или укажи @username"); return
        try:
            await context.bot.restrict_chat_member(chat_id, target_user.id, permissions=ChatPermissions(can_send_messages=True, can_send_audios=True, can_send_documents=True, can_send_photos=True, can_send_videos=True, can_send_video_notes=True, can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True, can_invite_users=True))
            await update.message.reply_text(f"{target_user.first_name} размучен")
        except Exception as e:
            await update.message.reply_text("Не получилось размутить")
            await log_to_owner(context, f"[UNMUTE ERROR] {e}")
        return

    add_to_pl = re.match(r"^(рар|rar)[,.]?\s+добав(ь|ить)\s+в\s+(.+)$", text, re.IGNORECASE)
    if add_to_pl:
        pl_name = add_to_pl.group(3).strip(); target = None
        if update.message.reply_to_message and update.message.reply_to_message.audio:
            target = update.message.reply_to_message.audio
        elif update.message.audio: target = update.message.audio
        if not target:
            await update.message.reply_text(f'Прикрепи аудио или ответь "Рар добавь в {pl_name}"'); return
        pl = get_playlist_by_name(pl_name)
        if not pl:
            await update.message.reply_text(f"Не нашла плейлист <b>{html_escape(pl_name)}</b>. Создай: <code>Рар создай плейлист {html_escape(pl_name)}</code>", parse_mode="HTML"); return
        pid, pname, pl_owner, track_nums = pl
        if user_id != pl_owner and user_id != owner_id:
            await update.message.reply_text("Это не твой плейлист! Нельзя так делать!!!"); return
        perf = target.performer.strip() if target.performer else ""
        title = target.title.strip() if target.title else ""
        track_title = f"{perf} - {title}" if perf and title else (target.file_name or "Неизвестный трек")
        tn = get_track_num(target.file_id)
        if not tn:
            existing = search_track_in_db_by_title(track_title)
            if existing: tn = existing[2]
            else:
                save_track_to_db(target.file_id, track_title, user_id); tn = get_track_num(target.file_id)
        if not tn:
            await update.message.reply_text("Что-то пошло не так"); return
        result = add_track_to_playlist(pid, tn)
        if result == "already": await update.message.reply_text("Вообще то этот трек уже в плейлисте")
        elif result == "limit": await update.message.reply_text(f"Максимум {PLAYLIST_LIMIT} песен!")
        elif result == "ok": await update.message.reply_text(f"Добавила в плейлист <b>{html_escape(pname)}</b>!", parse_mode="HTML")
        return

    if re.match(r"^(рар|rar)[,.]?\s+добав(ь|ить)$", incoming) or incoming in ["добавь", "добавить"]:
        target = None
        if update.message.reply_to_message and update.message.reply_to_message.audio:
            target = update.message.reply_to_message.audio
        elif update.message.audio: target = update.message.audio
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
                    except Exception: pass
                try:
                    await context.bot.send_audio(chat_id=chat_id, audio=target.file_id, caption=f"Занесла трек в коллекцию.\n\nИмя в базе: {track_title}")
                except Exception: pass
            else: await update.message.reply_text(f"Этот трек уже сохранён: {track_title}")
        else: await update.message.reply_text('Прикрепи аудио или ответь "Рар добавь"')
        return

    if not text: return

    if clean in ["рар команды", "rar команды", "рар, команды", "rar, команды"]:
        await update.message.reply_text(
            "<b>Команды Rar:</b>\n\n<b>Музыка:</b>\n"
            "• <code>Рар добавь</code> / <code>Рар добавить</code>\n"
            "• <code>Рар дай песню</code>\n"
            "• <code>Рар найди</code> / <code>Рар найти</code> [название]\n"
            "• <code>Рар топ песен</code>\n\n<b>Плейлисты:</b>\n"
            "• <code>Рар создай плейлист</code> [название]\n"
            "• <code>Рар добавь в</code> [плейлист]\n"
            "• <code>Рар удали</code> [песня] <code>из</code> [плейлист]\n"
            "• <code>Рар удали</code> [плейлист]\n"
            "• <code>Рар переименуй</code> [X] <code>в</code> [Y]\n"
            "• <code>Рар мои плейлисты</code>\n"
            "• <code>Рар дай плейлист</code>\n"
            "• <code>Рар найди плейлист</code> [название]\n"
            "• <code>Рар топ плейлистов</code>\n\n<b>Игры:</b>\n"
            "• <code>Рар рулетка</code>\n"
            "• <code>Рар дуэль</code> / <code>Рар перестрелка</code>\n"
            "• <code>Рар дуэль хард</code>\n\n<b>Слежка:</b>\n"
            "• <code>Рар слежка</code> @user [срок] [норма] [кик/бан]\n"
            "• <code>Рар снять слежку</code> @user\n"
            "• <code>Рар слежки</code>\n\n<b>Админ-система:</b>\n"
            "• <code>Рар вкл/выкл калл</code> (ст.)\n"
            "• <code>Рар админ</code> @user\n"
            "• <code>Рар админ +</code> @user (ст.)\n"
            "• <code>Рар админ ++</code> / <code>Рар админ -</code> / <code>Рар админ --</code>\n"
            "• <code>Рар админы</code>\n"
            "• <code>Рар бан</code> @user [причина] (ст.)\n"
            "• <code>Рар разбан</code> @user (ст.)\n"
            "• <code>Рар кик</code> @user (ст.)\n"
            "• <code>Рар мут</code> @user [время] (ср.)\n"
            "• <code>Рар размут</code> @user (ср.)\n\n<b>Настройки (ст. админы):</b>\n"
            "• <code>Рар вкл/выкл приветствие</code>\n"
            "• <code>Рар вкл/выкл прощание</code>\n"
            "• <code>Рар измени приветствие/прощание</code> [текст]\n\n"
            "<b>Инлайн:</b> <code>@ChRarBot</code> / <code>дай песню</code> / <code>найди X</code> / <code>плейлист X</code> / <code>монетка</code>",
            parse_mode="HTML"); return

    if clean in ["рар проблемные", "rar проблемные"]:
        if user_id != owner_id:
            await update.message.reply_text("Только для создателя!"); return
        broken = get_broken_tracks()
        if not broken:
            await update.message.reply_text("Проблемных треков нет!"); return
        lines = [f"Проблемные ({len(broken)}):\n"]
        for tn, title in broken[:30]: lines.append(f"№{tn}: {title}")
        if len(broken) > 30: lines.append(f"...и ещё {len(broken) - 30}")
        await update.message.reply_text("\n".join(lines)); return

    create_pl = re.match(r"^(рар|rar)[,.]?\s+созда(й|ть)\s+плейлист\s+(.+)$", text, re.IGNORECASE)
    if create_pl:
        pl_name = create_pl.group(3).strip()
        pid, err = create_playlist(pl_name, user_id)
        if err == "exists":
            await update.message.reply_text("Такое название уже есть!"); return
        if err:
            await update.message.reply_text(f"Ошибка: {err}"); return
        await update.message.reply_text(f"Плейлист <b>{html_escape(pl_name)}</b> создан!", parse_mode="HTML")
        uname = f"@{username}" if username else (update.effective_user.first_name or "друг")
        await notify_moderation(context, f"Создан плейлист\n{html_escape(pl_name)}\nКто: {html_escape(uname)} [ID: {user_id}]")
        return

    if clean in ["рар мои плейлисты", "rar мои плейлисты", "рар, мои плейлисты", "rar, мои плейлисты"]:
        pls = get_user_playlists(user_id)
        if not pls:
            await update.message.reply_text("Нет плейлистов. Создай: <code>Рар создай плейлист Мой топ</code>", parse_mode="HTML"); return
        lines = ["Твои плейлисты:\n"]
        for pid, name, track_nums in pls: lines.append(f"{name} – {len(track_nums or [])} треков")
        await update.message.reply_text("\n".join(lines)); return

    if clean in ["рар дай плейлист", "rar дай плейлист", "рар, дай плейлист", "rar, дай плейлист"]:
        result = get_random_playlist_with_tracks(user_id)
        if not result:
            await update.message.reply_text("Нет плейлистов с треками"); return
        pid, pname, pl_owner, track_nums = result
        built = build_playlist_message(pid, 0)
        if not built:
            await update.message.reply_text("Ошибка"); return
        text_msg, fid, kb = built
        try: await context.bot.send_audio(chat_id=chat_id, audio=fid, caption=text_msg, reply_markup=kb, parse_mode="HTML")
        except Exception as e: await log_to_owner(context, f"[PL SEND ERROR] {e}")
        return

    find_pl = re.match(r"^(рар|rar)[,.]?\s+най(ди|ти)\s+плейлист\s+(.+)$", text, re.IGNORECASE)
    if find_pl:
        pl_name = find_pl.group(3).strip()
        pl = get_playlist_by_name(pl_name)
        if not pl:
            await update.message.reply_text(f"Не нашла <b>{html_escape(pl_name)}</b>", parse_mode="HTML"); return
        pid, pname, pl_owner, track_nums = pl
        if not track_nums:
            await update.message.reply_text("Плейлист пуст!"); return
        increment_playlist_search(pid)
        built = build_playlist_message(pid, 0)
        if not built:
            await update.message.reply_text("Ошибка"); return
        text_msg, fid, kb = built
        try: await context.bot.send_audio(chat_id=chat_id, audio=fid, caption=text_msg, reply_markup=kb, parse_mode="HTML")
        except Exception as e: await log_to_owner(context, f"[PL SEND ERROR] {e}")
        return

    if clean in ["рар топ плейлистов", "rar топ плейлистов"]:
        top = get_top_playlists(limit=10)
        if not top:
            await update.message.reply_text("Топ пуст!"); return
        lines = ["\nТоп 10 плейлистов\n"]
        for i, (name, searches) in enumerate(top, 1):
            lines.append(f"{i}. {name} ({searches})")
        await update.message.reply_text("\n".join(lines)); return

    del_from = re.match(r"^(рар|rar)[,.]?\s+удал(и|ить)\s+(.+?)\s+из\s+(?:плейлист[а]?\s+)?(.+)$", text, re.IGNORECASE)
    if del_from:
        track_query = del_from.group(3).strip(); pl_name = del_from.group(4).strip()
        pl = get_playlist_by_name(pl_name)
        if not pl:
            await update.message.reply_text(f"Не нашла <b>{html_escape(pl_name)}</b>", parse_mode="HTML"); return
        pid, pname, pl_owner, track_nums = pl
        if user_id != pl_owner and user_id != owner_id:
            await update.message.reply_text("Да что ты себе позволяешь?! Не твой плейлист!!!!!!"); return
        if not track_nums:
            await update.message.reply_text("Плейлист пуст"); return
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT track_num, title FROM channel_music WHERE track_num = ANY(%s) AND LOWER(title) LIKE LOWER(%s)", (track_nums, f"%{track_query}%"))
        found = cursor.fetchone(); cursor.close(); conn.close()
        if not found:
            await update.message.reply_text(f"Не нашла {track_query} в {pl_name}"); return
        found_tn, found_title = found
        remove_track_from_playlist(pid, found_tn)
        await update.message.reply_text(f"Убрала {found_title} из {pl_name}"); return

    del_pl = re.match(r"^(рар|rar)[,.]?\s+удал(и|ить)\s+(?:плейлист\s+)?(.+)$", text, re.IGNORECASE)
    if del_pl:
        pl_name = del_pl.group(3).strip()
        pl = get_playlist_by_name(pl_name)
        if not pl:
            await update.message.reply_text(f"Не нашла <b>{html_escape(pl_name)}</b>", parse_mode="HTML"); return
        pid, pname, pl_owner, track_nums = pl
        if user_id != pl_owner and user_id != owner_id:
            await update.message.reply_text("Да что ты себе позволяешь?! Не твой плейлист!!!!!!"); return
        delete_playlist(pid)
        await update.message.reply_text(f"Плейлист <b>{html_escape(pname)}</b> удалён", parse_mode="HTML")
        uname = f"@{username}" if username else (update.effective_user.first_name or "друг")
        await notify_moderation(context, f"Удалён плейлист\n{pname}\nКто: {uname}")
        return

    ren_pl = re.match(r"^(рар|rar)[,.]?\s+переимену(й|ть)\s+(?:плейлист\s+)?(.+?)\s+в\s+(.+)$", text, re.IGNORECASE)
    if ren_pl:
        old_name = ren_pl.group(3).strip(); new_name = ren_pl.group(4).strip()
        pl = get_playlist_by_name(old_name)
        if not pl:
            await update.message.reply_text(f"Не нашла <b>{html_escape(old_name)}</b>", parse_mode="HTML"); return
        pid, pname, pl_owner, track_nums = pl
        if user_id != pl_owner and user_id != owner_id:
            await update.message.reply_text("Да что ты себе позволяешь?! Не твой плейлист!!!!!!"); return
        err = rename_playlist(pid, new_name)
        if err == "exists":
            await update.message.reply_text("Такое название уже есть!"); return
        await update.message.reply_text(f"Переименован: <b>{html_escape(old_name)}</b> – <b>{html_escape(new_name)}</b>", parse_mode="HTML")
        uname = f"@{username}" if username else (update.effective_user.first_name or "друг")
        await notify_moderation(context, f"Переименован плейлист\n{old_name} -> {new_name}\nКто: {uname}")
        return

    if clean in ["рар удали трек", "rar удали трек"]:
        if user_id != owner_id:
            await update.message.reply_text("Только для создателя!"); return
        if not update.message.reply_to_message or not update.message.reply_to_message.audio:
            await update.message.reply_text("Ответь на аудио"); return
        audio = update.message.reply_to_message.audio
        perf = audio.performer.strip() if audio.performer else ""
        title = audio.title.strip() if audio.title else ""
        fallback = f"{perf} - {title}" if perf and title else (audio.file_name or None)
        deleted = delete_track_from_db(audio.file_id, fallback)
        if deleted: await update.message.reply_text(f"Трек удалён: {deleted}")
        else: await update.message.reply_text("Не найден")
        return

    if clean in ["рар топ песен", "rar топ песен", "рар топ песни", "rar топ песни"]:
        top = get_top_tracks(limit=10)
        if not top:
            await update.message.reply_text("Топ пуст!"); return
        lines = ["\nТоп 10 треков\n"]
        for i, (title, plays) in enumerate(top, 1):
            lines.append(f"{i}. {title} ({plays})")
        await update.message.reply_text("\n".join(lines)); return

    toggle_map = {
        "рар вкл приветствие": ("greet_enabled", True, "Приветствие включено!"),
        "рар выкл приветствие": ("greet_enabled", False, "Приветствие выключено!"),
        "rar вкл приветствие": ("greet_enabled", True, "Приветствие включено!"),
        "rar выкл приветствие": ("greet_enabled", False, "Приветствие выключено!"),
        "рар вкл прощание": ("farewell_enabled", True, "Прощание включено!"),
        "рар выкл прощание": ("farewell_enabled", False, "Прощание выключено!"),
        "rar вкл прощание": ("farewell_enabled", True, "Прощание включено!"),
        "rar выкл прощание": ("farewell_enabled", False, "Прощание выключено!"),
    }
    if clean in toggle_map:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_SENIOR): return
        field, val, msg = toggle_map[clean]
        update_chat_setting(chat_id, field, val)
        await update.message.reply_text(msg); return

    matched_greet = next((p for p in GREET_PREFIXES if clean == p or clean.startswith(p + " ")), None)
    matched_fare = next((p for p in FAREWELL_PREFIXES if clean == p or clean.startswith(p + " ")), None)
    if matched_greet or matched_fare:
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_SENIOR): return
        matched = matched_greet or matched_fare
        new_text = text.strip()[len(matched):].strip()
        if not new_text:
            await update.message.reply_text("Напиши текст!"); return
        if matched_greet:
            update_chat_setting(chat_id, "greet_text", new_text)
            await update.message.reply_text(f"Приветствие обновлено:\n\n{new_text}")
        else:
            update_chat_setting(chat_id, "farewell_text", new_text)
            await update.message.reply_text(f"Прощание обновлено:\n\n{new_text}")
        return

    if clean in ["rar", "рар"]:
        if chat_id not in rar_replies_history: rar_replies_history[chat_id] = []
        avail = [a for a in answers_rar if a not in rar_replies_history[chat_id]] or answers_rar
        r = random.choice(avail)
        rar_replies_history[chat_id].append(r)
        if len(rar_replies_history[chat_id]) > 2: rar_replies_history[chat_id].pop(0)
        await update.message.reply_text(r); return

    if clean in ["рар, подкинь монетку","rar, подкинь монетку","рар подкинь монетку","rar подкинь монетку","рар, монетка","rar, монетка","рар монетка","rar монетка"]:
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

    if clean in ["rar, что делаешь?","рар, что делаешь?","rar что делаешь?","рар что делаешь?"]:
        if chat_id not in does_replies_history: does_replies_history[chat_id] = []
        avail = [a for a in answers_does if a not in does_replies_history[chat_id]] or answers_does
        r = random.choice(avail)
        does_replies_history[chat_id].append(r)
        if len(does_replies_history[chat_id]) > 2: does_replies_history[chat_id].pop(0)
        await update.message.reply_text(r); return

    if clean in ["rar дай песню","рар дай песню","rar дай музыку","рар дай музыку"]:
        try:
            tracks = get_all_tracks_from_db(only_ok=True)
            if not tracks:
                await update.message.reply_text("Коллекция пуста!"); return
            if chat_id not in recent_tracks_history or not isinstance(recent_tracks_history[chat_id], list):
                recent_tracks_history[chat_id] = []
            avail = [t for t in tracks if t not in recent_tracks_history[chat_id]] or tracks
            if not avail: recent_tracks_history[chat_id] = []; avail = tracks
            fid, ttitle = random.choice(avail)
            recent_tracks_history[chat_id].append(fid)
            lim = max(5, len(tracks) // 3)
            if len(recent_tracks_history[chat_id]) > lim: recent_tracks_history[chat_id].pop(0)
            try: await context.bot.send_audio(chat_id=chat_id, audio=fid, caption=f"Вот ваша песня.\n\n{ttitle}")
            except Exception as e: print(f"[SEND AUDIO] {e}")
        except Exception as e: await update.message.reply_text(f"Ошибка: {e}")
        return

    if clean == "калл":
        if chat_id >= 0:
            await update.message.reply_text("Только в группах"); return
        if not get_kall_enabled(chat_id):
            await update.message.reply_text("Калл отключён."); return
        if not await check_user_rank(update, context, chat_id, user_id, RANK_MID): return
        try:
            user_ids = get_chat_members(chat_id)
            if not user_ids:
                await update.message.reply_text("Список пуст"); return
            valid = [STATUS_MEMBER, STATUS_ADMINISTRATOR, STATUS_CREATOR, STATUS_RESTRICTED]
            tags, to_remove = [], []
            for m_id in user_ids:
                m_id_int = int(m_id)
                if m_id_int in (int(context.bot.id), GROUP_ANON_BOT_ID): continue
                try: member = await context.bot.get_chat_member(chat_id, m_id_int)
                except Exception as e:
                    err = str(e).lower()
                    if "not found" in err or "participant not found" in err: to_remove.append(m_id_int)
                    continue
                if member.status == STATUS_LEFT: to_remove.append(m_id_int); continue
                if member.status in (STATUS_KICKED, STATUS_BANNED): continue
                if member.status not in valid: continue
                uname = member.user.username
                fname = member.user.first_name or "друг"
                display = f"@{uname}" if uname else fname
                tags.append(f'<a href="tg://user?id={m_id_int}">{html_escape(display)}</a>')
            if to_remove:
                try:
                    remove_users_from_chat_batch(to_remove, chat_id)
                    for uid in to_remove: uncache_user(uid, chat_id)
                except Exception: pass
            if not tags:
                await update.message.reply_text("Нет активных"); return
            for i in range(0, len(tags), 6):
                chunk = tags[i:i+6]
                try: await update.message.reply_text("<b>Минуточку внимания!</b>\n\n" + "\n".join(chunk), parse_mode="HTML")
                except Exception: pass
        except Exception as e: await log_to_owner(context, f"[КАЛЛ FATAL] {e}")
        return

    find_track = re.match(r"^(рар|rar)[,.]?\s+най(ди|ти)\s+(.+)$", text, re.IGNORECASE)
    if find_track:
        query = find_track.group(3).strip()
        if not query:
            await update.message.reply_text("Напиши название"); return
        status_msg = await update.message.reply_text("Ищу...")
        local_track = search_track_in_db(query)
        if local_track:
            fid, ttitle, tnum = local_track
            await status_msg.delete()
            try:
                await context.bot.send_audio(chat_id=chat_id, audio=fid, caption=f"Нашла: {ttitle}\n\nЗапрос: {query}")
                increment_play(fid)
            except Exception as e: print(f"[FIND SEND] {e}")
            return
        else: await status_msg.edit_text("Не нашла такую песню")


async def handle_chat_member(update, context):
    try:
        result = update.chat_member
        if not result: return
        user = result.new_chat_member.user
        chat_id = result.chat.id
        new_status = result.new_chat_member.status
        old_status = result.old_chat_member.status
        if user.is_bot or chat_id >= 0: return
        if user.id == GROUP_ANON_BOT_ID: return
        greet_enabled, farewell_enabled, greet_text, farewell_text = get_chat_settings(chat_id)
        try:
            chat = await context.bot.get_chat(chat_id)
            chat_title = chat.title or "этот чат"
        except Exception: chat_title = "этот чат"
        user_name = user.first_name or "друг"
        if old_status in LEAVE_STATUSES and new_status in IN_CHAT_STATUSES:
            try: save_user_to_chat(user.id, chat_id); cache_user(user.id, chat_id)
            except Exception: pass
            if greet_enabled:
                try: await context.bot.send_message(chat_id=chat_id, text=substitute_vars(greet_text, user_name, chat_title))
                except Exception: pass
        elif old_status in IN_CHAT_STATUSES and new_status in LEAVE_STATUSES:
            try: remove_user_from_chat(user.id, chat_id); uncache_user(user.id, chat_id)
            except Exception: pass
            if farewell_enabled:
                try: await context.bot.send_message(chat_id=chat_id, text=substitute_vars(farewell_text, user_name, chat_title))
                except Exception: pass
    except Exception as e: print(f"[CHAT_MEMBER FATAL] {e}")


async def handle_my_chat_member(update, context):
    try:
        result = update.my_chat_member
        if not result: return
        chat = result.chat; chat_id = chat.id
        new_status = result.new_chat_member.status
        old_status = result.old_chat_member.status
        if chat_id >= 0: return
        if new_status in LEAVE_STATUSES:
            try: remove_chat_data(chat_id); _creator_cache.pop(chat_id, None)
            except Exception: pass
            return
        if old_status not in ADMIN_STATUSES and new_status in ADMIN_STATUSES:
            now = time.time()
            if chat_id in _recent_greets and now - _recent_greets[chat_id] < GREET_DEDUP_TTL:
                try: await context.bot.send_message(chat_id=chat_id, text="Спасибо, теперь могу работать")
                except Exception: pass
                return
            try: await context.bot.send_message(chat_id=chat_id, text="Спасибо, теперь могу работать")
            except Exception: pass
            return
        if old_status in ADMIN_STATUSES and new_status not in ADMIN_STATUSES:
            try: await context.bot.send_message(chat_id=chat_id, text="Эй! Верните мне админа! Я же так сломаться могу!!!")
            except Exception: pass
            return
        if old_status not in IN_CHAT_STATUSES and new_status in IN_CHAT_STATUSES:
            _recent_greets[chat_id] = time.time()
            _creator_cache.pop(chat_id, None)
            try:
                admins = await context.bot.get_chat_administrators(chat_id)
                for admin in admins:
                    if admin.user.is_bot: continue
                    if admin.user.id == GROUP_ANON_BOT_ID: continue
                    save_user_to_chat(admin.user.id, chat_id); cache_user(admin.user.id, chat_id)
            except Exception: pass
            if new_status in ADMIN_STATUSES:
                try: await context.bot.send_message(chat_id=chat_id, text="Спасибо, теперь могу работать")
                except Exception: pass
            else:
                text = ("Здравствуйте! Я Rar.\n\nДля работы дайте мне права админа, пожалуйста!\n\n"
                        "Команды: <code>Рар команды</code>")
                try: await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
                except Exception: pass
    except Exception as e: print(f"[MY_CHAT_MEMBER FATAL] {e}")


async def sleuth_deadline_loop(app):
    await asyncio.sleep(90)
    while True:
        try:
            rows = sleuth_get_expired()
            for r in rows:
                sid, chat_id, target_id, tname, admin_id, aname, req, cur, dur, action = r
                sleuth_delete_by_id(sid)
                t_display = f"@{tname}" if tname else f"ID {target_id}"
                a_display = f"@{aname}" if aname else f"ID {admin_id}"
                dur_str = format_duration(dur)
                msg = f"Слежка завершена. {t_display} набрал {cur}/{req} за {dur_str}. Наблюдатель: {a_display}."
                if action == "kick":
                    try:
                        await app.bot.ban_chat_member(chat_id, target_id)
                        await app.bot.unban_chat_member(chat_id, target_id)
                        msg += " Исключён."
                    except Exception as e:
                        msg += " Не смогла кикнуть (мало прав)."
                        print(f"[SLEUTH KICK ERROR] {e}")
                elif action == "ban":
                    try:
                        await app.bot.ban_chat_member(chat_id, target_id)
                        msg += " Забанен."
                    except Exception as e:
                        msg += " Не смогла забанить (мало прав)."
                        print(f"[SLEUTH BAN ERROR] {e}")
                try: await app.bot.send_message(chat_id=chat_id, text=msg)
                except Exception: pass
                try:
                    owner_id = get_owner_id()
                    if owner_id:
                        await app.bot.send_message(chat_id=owner_id, text=f"<b>LOG</b>\n<code>[SLEUTH END] {msg}</code>", parse_mode="HTML")
                except Exception: pass
        except Exception as e:
            print(f"[SLEUTH LOOP ERROR] {e}")
        await asyncio.sleep(60)


async def daily_track_loop(app):
    await asyncio.sleep(60)
    while True:
        try:
            notify = get_notify_chat_id()
            if notify:
                tracks = get_all_tracks_from_db(only_ok=True)
                if tracks:
                    fid, title = random.choice(tracks)
                    try: await app.bot.send_audio(chat_id=notify, audio=fid, caption=f"Ежедневный трек:\n{title}")
                    except Exception: pass
        except Exception: pass
        await asyncio.sleep(86400)


async def keep_database_alive():
    await asyncio.sleep(30)
    while True:
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("SELECT 1;"); cursor.fetchone()
            cursor.close(); conn.close()
        except Exception as e: print(f"=== [PING ERROR] {e} ===")
        await asyncio.sleep(21600)


async def error_handler(update, context):
    err_text = str(context.error)
    tb = "".join(traceback.format_exception(type(context.error), context.error, context.error.__traceback__))[:1500]
    print(f"[ERROR HANDLER] {err_text}\n{tb}")
    try:
        owner_id = get_owner_id()
        if owner_id:
            is_network = any(kw in err_text for kw in ["httpx", "httpcore", "ReadTimeout", "ConnectTimeout", "ConnectionError", "NetworkError", "TimedOut"])
            if is_network:
                print(f"[NETWORK ERROR] {err_text}"); return
            is_noise = ("bot was kicked from the group chat" in err_text
                        or "bot was blocked by the user" in err_text
                        or "chat not found" in err_text)
            prefix = "NOISE" if is_noise else "ERROR"
            await context.bot.send_message(chat_id=owner_id, text=f"{prefix}\n<code>{err_text[:600]}</code>\n\n<pre>{tb[:1500]}</pre>", parse_mode="HTML")
    except Exception as e: print(f"[ERROR HANDLER SEND FAIL] {e}")


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
    asyncio.create_task(rl_timeout_loop(application))
    asyncio.create_task(sleuth_deadline_loop(application))


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
    app.add_handler(ChosenInlineResultHandler(chosen_inline_result_handler))
    app.add_handler(CallbackQueryHandler(playlist_nav_handler, pattern=r"^pl_\d+_\d+$"))
    app.add_handler(CallbackQueryHandler(rl_callback, pattern=r"^rl_"))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    print("Запуск бота...")
    app.run_polling(allowed_updates=["message", "chat_member", "my_chat_member", "inline_query", "chosen_inline_result", "callback_query"])


if __name__ == "__main__":
    main()
