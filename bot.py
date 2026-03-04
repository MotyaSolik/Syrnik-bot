import logging
import os
import re
import json
import random
import string
import aiohttp
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

# Firebase
FIREBASE_URL = "https://syrnik-wallet-default-rtdb.europe-west1.firebasedatabase.app"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Состояния диалога регистрации
WAITING_LOGIN, WAITING_DISPLAY_NAME = range(2)

# Хранилище: user_id -> данные пользователя
user_data_store = {}

EMOJIS = ['🐻','🐼','🦊','🐸','🐯','🦁','🐺','🦉','🐧','🦋','🐬','🦄','🐙','🌟','🍀','🦝','🐨','🦔','🐉','🌈','🦅','🦩','🐝','🦀','🍕','🎸','🚀','💎','🎯','⚡','🦆','🥞','🦢','🐓']

def emoji_for(login: str) -> str:
    s = sum(ord(c) for c in login)
    return EMOJIS[s % len(EMOJIS)]

def gen_password(length=8) -> str:
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


# ── Firebase helpers ──
async def fb_get(path: str) -> dict:
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{FIREBASE_URL}/{path}.json") as r:
            return await r.json()

async def fb_set(path: str, data) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.put(
            f"{FIREBASE_URL}/{path}.json",
            data=json.dumps(data),
            headers={"Content-Type": "application/json"},
        ) as r:
            await r.read()

async def register_in_firebase(login: str, display: str, telegram_id: int) -> str:
    """Добавляет пользователя в Firebase. Возвращает сгенерированный пароль."""
    # Load existing db
    data = await fb_get("syrniki")
    if not data:
        data = {}

    users = data.get("users", {})
    if isinstance(users, list):
        users = {str(i): u for i, u in enumerate(users)}

    # Check login uniqueness
    for u in users.values():
        if u.get("login", "").lower() == login.lower():
            return None  # already exists

    password = gen_password()
    user_id = telegram_id  # use telegram ID as unique ID

    users[str(user_id)] = {
        "id": user_id,
        "login": login.lower(),
        "display": display,
        "password": password,
        "balance": 0,
        "emoji": emoji_for(login),
        "telegramId": telegram_id,
        "isPrivate": False,
        "bgColor": "yellow",
        "privileges": [],
    }

    await fb_set("syrniki/users", users)
    return password


# ── Bot handlers ──

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
        "✏️ Введите ваш <b>логин</b> — только латинские буквы, цифры или _\n\n"
        "<i>Пример: ivan_petrov</i>",
        parse_mode="HTML",
    )
    return WAITING_LOGIN


async def receive_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    login = update.message.text.strip()
    if not re.match(r"^[a-zA-Z0-9_]{3,22}$", login):
        await update.message.reply_text(
            "❌ Неверный формат. Только латинские буквы, цифры, _\n"
            "Длина: 3–22 символа.\n\nПопробуйте ещё раз:"
        )
        return WAITING_LOGIN

    context.user_data["login"] = login.lower()
    await update.message.reply_text(
        f"✅ Логин <b>{login}</b> принят!\n\n"
        "Теперь введите ваше <b>отображаемое имя</b> (любой язык):\n\n"
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

    # Кнопки для админа: Зарегистрировать + Связаться
    keyboard = [[
        InlineKeyboardButton("✅ Зарегистрировать", callback_data=f"register_{user.id}"),
        InlineKeyboardButton("💬 Написать", url=f"tg://user?id={user.id}"),
    ]]

    admin_text = (
        "🆕 <b>Новая заявка на регистрацию</b>\n\n"
        f"👤 <b>Telegram:</b> {user.full_name} ({user_data_store[user.id]['telegram_username']})\n"
        f"🆔 <b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"🔑 <b>Логин:</b> <code>{login}</code>\n"
        f"📛 <b>Имя:</b> {display_name}\n\n"
        f"Ответить: <code>/msg {user.id} текст</code>"
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
            "Ожидайте подтверждения от администратора.\n"
            "Если хотите написать — просто напишите сюда.",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка отправки админу: {e}")
        await update.message.reply_text("⚠️ Ошибка при отправке заявки. Попробуйте позже.")

    return ConversationHandler.END


async def do_register_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админ нажимает 'Зарегистрировать' — добавляем в Firebase."""
    query = update.callback_query
    await query.answer()

    # Only admin can register
    if update.effective_user.id != ADMIN_CHAT_ID:
        await query.answer("⛔ Только администратор может регистрировать.", show_alert=True)
        return

    user_id = int(query.data.split("_")[1])
    info = user_data_store.get(user_id)

    if not info:
        await query.answer("❌ Данные заявки не найдены (перезапустите бота)", show_alert=True)
        return

    login = info["login"]
    display = info["display_name"]

    # Register in Firebase
    try:
        password = await register_in_firebase(login, display, user_id)
    except Exception as e:
        logger.error(f"Firebase error: {e}")
        await query.edit_message_text(
            text=query.message.text + "\n\n❌ <b>Ошибка подключения к Firebase</b>",
            parse_mode="HTML",
        )
        return

    if password is None:
        # Login already taken
        await query.edit_message_text(
            text=query.message.text + f"\n\n⚠️ <b>Логин <code>{login}</code> уже занят!</b> Попросите пользователя выбрать другой.",
            parse_mode="HTML",
        )
        return

    # Notify user with credentials
    keyboard = [[InlineKeyboardButton("🌐 Войти на сайт", url="https://syrnik-wallet.netlify.app/")]]
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 <b>Поздравляем! Вы зарегистрированы!</b>\n\n"
                f"🔑 <b>Логин:</b> <code>{login}</code>\n"
                f"🔒 <b>Пароль:</b> <code>{password}</code>\n\n"
                "Сохраните эти данные! Пароль можно сменить в профиле.\n"
                "Нажмите кнопку чтобы войти:"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

    # Update admin message
    await query.edit_message_text(
        text=query.message.text + f"\n\n✅ <b>Зарегистрирован!</b>\nПароль: <code>{password}</code>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Написать", url=f"tg://user?id={user_id}"),
        ]])
    )


async def user_message_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = update.message.text
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
    if update.effective_user.id != ADMIN_CHAT_ID:
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "❌ Использование: <code>/msg &lt;user_id&gt; текст</code>",
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
        await update.message.reply_text(
            f"✅ Отправлено пользователю <code>{target_id}</code>.",
            parse_mode="HTML",
        )
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
    # ВАЖНО: register_ должен быть до approve_ чтобы не перехватывало
    app.add_handler(CallbackQueryHandler(do_register_user, pattern=r"^register_\d+$"))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Chat(ADMIN_CHAT_ID),
        user_message_to_admin,
    ))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
