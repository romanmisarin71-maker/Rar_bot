import os
import random
import re
import asyncio
import psycopg2
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaAudio
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    MessageHandler,
    ChatMemberHandler,
    CallbackQueryHandler,
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

# Словарь для истории треков в чатах (чтобы не повторялись)
recent_tracks_history = {}

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global recent_tracks_history
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
                await update.message.reply_text(f"Этот трек уже бережно сохранен в моей коллекции под именем: {track_title}")
            return

    if update.message.text:
        text = update.message.text
        clean = text.lower().strip()

        # --- КОМАНДА "РАР ДАЙ ПЕСНИ" (5 треков с кнопками, замена сообщения, без повторов) ---
        if clean in ["rar дай песни", "рар дай песни", "rar дай 5 песен", "рар дай 5", "rar дай 5", "рар дай песен"]:
            all_tracks = get_all_tracks_from_db()
            
            if len(all_tracks) < 5:
                await update.message.reply_text("😅 В коллекции меньше 5 песен! Добавьте ещё.")
                return
            
            # Инициализируем историю просмотренных треков для этого чата
            if 'seen_tracks' not in context.chat_data:
                context.chat_data['seen_tracks'] = []
            if 'current_tracks' not in context.chat_data:
                context.chat_data['current_tracks'] = []
            
            # Выбираем 5 треков, которых ещё не было
            available = [t for t in all_tracks if t[0] not in context.chat_data['seen_tracks']]
            
            if len(available) >= 5:
                selected_tracks = random.sample(available, 5)
            else:
                # Если все треки уже показаны — сбрасываем историю
                context.chat_data['seen_tracks'] = []
                selected_tracks = random.sample(all_tracks, min(5, len(all_tracks)))
            
            # Сохраняем ID показанных треков
            for file_id, _ in selected_tracks:
                if file_id not in context.chat_data['seen_tracks']:
                    context.chat_data['seen_tracks'].append(file_id)
            
            context.chat_data['current_tracks'] = selected_tracks
            
            # Формируем сообщение
            track_list = []
            for i, (_, title) in enumerate(selected_tracks, 1):
                track_list.append(f"{i}. {title}")
            
            keyboard = []
            for i, (file_id, title) in enumerate(selected_tracks, 1):
                short_title = title[:30] + "..." if len(title) > 30 else title
                keyboard.append([InlineKeyboardButton(f"{i}. {short_title}", callback_data=f"play_from_list_{file_id}")])
            
            # Проверяем, есть ли ещё новые треки
            remaining = len([t for t in all_tracks if t[0] not in context.chat_data['seen_tracks']])
            if remaining > 0:
                keyboard.append([InlineKeyboardButton(f"🎲 Ещё 5 песен ({remaining} новых)", callback_data="new_list")])
            else:
                keyboard.append([InlineKeyboardButton("🔄 Все треки показаны! Сбросить", callback_data="reset_list")])
            
            sent_msg = await update.message.reply_text(
                f"🎵 **Вот 5 случайных песен:**\n\n" + "\n".join(track_list),
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            
            context.chat_data['list_message_id'] = sent_msg.message_id
            return

        # --- КОМАНДА "РАР ДАЙ ПЕСНЮ" (один трек с кнопкой "Следующая") ---
        elif clean in ["rar дай песню", "рар дай песню", "rar дай музыку", "рар дай музыку", "rar, дай песню", "рар, дай песню", "rar, дай музыку", "рар, дай музыку"]:
            try:
                all_tracks = get_all_tracks_from_db()
                if not all_tracks:
                    await update.message.reply_text("В моей коллекции пока нет ни одной сохранённой песни. Админы, добавьте музыку!")
                    return
                
                if chat_id not in recent_tracks_history or not isinstance(recent_tracks_history[chat_id], list):
                    recent_tracks_history[chat_id] = []
                    
                available_tracks = [t for t in all_tracks if t[0] not in recent_tracks_history[chat_id]]
                
                if not available_tracks:
                    recent_tracks_history[chat_id] = []
                    available_tracks = all_tracks
                    
                selected_track = random.choice(available_tracks)
                file_id, track_title = selected_track
                
                recent_tracks_history[chat_id].append(file_id)
                if len(recent_tracks_history[chat_id]) > 5:
                    recent_tracks_history[chat_id].pop(0)

                caption_text = f"✨ Вот ваша песня!\n\n{track_title}"
                
                keyboard = [[InlineKeyboardButton("🎲 Следующая песня", callback_data=f"next_track_{chat_id}")]]
                await context.bot.send_audio(
                    chat_id=chat_id,
                    audio=file_id,
                    caption=caption_text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                await update.message.reply_text(f"⚠️ Ошибка в блоке рандома музыки: {e}")
            return

        # --- КОМАНДА "КАЛЛ" (ГИБРИД: проверяет и чистит БД на лету) ---
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
                
                valid_statuses = [
                    ChatMemberStatus.MEMBER,
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.OWNER,
                    ChatMemberStatus.RESTRICTED
                ]
                
                members_tags = []
                left_count = 0
                
                for row in saved_members:
                    m_id, m_username, m_first_name = row
                    m_id = int(m_id)
                    if m_id == int(context.bot.id):
                        continue
                    
                    # Проверяем статус пользователя
                    try:
                        current_status = await context.bot.get_chat_member(chat_id, m_id)
                        if current_status.status not in valid_statuses:
                            remove_user(chat_id, m_id)
                            left_count += 1
                            continue
                    except Exception:
                        remove_user(chat_id, m_id)
                        left_count += 1
                        continue
                    
                    # Если пользователь активен — добавляем в список для тега
                    if m_username:
                        members_tags.append(f"@{escape_markdown(m_username)}")
                    else:
                        members_tags.append(f"[{escape_markdown(m_first_name)}](tg://user?id={m_id})")
                
                if not members_tags:
                    await update.message.reply_text("В моей книжке нет активных участников для тега!")
                    return
                
                # Если кто-то вышел — пишем об этом
                if left_count > 0:
                    await update.message.reply_text(f"👋 Очистил {left_count} вышедших участников из книжки")
                
                chunk_size = 6
                for i in range(0, len(members_tags), chunk_size):
                    chunk = members_tags[i:i + chunk_size]
                    await update.message.reply_text("*Минуточку внимания\\!\\!\\!*\n\n" + "\n".join(chunk), parse_mode="MarkdownV2")
                    
            except Exception as e:
                await update.message.reply_text(f"Ошибка команды калл: {e}")
            return

        # --- КОМАНДА "RAR НАЙДИ" ---
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
                caption_text = f"✨ Вот что нашла у себя в коллекции: {track_title}\n\nЗапрос: {query}"
                await context.bot.send_audio(chat_id=chat_id, audio=file_id, caption=caption_text)
                return
            else:
                await status_msg.edit_text("❌ К сожалению, такой песни в моей коллекции пока нет.")

# --- CALLBACK-ОБРАБОТЧИКИ ---

async def play_from_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ЗАМЕНЯЕТ список на аудио с кнопкой 'Вернуться к списку'"""
    query = update.callback_query
    await query.answer()
    
    file_id = query.data.split("_")[2]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT title FROM channel_music WHERE file_id = %s", (file_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    
    title = row[0] if row else "Неизвестный трек"
    
    keyboard = [[InlineKeyboardButton("📋 Вернуться к списку", callback_data="back_to_list")]]
    
    await query.edit_message_media(
        media=InputMediaAudio(
            media=file_id,
            caption=f"🎵 **{title}**",
            parse_mode="Markdown"
        ),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def back_to_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возвращает сообщение к списку песен"""
    query = update.callback_query
    await query.answer()
    
    selected_tracks = context.chat_data.get('current_tracks', [])
    
    if not selected_tracks:
        await query.edit_message_text("❌ Список песен потерян. Попробуйте снова: `рар дай песни`")
        return
    
    track_list = []
    for i, (_, title) in enumerate(selected_tracks, 1):
        track_list.append(f"{i}. {title}")
    
    keyboard = []
    for i, (file_id, title) in enumerate(selected_tracks, 1):
        short_title = title[:30] + "..." if len(title) > 30 else title
        keyboard.append([InlineKeyboardButton(f"{i}. {short_title}", callback_data=f"play_from_list_{file_id}")])
    
    # Проверяем остаток новых треков
    all_tracks = get_all_tracks_from_db()
    remaining = len([t for t in all_tracks if t[0] not in context.chat_data.get('seen_tracks', [])])
    
    if remaining > 0:
        keyboard.append([InlineKeyboardButton(f"🎲 Ещё 5 песен ({remaining} новых)", callback_data="new_list")])
    else:
        keyboard.append([InlineKeyboardButton("🔄 Все треки показаны! Сбросить", callback_data="reset_list")])
    
    await query.edit_message_text(
        text=f"🎵 **Вот 5 случайных песен:**\n\n" + "\n".join(track_list),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def new_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ЗАМЕНЯЕТ сообщение на новый список из 5 песен (без повторов)"""
    query = update.callback_query
    await query.answer()
    
    all_tracks = get_all_tracks_from_db()
    seen_tracks = context.chat_data.get('seen_tracks', [])
    
    # Ищем треки, которые ещё не показывали
    available = [t for t in all_tracks if t[0] not in seen_tracks]
    
    if len(available) == 0:
        # Все треки показаны — предлагаем сбросить
        keyboard = [[InlineKeyboardButton("🔄 Сбросить историю и показать новые", callback_data="reset_list")]]
        await query.edit_message_text(
            text="🎵 **Вы просмотрели все песни в коллекции!**\n\nХотите начать заново?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        return
    
    # Берём до 5 новых треков
    if len(available) >= 5:
        selected_tracks = random.sample(available, 5)
    else:
        selected_tracks = available
    
    # Добавляем новые треки в историю
    for file_id, _ in selected_tracks:
        if file_id not in seen_tracks:
            seen_tracks.append(file_id)
    
    context.chat_data['seen_tracks'] = seen_tracks
    context.chat_data['current_tracks'] = selected_tracks
    
    track_list = []
    for i, (_, title) in enumerate(selected_tracks, 1):
        track_list.append(f"{i}. {title}")
    
    keyboard = []
    for i, (file_id, title) in enumerate(selected_tracks, 1):
        short_title = title[:30] + "..." if len(title) > 30 else title
        keyboard.append([InlineKeyboardButton(f"{i}. {short_title}", callback_data=f"play_from_list_{file_id}")])
    
    remaining = len([t for t in all_tracks if t[0] not in seen_tracks])
    
    if remaining > 0:
        keyboard.append([InlineKeyboardButton(f"🎲 Ещё 5 песен ({remaining} новых)", callback_data="new_list")])
    else:
        keyboard.append([InlineKeyboardButton("🔄 Все треки показаны! Сбросить", callback_data="reset_list")])
    
    # Если это последние треки в коллекции, меняем заголовок
    if len(available) < 5:
        title_text = f"🎵 **Осталось {len(available)} песен:**"
    else:
        title_text = "🎵 **Вот ещё 5 случайных песен:**"
    
    await query.edit_message_text(
        text=f"{title_text}\n\n" + "\n".join(track_list),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def reset_list_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сбрасывает историю просмотренных треков и показывает новый список"""
    query = update.callback_query
    await query.answer()
    
    context.chat_data['seen_tracks'] = []
    all_tracks = get_all_tracks_from_db()
    
    if len(all_tracks) < 5:
        await query.edit_message_text("😅 В коллекции меньше 5 песен!")
        return
    
    selected_tracks = random.sample(all_tracks, 5)
    
    for file_id, _ in selected_tracks:
        context.chat_data['seen_tracks'].append(file_id)
    
    context.chat_data['current_tracks'] = selected_tracks
    
    track_list = []
    for i, (_, title) in enumerate(selected_tracks, 1):
        track_list.append(f"{i}. {title}")
    
    keyboard = []
    for i, (file_id, title) in enumerate(selected_tracks, 1):
        short_title = title[:30] + "..." if len(title) > 30 else title
        keyboard.append([InlineKeyboardButton(f"{i}. {short_title}", callback_data=f"play_from_list_{file_id}")])
    
    remaining = len([t for t in all_tracks if t[0] not in context.chat_data['seen_tracks']])
    keyboard.append([InlineKeyboardButton(f"🎲 Ещё 5 песен ({remaining} новых)", callback_data="new_list")])
    
    await query.edit_message_text(
        text=f"🔄 **История сброшена! Вот новый список:**\n\n" + "\n".join(track_list),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def next_track_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка 'Следующая песня' для команды 'рар дай песню'"""
    global recent_tracks_history
    query = update.callback_query
    await query.answer()
    
    chat_id = int(query.data.split("_")[2])
    
    try:
        all_tracks = get_all_tracks_from_db()
        if not all_tracks:
            await query.edit_message_text("❌ В коллекции нет песен!")
            return
        
        if chat_id not in recent_tracks_history or not isinstance(recent_tracks_history[chat_id], list):
            recent_tracks_history[chat_id] = []
        
        available_tracks = [t for t in all_tracks if t[0] not in recent_tracks_history[chat_id]]
        
        if not available_tracks:
            recent_tracks_history[chat_id] = []
            available_tracks = all_tracks
        
        selected_track = random.choice(available_tracks)
        file_id, track_title = selected_track
        
        recent_tracks_history[chat_id].append(file_id)
        if len(recent_tracks_history[chat_id]) > 5:
            recent_tracks_history[chat_id].pop(0)
        
        keyboard = [[InlineKeyboardButton("🎲 Следующая песня", callback_data=f"next_track_{chat_id}")]]
        await query.edit_message_media(
            media=InputMediaAudio(
                media=file_id,
                caption=f"✨ Вот ваша песня!\n\n{track_title}",
                parse_mode="Markdown"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        await query.edit_message_text(f"⚠️ Ошибка: {e}")

# --- ОБРАБОТЧИКИ СОБЫТИЙ ---

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
    app.add_handler(MessageHandler(filters.ALL, handle_message))
    
    # Добавляем callback-обработчики
    app.add_handler(CallbackQueryHandler(play_from_list_callback, pattern="play_from_list_"))
    app.add_handler(CallbackQueryHandler(back_to_list_callback, pattern="back_to_list"))
    app.add_handler(CallbackQueryHandler(new_list_callback, pattern="new_list"))
    app.add_handler(CallbackQueryHandler(reset_list_callback, pattern="reset_list"))
    app.add_handler(CallbackQueryHandler(next_track_callback, pattern="next_track_"))
    
    print("Запуск бота...")
    app.run_polling(allowed_updates=["message", "chat_member", "my_chat_member", "callback_query"])

if __name__ == "__main__":
    main()
