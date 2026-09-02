import aiohttp
import os
import random
import re
import asyncio
import psycopg2
from urllib.parse import urlparse  # Добавляем стандартный инструмент разбора ссылок
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

def get_db_connection():
    # Безопасно разбираем скрытую ссылку из Render на части для psycopg2
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
        CREATE TABLE IF NOT EXISTS channel_music (
            file_id TEXT PRIMARY KEY,
            title TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS left_stats (
            chat_id NUMERIC PRIMARY KEY,
            count_left INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()

async def keep_database_alive():
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1;")
            cursor.fetchone()
            cursor.close()
            conn.close()
            print("=== [PING] Ok ===")
        except Exception as e:
            print(f"=== [PING ERROR] {e} ===")
        await asyncio.sleep(86400)
def escape_markdown(text: str) -> str:
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', text)

def save_user(chat_id: int, user_id: int, username: str, first_name: str):
    if chat_id >= 0:
        return
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
    cursor.execute("""
        INSERT INTO left_stats (chat_id, count_left) VALUES (%s, 1)
        ON CONFLICT (chat_id) DO UPDATE SET count_left = left_stats.count_left + 1
    """, (chat_id,))
    conn.commit()
    cursor.close()
    conn.close()
def get_left_count(chat_id: int) -> int:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT count_left FROM left_stats WHERE chat_id = %s", (chat_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row if row else 0

def get_chat_members(chat_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, first_name FROM users WHERE chat_id = %s", (chat_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return rows

def save_track_to_db(file_id: str, title: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM channel_music WHERE file_id = %s OR LOWER(title) = LOWER(%s) LIMIT 1", (file_id, title))
    exists = cursor.fetchone()
    if exists:
        cursor.close()
        conn.close()
        return False
    cursor.execute("INSERT INTO channel_music (file_id) VALUES (%s, %s) ON CONFLICT (file_id) DO NOTHING", (file_id, title))
    conn.commit()
    cursor.close()
    conn.close()
    return True
def search_track_in_db(query: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    clean_query = f"%{query.strip().lower()}%"
    cursor.execute("SELECT file_id, title FROM channel_music WHERE LOWER(title) LIKE LOWER(%s) LIMIT 1", (clean_query,))
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

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name or "друг"
    
    save_user(chat_id, user_id, username, first_name)

    incoming_text = ""
    if update.message.text: incoming_text = update.message.text.lower().strip()
    elif update.message.caption: incoming_text = update.message.caption.lower().strip()
    
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
            is_new = save_track_to_db(target_audio.file_id, track_title)
            if is_new:
                await context.bot.send_audio(chat_id=chat_id, audio=target_audio.file_id, caption=f"✨ Я занесла этот трек в коллекцию!\n\nИмя в базе: {track_title}")
            else:
                await update.message.reply_text(f" Этот трек уже бережно сохранен в моей коллекции под именем: {track_title}")
            return
    if update.message.text:
        text = update.message.text
        clean = text.lower().strip()
        
        if clean in ["рар команды", "rar команды", "рар, команды", "rar, команды"]:
            cmd_text = (
                "<b>Список доступных команд Rar:</b>\n\n"
                "<b>Музыкальная коллекция:</b>\n"
                "• <code>добавь</code> / <code>добавить</code> (ответом на аудио) – занести трек в коллекцию\n"
                "• <code>Рар дай песню</code> / <code>музыку</code> – отправить случайную песню\n"
                "• <code>Рар найди [название]</code> – найти сохраненный трек\n\n"
                "<b>Администрирование чата:</b>\n"
                "• <code>калл</code> (только для админов, только в группах) – призвать участников группы тегами по 6 человек\n"
                "• <code>Рар, кто вышел</code> / <code>вышедшие</code> – узнать, сколько человек покинуло группу\n\n"
                "<b>Развлечения:</b>\n"
                "• <code>Рар подкинь монетку</code> – сыграть в орла или решку\n"
                "• <code>Рар что делаешь</code> – узнать, чем сейчас занята Rar\n"
                "• <code>Rar</code> – проверка работы бота"
            )
            await update.message.reply_text(cmd_text, parse_mode="HTML")
            return
        elif clean in ["рар кто вышел", "рар, кто вышел", "rar кто вышел", "rar, кто вышел", "рар кто вышел?", "рар, кто вышел?", "rar кто вышел?", "rar, кто вышел?", "рар вышедшие", "рар, вышедшие", "rar вышедшие", "rar, вышедшие"]:
            if chat_id >= 0:
                await update.message.reply_text("Эта команда работает только в группах.")
                return
            count = get_left_count(chat_id)
            await update.message.reply_text(f"Количество участников, покинувших группу: {count}\nБуду скучать по ним!")
            return
        elif clean in ["rar", "рар"]:
            if chat_id not in rar_replies_history: rar_replies_history[chat_id] = []
            available = [a for a in answers_rar if a not in rar_replies_history[chat_id]]
            if not available: available = answers_rar
            reply_rar = random.choice(available)
            rar_replies_history[chat_id].append(reply_rar)
            if len(rar_replies_history[chat_id]) > 2: rar_replies_history[chat_id].pop(0)
            await update.message.reply_text(reply_rar)
            return
        elif clean in ["рар, подкинь монетку", "rar, подкинь монетку", "рар подкинь монетку", "rar подкинь монетку", "рар, кинь монетку", "rar, кинь монетку", "рар кинь монетку", "rar кинь монетку", "рар, монетка", "rar, монетка", "рар монетка", "rar монетка"]:
            if random.randint(1, 50) == 50:
                await update.message.reply_text("Эээ... монетка встала ребром...")
                return
            await update.message.reply_text(random.choice(answers_coin))
            return
        elif clean in ["rar, привет", "rar привет", "рар, привет", "рар привет"]:
            if chat_id not in hi_replies_history: hi_replies_history[chat_id] = []
            available = [a for a in answers_hi if a not in hi_replies_history[chat_id]]
            if not available: available = answers_hi
            reply_text = random.choice(available)
            hi_replies_history[chat_id].append(reply_text)
            if len(hi_replies_history[chat_id]) > 2: hi_replies_history[chat_id].pop(0)
            await update.message.reply_text(reply_text)
            return
        elif clean in ["we.all.love.rar", "we.all.love.rar."]:
            if chat_id not in love_replies_history: love_replies_history[chat_id] = []
            available = [a for a in answers_love if a not in love_replies_history[chat_id]]
            if not available: available = answers_love
            reply_text = random.choice(available)
            love_replies_history[chat_id].append(reply_text)
            if len(love_replies_history[chat_id]) > 2: love_replies_history[chat_id].pop(0)
            await update.message.reply_text(reply_text)
            return
        elif clean in ["rar, дай отсылку", "rar дай отсылку", "rar, отсылка", "rar отсылка", "рар, дай отсылку", "рар дай отсылку", "рар, отсылка", "рар отсылка"]:
            if chat_id not in ref_replies_history: ref_replies_history[chat_id] = []
            available = [a for a in answers_ref if a not in ref_replies_history[chat_id]]
            if not available: available = answers_ref
            reply_text = random.choice(available)
            ref_replies_history[chat_id].append(reply_text)
            if len(ref_replies_history[chat_id]) > 15: ref_replies_history[chat_id].pop(0)
            await update.message.reply_text(reply_text)
            return
        elif clean in ["rar, что делаешь?", "рар, что делаешь?", "rar что делаешь?", "рар что делаешь?", "rar, что делаешь", "рар, что делаешь", "rar что делаешь", "рар что делаешь"]:
            if chat_id not in does_replies_history: does_replies_history[chat_id] = []
            available = [a for a in answers_does if a not in does_replies_history[chat_id]]
            if not available: available = answers_does
            reply_does = random.choice(available)
            does_replies_history[chat_id].append(reply_does)
            if len(does_replies_history[chat_id]) > 2: does_replies_history[chat_id].pop(0)
            await update.message.reply_text(reply_does)
            return
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
        elif clean == "калл":
            if chat_id >= 0:
                await update.message.reply_text("Эта команда доступна только в группах.")
                return
            try:
                sender = await context.bot.get_chat_member(chat_id, user_id)
                if sender.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
                    await update.message.reply_text("Прости, но калл доступен только админам")
                    return
            except Exception: pass
            try:
                saved_members = get_chat_members(chat_id)
                if not saved_members:
                    await update.message.reply_text("В моей записной книжке пока пусто. Напишите любое слово!")
                    return
                members_tags = []
                for row in saved_members:
                    m_id, m_username, m_first_name = row
                    if int(m_id) == int(context.bot.id): continue
                    if m_username: members_tags.append(f"@{escape_markdown(m_username)}")
                    else: members_tags.append(f"[{escape_markdown(m_first_name)}](tg://user?id={int(m_id)})")
                chunk_size = 6
                for i in range(0, len(members_tags), chunk_size):
                    chunk = members_tags[i:i + chunk_size]
                    await update.message.reply_text("*Минуточку внимания\\!\\!\\!*\n\n" + "\n".join(chunk), parse_mode="MarkdownV2")
            except Exception as e:
                await update.message.reply_text(f"Ошибка команды калл: {e}")
            return
        elif clean.startswith("rar найди ") or clean.startswith("рар найди "):
            query = text[9:].strip()
            if not query:
                await update.message.reply_text("Напиши название песни, например: Rar найди duvet")
                return
            status_msg = await update.message.reply_text(" Ищу трек в своей коллекции...")
            local_track = search_track_in_db(query)
            if local_track:
                file_id, track_title = local_track
                await status_msg.delete()
                await context.bot.send_audio(chat_id=chat_id, audio=file_id, caption=f"✨ Вот что нашла у себя в коллекции: {track_title}\n\nЗапрос: {query}")
                return
            else:
                await status_msg.edit_text(" К сожалению, такой песни в моей коллекции пока нет.")

async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result: return
    user = result.new_chat_member.user
    chat_id = result.chat.id
    new_status = result.new_chat_member.status
    if user.is_bot or chat_id >= 0: return
    
    if new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED]:
        user_name = user.first_name or "друг"
        save_user(chat_id, user.id, user.username, user_name)
    elif new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
        remove_user(chat_id, user.id)

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
    asyncio.create_task(keep_alive(application))

async def keep_alive(app: Application):
    """Пинг каждые 10 минут чтобы Render не усыпил бота"""
    await asyncio.sleep(30)  # Подождать запуска
    port = int(os.environ.get("PORT", 8080))
    self_url = f"http://localhost:{port}/"
    
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(self_url, timeout=5) as resp:
                    print(f"[KEEPALIVE] Self-ping: {resp.status}")
                await app.bot.get_me()
            except Exception as e:
                print(f"[KEEPALIVE] Error: {e}")
            await asyncio.sleep(600)

def main():
    if not TOKEN or not DATABASE_URL:
        print("Ошибка: Переменные окружения не заданы!")
        return
    init_db()
    app = Application.builder().token(TOKEN).post_init(on_startup).build()
    app.add_error_handler(error_handler)
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    print("Запуск бота...")
    app.run_polling(allowed_updates=["message", "chat_member", "my_chat_member"])

if __name__ == "__main__":
    main()
