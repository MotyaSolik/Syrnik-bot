import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# ==============================
# НАСТРОЙКИ — ЗАПОЛНИ ПЕРЕД ЗАПУСКОМ
# ==============================
import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_CHAT_ID = int(os.environ["ADMIN_CHAT_ID"])          # твой личный Telegram chat_id (узнать через @userinfobot)
# ==============================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Состояния диалога
WAITING_LOGIN, WAITING_DISPLAY_NAME = range(2)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Приветствие с кнопкой Регистрация."""
    keyboard = [[InlineKeyboardButton("📝 Регистрация", callback_data="register")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "👋 Добро пожаловать в <b>Сырники Wallet</b>!\n\n"
        "Нажмите кнопку ниже, чтобы зарегистрироваться.",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )


async def register_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка нажатия кнопки Регистрация."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ Введите ваш <b>логин (ID)</b> — только латинские буквы, цифры, _ или -\n\n"
        "<i>Пример: john_doe123</i>",
        parse_mode="HTML",
    )
    return WAITING_LOGIN


async def receive_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получаем логин, проверяем что он латиницей."""
    login = update.message.text.strip()

    # Проверка: только латиница, цифры, _ и -
    import re
    if not re.match(r"^[a-zA-Z0-9_\-]{3,32}$", login):
        await update.message.reply_text(
            "❌ Неверный формат. Логин должен содержать только латинские буквы, цифры, _ или -\n"
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
    """Получаем отображаемое имя и отправляем заявку админу."""
    display_name = update.message.text.strip()
    login = context.user_data.get("login", "—")
    user = update.effective_user

    context.user_data["display_name"] = display_name
    context.user_data["telegram_id"] = user.id
    context.user_data["telegram_username"] = f"@{user.username}" if user.username else "—"

    # Кнопки для админа: подтверждение + связь с пользователем
    bot_username = (await context.bot.get_me()).username
    chat_link = f"https://t.me/{user.username}" if user.username else None

    buttons = [
        [InlineKeyboardButton("Зарегестрирован ✅", callback_data=f"approve_{user.id}")],
        [InlineKeyboardButton(
            "Связаться через бот 🔗",
            url=chat_link if chat_link else f"tg://user?id={user.id}"
        )],
    ]
    reply_markup = InlineKeyboardMarkup(buttons)

    admin_text = (
        "🆕 <b>Новая заявка на регистрацию</b>\n\n"
        f"👤 <b>Telegram:</b> {user.full_name} ({context.user_data['telegram_username']})\n"
        f"🆔 <b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"🔑 <b>Логин (ID):</b> <code>{login}</code>\n"
        f"📛 <b>Отображаемое имя:</b> {display_name}"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
        await update.message.reply_text(
            "⏳ <b>Ваша заявка отправлена!</b>\n\n"
            "Ожидайте подтверждения от администратора. Мы уведомим вас, как только аккаунт будет активирован.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Не удалось отправить сообщение админу: {e}")
        await update.message.reply_text(
            "⚠️ Произошла ошибка при отправке заявки. Попробуйте позже или свяжитесь с администратором."
        )

    return ConversationHandler.END


async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ нажимает 'Зарегестрирован ✅' — бот уведомляет пользователя."""
    query = update.callback_query
    await query.answer()

    # Получаем telegram_id пользователя из callback_data
    data = query.data  # "approve_<user_id>"
    user_id = int(data.split("_")[1])

    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 <b>Поздравляем!</b>\n\n"
                "Ваш аккаунт зарегистрирован и вы можете заходить на сайт.\n\n"
                "🌐 <b>Сырники Wallet</b> — добро пожаловать в систему!"
            ),
            parse_mode="HTML",
        )
        # Обновляем сообщение у админа
        original_text = query.message.text
        await query.edit_message_text(
            text=original_text + "\n\n✅ <b>Подтверждено</b>",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")
        await query.edit_message_text(
            text=query.message.text + "\n\n⚠️ <b>Ошибка при уведомлении пользователя</b>",
            parse_mode="HTML",
        )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена регистрации."""
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
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(approve_user, pattern=r"^approve_\d+$"))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
