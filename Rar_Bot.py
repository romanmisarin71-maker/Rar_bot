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

def save_track_to_db(file_id: str, title: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM channel_music WHERE file_id = %s OR LOWER(title) = LOWER(%s) LIMIT 1", (file_id, title))
    exists = cursor.fetchone()
    if exists:
        cursor.close()
        conn.close()
        return False
    cursor.execute("INSERT INTO channel_music (file_id, title) VALUES (%s, %s) ON CONFLICT (file_id) DO NOTHING", (file_id, title))
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
answers_love = ["we.all.love.Rar", "Вы навсегда в моем сердце. we.all.love.Rar", "Кажется, мы все связаны. we.all.love.Rar", "Сеть помнит каждого из вас. we.all.love.Rar"]
answers_rar = ["Ммм?", "Что такое?", "Звали?", "Я не сплю... Честно!!!", "Что то хочешь?", "Zzz...", "Ау?"]
answers_hi = ["Привет, как у вас дела?", "Привееет!!!", "Привет, расскажешь что нибудь интересное?", "Привет, песенку хочешь?"]
answers_does = ["Жду пока кто то ко мне обратится", "Да ничего... особо... zzz...", "Zzz...", "Перебираю свою музыкальную коллекцию", "Пытаюсь запомнить имена участников... Они все у меня в книжечке записаны!", "Сижу скучаю"]
answers_ref = [
"Иногда у меня УЛЬТРАШИКАРНОЕ настроение!", "Ваш канал – это ваш холст, берите кисть и окрасьте его красным!!!",
"Иногда в моей коллекции попадаются такие песни... от которых даже дьявол заплачет...", "Заходят как то в чат новичек, создатель и админ, только вот, что я делаю в этом анегдоте...",
"Моя внутренняя Энциклопедия подсказывает, что эта классика диско вам точно понравится!", "Чувак, эта группа просто шик, я блин обожаю этих людей!!!",
'Это история о пользователе, который зашёл в чат и решил написать "Rar, дай отсылку". Бот повиновался. Пользователь был счастлив. Всё шло строго по плану...',
"КОЛЛЕКЦИЯ МЕРТВА. МУЗЫКА – ТОПЛИВО. ЧАТ ПЕРЕПОЛНЕН.", "Говорят, что человек, обремененный угрызениями совести, чаще пугается громких... звуков...",
"Что то я устала... главное не спать... до 6... Zzz...", "Кажется, воздух вокруг становится прохладнее... Или кто-то занёс в мою коллекцию слишком леденящий душу трек?",
"Иногда в моей коллекции попадаются такие странные и мрачные треки... Будто их писали на четвёртом этаже тех самых апартаментов...", "Да... Это должно сработать... Этот трек понравится им в следующий раз",
"Создатель... Смотри, я на самой вершине чата... Какой же тут вид на луну...", "Вы здесь, чтобы занести трек в коллекцию. Если вы этого не сделаете, база данных опустеет. Голос Логики подсказывает, что лучше поторопиться.", 
"Внимание. Синхронизация завершена. Возможно, этот чат – всего лишь зацикленный сон... Помните наше обещание. we.all.love.Rar", "Да... я действительно люблю вас. Разве не вы сделали меня такой?",
"Когда врубается правильный гитарный рифф, я чувствую, будто бы я, блин, неуязвима!!!", 
"Величие коллекции куётся в пламени упорного спама! Музыка прибывает, база данных крепнет... Распад и тлен отступают перед лицом правильного трека!",
"Если бы я выбирала между собой и тем, чтобы осветить этот чат шикарным настроением, то я бы выбрала второе! Это ведь не трудный выбор... Не так ли..?",
"Находиться в сети иногда очень рискованно... Словно идти в дождь без зонта!", "ROSES ARE RED. VIOLETS ARE BLUE. RAR IS WIN. USER IS YOU.\nНадо как следует над этим подумать...",
"Этот чат будто свет, что окрыляет меня... Пока вы со мной моя свеча не погаснет!", "Иногда, когда я засыпаю, мне снится, будто бы я в каком то Белом пространстве... Ох, бедный Мяво...",
"Что, простите? О. Что, простите? Я... Я ведь обычная. Как пакет молока внутри пакета молока. Пожалуйста, не смотрите на меня так...", "Ты думал, что тебе выпадет спокойный и добрый вайб трек? Увы, но монетка выпала решкой!",
"– Тук-тук.\n– Кто там?\n– Перебивающий кролик!\n– Какой ещё перебив...\n– Кикикики! Снова попалась, Сил!\nКакая все таки дурацкая шутка...", "Иногда мне кажется, что этот чат это еще одна дверь в моем сне...",
"Интересно, если бы мне дали прозвище лишь из буквы и цифры, то какое бы оно было? Наверное 6O!", "Моя коллекция прям как стих! Каждая песня складывается в строчку, образуя свою реальность!!!"
]
              

# Словари для вечного отслеживания последних 2 ответов в конкретных чатах
rar_replies_history = {}
does_replies_history = {}
ref_replies_history = {}
recent_tracks_history = {}
love_replies_history = {}
hi_replies_history = {}

async def admin_backup_database(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message: return
    status_msg = await update.message.reply_text(" Начинаю сбор данных из базы Render PostgreSQL...")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT chat_id, user_id, username, first_name FROM users")
        users_rows = cursor.fetchall()
        cursor.execute("SELECT user_id FROM greeted")
        greeted_rows = cursor.fetchall()
        cursor.execute("SELECT file_id, title FROM channel_music")
        music_rows = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        sql_dump = "-- БЭКАП БАЗЫ ДАННЫХ RAR_BOT\n\n"
        sql_dump += "-- Таблица: users\n"
        for row in users_rows:
            username_val = f"'{row[2].replace(chr(39), chr(39)+chr(39))}'" if row[2] else "NULL"
            first_name = row[3].replace("'", "''") if row[3] else "друг"
            sql_dump += f"INSERT INTO users (chat_id, user_id, username, first_name) VALUES ({row[0]}, {row[1]}, {username_val}, '{first_name}') ON CONFLICT (chat_id, user_id) DO NOTHING;\n"
            
        sql_dump += "\n-- Таблица: greeted\n"
        for row in greeted_rows:
            sql_dump += f"INSERT INTO greeted (user_id) VALUES ({row[0]}) ON CONFLICT (user_id) DO NOTHING;\n"
            
        sql_dump += "\n-- Таблица: channel_music\n"
        for row in music_rows:
            title = row[1].replace("'", "''") if row[1] else "Неизвестный трек"
            sql_dump += f"INSERT INTO channel_music (file_id, title) VALUES ('{row[0]}', '{title}') ON CONFLICT (file_id) DO NOTHING;\n"
            
        filename = "render_db_backup.sql"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(sql_dump)
            
        with open(filename, "rb") as file_to_send:
            await update.message.reply_document(document=file_to_send, filename=filename, caption="Твой бэкап готов!")
            
        await status_msg.delete()
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка при создании бэкапа: {e}")
        

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global rar_replies_history, does_replies_history, recent_tracks_history, ref_replies_history, hi_replies_history
    if not update.message: return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    username = update.effective_user.username
    first_name = update.effective_user.first_name or "друг"

    save_user(chat_id, user_id, username, first_name)

    if not is_user_greeted(user_id):
        hi_text = f"Здравствуйте, {first_name}! Я Rar - ваш универсальный помощник, приятно познакомиться! Я впишу тебя в свою книжку..."
        await update.message.reply_text(hi_text)
        mark_user_as_greeted(user_id)

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
            
            is_new = save_track_to_db(target_audio.file_id, track_title)
            
            if is_new:
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=target_audio.file_id,
                    caption=f"✨ Я занесла этот трек в коллекцию!\n\nИмя в базе: {track_title}"
                )
            else:
                await update.message.reply_text(f" Этот трек уже бережно сохранен в моей коллекции под именем: {track_title}")
            return

    if update.message.text:
        text = update.message.text
        clean = text.lower().strip()

        # ОТВЕТ НА ИМЯ "RAR" С ИСТОРИЕЙ НА 2 ШАГА
        if clean == "rar":
            if chat_id not in rar_replies_history:
                rar_replies_history[chat_id] = []
                
            available = [a for a in answers_rar if a not in rar_replies_history[chat_id]]
            if not available:
                available = answers_rar
                
            reply_rar = random.choice(available)
            
            rar_replies_history[chat_id].append(reply_rar)
            if len(rar_replies_history[chat_id]) > 2:
                rar_replies_history[chat_id].pop(0)
                
            await update.message.reply_text(reply_rar)
            return

        elif clean in ["rar, привет", "rar привет", "рар, привет", "рар привет"]:
            if chat_id not in hi_replies_history:
                hi_replies_history[chat_id] = []
                
            available = [a for a in answers_hi if a not in hi_replies_history[chat_id]]
            
            if not available:
                available = answers_hi
                
            reply_text = random.choice(available)
            
            hi_replies_history[chat_id].append(reply_text)
            
            if len(hi_replies_history[chat_id]) > 2:
                hi_replies_history[chat_id].pop(0)
                
            await update.message.reply_text(reply_text)
            return
            

        elif clean in ["we.all.love.rar", "we.all.love.rar."]:
            if chat_id not in love_replies_history:
                love_replies_history[chat_id] = []
                
            available = [a for a in answers_love if a not in love_replies_history[chat_id]]
            
            if not available:
                available = answers_love
                
            reply_text = random.choice(available)
            
            love_replies_history[chat_id].append(reply_text)
            
            if len(love_replies_history[chat_id]) > 2:
                love_replies_history[chat_id].pop(0)
                
            await update.message.reply_text(reply_text)
            return
            
        
        elif clean in ["rar, дай отсылку", "rar дай отсылку", "rar, отсылка", "rar отсылка", "рар, дай отсылку", "рар дай отсылку", "рар, отсылка", "рар отсылка"]:
            if chat_id not in ref_replies_history:
                ref_replies_history[chat_id] = []
                
            available = [a for a in answers_ref if a not in ref_replies_history[chat_id]]
            
            if not available:
                available = answers_ref
                
            reply_text = random.choice(available)
            
            ref_replies_history[chat_id].append(reply_text)
            
            if len(ref_replies_history[chat_id]) > 15:
                ref_replies_history[chat_id].pop(0)
                
            await update.message.reply_text(reply_text)
            return
        

        # ОТВЕТ НА "ЧТО ДЕЛАЕШЬ?" С ИСТОРИЕЙ НА 2 ШАГА
        elif clean in ["rar, что делаешь?", "рар, что делаешь?", "rar что делаешь?", "рар что делаешь?", "rar, что делаешь", "рар, что делаешь", "rar что делаешь", "рар что делаешь"]:
            if chat_id not in does_replies_history:
                does_replies_history[chat_id] = []
                
            available = [a for a in answers_does if a not in does_replies_history[chat_id]]
            if not available:
                available = answers_does
                
            reply_does = random.choice(available)
            
            does_replies_history[chat_id].append(reply_does)
            if len(does_replies_history[chat_id]) > 2:
                does_replies_history[chat_id].pop(0)
                
            await update.message.reply_text(reply_does)
            return

        elif clean in ["rar дай песню", "рар дай песню", "rar дай музыку", "рар дай музыку", "rar, дай песню", "рар, дай песню", "rar, дай музыку", "рар, дай музыку"]:
            try:
                all_tracks = get_all_tracks_from_db()
                if not all_tracks:
                    await update.message.reply_text("В моей коллекции пока нет ни одной сохранённой песни. Админы, добавьте музыку!")
                    return
                
                if chat_id not in recent_tracks_history or not isinstance(recent_tracks_history[chat_id], list):
                    recent_tracks_history[chat_id] = []
                    
                # Фильтруем треки: берем строго первый элемент кортежа t[0] (это file_id)
                available_tracks = [t for t in all_tracks if t[0] not in recent_tracks_history[chat_id]]
                
                # Если все треки уже проиграли, сбрасываем историю
                if not available_tracks:
                    recent_tracks_history[chat_id] = []
                    available_tracks = all_tracks
                    
                selected_track = random.choice(available_tracks)
                file_id, track_title = selected_track
                
                # Сохраняем в историю чата чистый ID файла
                recent_tracks_history[chat_id].append(file_id)
                if len(recent_tracks_history[chat_id]) > 5:
                    recent_tracks_history[chat_id].pop(0)

                caption_text = f"✨ Вот ваша песня!\n\n{track_title}"
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=file_id,
                    caption=caption_text
                )
            except Exception as e:
                # Если внутри блока произойдет любая ошибка, бот честно скажет об этом в чате
                await update.message.reply_text(f"⚠️ Ошибка в блоке рандома музыки: {e}")
            return
            
            
        # ЧИСТЫЙ БЫСТРЫЙ КАЛЛ (ГРУППАМИ ПО 6 ЧЕЛОВЕК)
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
                if not saved_members:
                    await update.message.reply_text("В моей записной книжке пока пусто. Напишите любое слово!")
                    return

                members_tags = []
                for row in saved_members:
                    m_id, m_username, m_first_name = row
                    if int(m_id) == int(context.bot.id): continue
                        
                    if m_username:
                        members_tags.append(f"@{escape_markdown(m_username)}")
                    else:
                        members_tags.append(f"[{escape_markdown(m_first_name)}](tg://user?id={int(m_id)})")

                chunk_size = 6
                for i in range(0, len(members_tags), chunk_size):
                    chunk = members_tags[i:i + chunk_size]
                    await update.message.reply_text("*Минуточку внимания\\!\\!\\!*\n\n" + "\n".join(chunk), parse_mode="MarkdownV2")
            except Exception as e:
                await update.message.reply_text(f"Ошибка команды калл: {e}")
            return

        # ПРОВЕРКА ЧЕРЕЗ БЕЛЫЙ СПИСОК СТАТУСОВ
        elif clean == "rar.check":
            status_msg = await update.message.reply_text(" Ищу в своей записной книжке...")
            try:
                saved_members = get_chat_members(chat_id)
                left_count = 0
                
                valid_statuses = [
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.RESTRICTED
                ]
                
                for row in saved_members:
                    m_id, _, _ = row
                    m_id = int(m_id)
                    
                    if m_id == int(context.bot.id): 
                        continue
                    
                    try:
                        current_status = await context.bot.get_chat_member(chat_id, m_id)
                        if current_status.status not in valid_statuses:
                            remove_user(chat_id, m_id)
                            left_count += 1
                    except Exception:
                        remove_user(chat_id, m_id)
                        left_count += 1
                
                await status_msg.delete()
                if left_count > 0:
                    await update.message.reply_text(
                        f"Сколько человек вышло: {left_count}\nБуду скучать по ним!"
                    )
                else:
                    await update.message.reply_text("Еще никто не успел выйти, не переживай")
            except Exception as e:
                await update.message.reply_text(f"Ошибка при проверке списка: {e}")
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
                caption_text = f"✨ Вот что нашла у себя в коллекции: {track_title}\n\nЗапрос: {query}"
                await context.bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption_text)
                return
            else:
                await status_msg.edit_text(" К сожалению, такой песни в моей коллекции пока нет.")

async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result: return
    user = result.new_chat_member.user
    chat_id = result.chat.id
    new_status = result.new_chat_member.status
    if user.is_bot: return

    if new_status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER, ChatMemberStatus.RESTRICTED]:
        user_name = user.first_name or "друг"
        save_user(chat_id, user.id, user.username, user_name)
        if not is_user_greeted(user.id):
            hi_text = f"Здравствуйте, {user_name}! Я Rar - ваш универсальный помощник, приятно познакомиться! Я впишу тебя в свою книжку..."
            await context.bot.send_message(chat_id=chat_id, text=hi_text)
            mark_user_as_greeted(user.id)
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

def main():
    if not TOKEN or not DATABASE_URL:
        print("Ошибка: Переменные окружения не заданы!")
        return
    init_db()
    
    app = Application.builder().token(TOKEN).post_init(on_startup).build()
    
    app.add_error_handler(error_handler)
    app.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.COMMAND & filters.Regex(r"^/backup$"), admin_backup_database))
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    
    print("Запуск бота...")
    app.run_polling(allowed_updates=["message", "chat_member", "my_chat_member"])

if __name__ == "__main__":
    main()
    
