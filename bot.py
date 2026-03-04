import logging
import os
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Состояния диалога регистрации
WAITING_LOGIN, WAITING_DISPLAY_NAME = range(2)

# Хранилище: user_id -> данные пользователя
user_data_store = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton("📝 Регистрация", callback_data="register")]]
    await update.message.reply_text(
        "👋 Добро пожаловать в <b>Сырники Wallet</b>!\n\n"
        "Нажмите кнопку ниже, чтобы зарегистрироваться.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def register_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ Введите ваш <b>логин (ID)</b> — только латинские буквы, цифры, _ или -\n\n"
        "<i>Пример: john_doe123</i>",
        parse_mode="HTML",
    )
    return WAITING_LOGIN


async def receive_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    login = update.message.text.strip()
    if not re.match(r"^[a-zA-Z0-9_\-]{3,32}$", login):
        await update.message.reply_text(
            "❌ Неверный формат. Только латинские буквы, цифры, _ или -\n"
            "Длина: от 3 до 32 символов.\n\nПопробуйте ещё раз:"
        )
        return WAITING_LOGIN

    context.user_data["login"] = login
    await update.message.reply_text(
        f"✅ Логин <b>{login}</b> принят!\n\n"
        "Теперь введите ваше <b>отображаемое имя</b> (можно на любом языке):\n\n"
        "<i>Пример: Иван Иванов</i>",
        parse_mode="HTML",
    )
    return WAITING_DISPLAY_NAME


async def receive_display_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    display_name = update.message.text.strip()
    login = context.user_data.get("login", "—")
    user = update.effective_user

    # Сохраняем данные пользователя
    user_data_store[user.id] = {
        "login": login,
        "display_name": display_name,
        "telegram_id": user.id,
        "telegram_username": f"@{user.username}" if user.username else "—",
        "full_name": user.full_name,
    }

    keyboard = [[
        InlineKeyboardButton("Зарегестрирован ✅", callback_data=f"approve_{user.id}"),
        InlineKeyboardButton("💬 Связаться", url=f"tg://user?id={user.id}"),
    ]]

    admin_text = (
        "🆕 <b>Новая заявка на регистрацию</b>\n\n"
        f"👤 <b>Telegram:</b> {user.full_name} ({user_data_store[user.id]['telegram_username']})\n"
        f"🆔 <b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"🔑 <b>Логин (ID):</b> <code>{login}</code>\n"
        f"📛 <b>Отображаемое имя:</b> {display_name}\n\n"
        f"💬 Чтобы написать этому пользователю, используйте:\n"
        f"<code>/msg {user.id} текст сообщения</code>"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        await update.message.reply_text(
            "⏳ <b>Ваша заявка отправлена!</b>\n\n"
            "Ожидайте подтверждения от администратора.\n\n"
            "Если хотите написать администратору, просто отправьте сообщение сюда — оно будет переслано."
            "Пока что вы можете зайти как user(пароль такой же)",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка отправки админу: {e}")
        await update.message.reply_text("⚠️ Ошибка при отправке заявки. Попробуйте позже.")

    return ConversationHandler.END


async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    user_id = int(query.data.split("_")[1])

    keyboard = [[InlineKeyboardButton("🌐 Войти на сайт", url="https://syrnik-wallet.netlify.app/")]]

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 <b>Поздравляем!</b>\n\n"
                "Ваш аккаунт зарегистрирован и вы можете заходить на сайт!\n\n"
                "Если есть вопросы — просто напишите сюда, администратор ответит."
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
        await query.edit_message_text(
            text=query.message.text + "\n\n✅ <b>Подтверждено</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")
        await query.edit_message_text(
            text=query.message.text + "\n\n⚠️ <b>Ошибка при уведомлении пользователя</b>",
            parse_mode="HTML",
        )


async def user_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пользователь пишет боту — сообщение пересылается админу."""
    user = update.effective_user
    text = update.message.text

    # Не обрабатываем сообщения от самого админа
    if user.id == ADMIN_CHAT_ID:
        return

    username = f"@{user.username}" if user.username else "—"
    info = user_data_store.get(user.id)
    login_info = f"🔑 Логин: <code>{info['login']}</code>\n" if info else ""

    forward_text = (
        f"💬 <b>Сообщение от пользователя</b>\n\n"
        f"👤 {user.full_name} ({username})\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"{login_info}\n"
        f"📩 <b>Сообщение:</b>\n{text}\n\n"
        f"<i>Ответить: <code>/msg {user.id} ваш ответ</code></i>"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=forward_text,
            parse_mode="HTML",
        )
        await update.message.reply_text("✉️ Ваше сообщение отправлено администратору.")
    except Exception as e:
        logger.error(f"Ошибка пересылки сообщения: {e}")


async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ отвечает пользователю через /msg <user_id> <текст>."""
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "❌ Использование: <code>/msg &lt;user_id&gt; текст сообщения</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_id = int(args[0])
        message_text = " ".join(args[1:])

        await context.bot.send_message(
            chat_id=target_id,
            text=f"📨 <b>Сообщение от администратора:</b>\n\n{message_text}",
            parse_mode="HTML",
        )
        await update.message.reply_text(f"✅ Сообщение отправлено пользователю <code>{target_id}</code>.", parse_mode="HTML")
    except ValueError:
        await update.message.reply_text("❌ Неверный ID пользователя.")
    except Exception as e:
        logger.error(f"Ошибка отправки ответа: {e}")
        await update.message.reply_text(f"⚠️ Ошибка: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Регистрация отменена. Напишите /start чтобы начать заново.")
    return ConversationHandler.END


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(register_button, pattern="^register$")],
        states={
            WAITING_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_login)],
            WAITING_DISPLAY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_display_name)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("msg", admin_reply))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(approve_user, pattern=r"^approve_\d+$"))

    # Сообщения от пользователей пересылаются админу
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Chat(ADMIN_CHAT_ID),
        user_message_to_admin
    ))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

