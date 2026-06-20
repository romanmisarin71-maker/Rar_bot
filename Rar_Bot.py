import random
from telegram import Update
from telegram.ext import Application, MessageHandler, filters

TOKEN = "8644822417:AAEhIQgztuKPdVa8ta8cvCLm5laqcqT1t8w"

greeted_users = set()

answers_rar = [
    "Привееет!",
    "Что такое?",
    "Звали?",
    "Я не сплю... Честно!!!"
]

hi_rar = "Добро пожаловать в наш чат, надеюсь, что вам здесь понравится. Меня зовут Rar, приятно познакомиться. Как ваши дела? Чем увлекаетесь? Что делали в последнее время?"

last_reply = None

async def handle_message(update: Update, context):
    global last_reply, greeted_users

    text = update.message.text
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "друг"

    if not text:
        return

    clean = text.lower().strip()

    # ---- ПРИВЕТСТВИЕ НОВИЧКА ----
    if user_id not in greeted_users:
        await update.message.reply_text(hi_rar)
        greeted_users.add(user_id)
        return

    # ---- КОМАНДА "rar" ----
    if clean == "rar":
        if last_reply is not None:
            available = [a for a in answers_rar if a != last_reply]
        else:
            available = answers_rar

        reply_rar = random.choice(available)
        last_reply = reply_rar
        await update.message.reply_text(reply_rar)

    # ---- КОМАНДА "калл" ----
    elif clean == "калл":
        chat_member = await context.bot.get_chat_member(update.effective_chat.id, user_id)
        if chat_member.status not in ["administrator", "creator"]:
            await update.message.reply_text("Прости, но калл доступен только админам, ты можешь попросить их созвать всех")
            return

        try:
            members = []
            async for member in context.bot.get_chat_members(update.effective_chat.id):
                if member.user.id == context.bot.id:
                    continue
                if member.user.username:
                    members.append(f"@{member.user.username}")
                else:
                    members.append(member.user.first_name or "Юзер")

            if not members:
                await update.message.reply_text("Почему-то я никого не нашла")
                return

            chunk_size = 10
            for i in range(0, len(members), chunk_size):
                chunk = members[i:i + chunk_size]
                await update.message.reply_text("Минуточку внимания!!!\n" + "\n".join(chunk))

        except Exception as e:
            await update.message.reply_text(f"Ошибка при сборе участников: {e}")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
