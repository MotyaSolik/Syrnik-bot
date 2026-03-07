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
FIREBASE_URL = os.environ["FIREBASE_URL"]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Состояния диалога регистрации
WAITING_LOGIN, WAITING_DISPLAY_NAME, WAITING_PASSWORD, EMOJI_LOGIN, EMOJI_PASSWORD, BROADCAST_TEXT = range(6)

# Хранилище: user_id -> данные пользователя
user_data_store = {}

EMOJIS = ['🐻','🐼','🦊','🐸','🐯','🦁','🐺','🦉','🐧','🦋','🐬','🦄','🐙','🌟','🍀','🦝','🐨','🦔','🐉','🌈','🦅','🦩','🐝','🦀','🍕','🎸','🚀','💎','🎯','⚡','🦆','🥞','🦢','🐓']
# System emojis excluded from user picker
EMOJIS_SYSTEM = ['🔰','🤖','💠','☑️']

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

async def register_in_firebase(login: str, display: str, telegram_id: int, password: str, tg_username_raw: str = "") -> bool:
    """Добавляет пользователя в Firebase. Возвращает False если логин занят."""
    data = await fb_get("syrniki")
    if not data:
        data = {}

    users = data.get("users", {})
    if isinstance(users, list):
        users = {str(i): u for i, u in enumerate(users)}

    # Check login uniqueness
    for u in users.values():
        if u.get("login", "").lower() == login.lower():
            return False  # already exists

    user_id = telegram_id

    tg_username = tg_username_raw.lstrip("@") if tg_username_raw and tg_username_raw != "—" else ""
    users[str(user_id)] = {
        "id": user_id,
        "login": login.lower(),
        "display": display,
        "password": password,
        "balance": 0,
        "emoji": emoji_for(login),
        "telegramId": telegram_id,
        "telegramUsername": tg_username,
        "isPrivate": False,
        "bgColor": "yellow",
        "privileges": [],
    }

    await fb_set("syrniki/users", users)
    return True


# ── Bot handlers ──

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("📝 Регистрация", callback_data="register")],
        [InlineKeyboardButton("😊 Сменить эмодзи", callback_data="emoji_change")],
    ]
    await update.message.reply_text(
        "👋 Добро пожаловать в <b>Сырники Wallet</b>!\n\n"
        "Нажмите кнопку ниже, чтобы зарегистрироваться или сменить эмодзи.",
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




async def emoji_change_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало смены эмодзи — просим логин."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "😊 <b>Смена эмодзи</b>\n\n"
        "Введите ваш <b>логин</b>:",
        parse_mode="HTML",
    )
    return EMOJI_LOGIN


async def emoji_receive_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получили логин — просим пароль."""
    context.user_data["emoji_login"] = update.message.text.strip().lower()
    await update.message.reply_text(
        "🔒 Теперь введите ваш <b>пароль</b>:",
        parse_mode="HTML",
    )
    return EMOJI_PASSWORD


async def emoji_receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Проверяем логин+пароль в Firebase, показываем эмодзи-кнопки."""
    password = update.message.text.strip()
    login = context.user_data.get("emoji_login", "")

    try:
        data = await fb_get("syrniki")
        users = data.get("users", {}) if data else {}
        if isinstance(users, list):
            users = {str(i): u for i, u in enumerate(users)}

        found_key = None
        found_user = None
        for k, u in users.items():
            if u.get("login", "").lower() == login and u.get("password") == password:
                found_key = k
                found_user = u
                break

        if not found_user:
            await update.message.reply_text(
                "❌ Неверный логин или пароль. Попробуйте ещё раз — нажмите /start"
            )
            return ConversationHandler.END

        # Store key for later
        context.user_data["emoji_user_key"] = found_key
        context.user_data["emoji_current"] = found_user.get("emoji", "")

        # Build emoji keyboard (5 per row)
        buttons = []
        row = []
        for i, e in enumerate(EMOJIS):
            row.append(InlineKeyboardButton(e, callback_data=f"setemoji_{e}"))
            if len(row) == 5:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="emoji_cancel")])

        current = found_user.get("emoji", "?")
        await update.message.reply_text(
            f"✅ Авторизован как <b>{found_user.get('display','')}</b>\n"
            f"Текущий эмодзи: {current}\n\n"
            "Выбери новый:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"emoji auth error: {e}")
        await update.message.reply_text("⚠️ Ошибка подключения к базе. Попробуйте позже.")

    return ConversationHandler.END


async def set_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пользователь выбрал эмодзи — сохраняем в Firebase."""
    query = update.callback_query
    await query.answer()

    chosen = query.data.replace("setemoji_", "", 1)

    # Safety check — no system emojis
    if chosen in EMOJIS_SYSTEM:
        await query.answer("⛔ Этот эмодзи недоступен", show_alert=True)
        return

    user_key = context.user_data.get("emoji_user_key")
    if not user_key:
        await query.edit_message_text("⚠️ Сессия истекла. Начните заново через /start")
        return

    try:
        data = await fb_get("syrniki")
        users = data.get("users", {}) if data else {}
        if isinstance(users, list):
            users = {str(i): u for i, u in enumerate(users)}

        if user_key not in users:
            await query.edit_message_text("❌ Пользователь не найден.")
            return

        users[user_key]["emoji"] = chosen
        await fb_set("syrniki/users", users)

        await query.edit_message_text(
            f"✅ Эмодзи обновлён на {chosen}!\n\nОткрой кошелёк чтобы увидеть изменения.",
            parse_mode="HTML",
        )
        context.user_data.pop("emoji_user_key", None)
        context.user_data.pop("emoji_current", None)

    except Exception as e:
        logger.error(f"set_emoji error: {e}")
        await query.edit_message_text("⚠️ Ошибка сохранения. Попробуйте позже.")


async def emoji_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Смена эмодзи отменена.")
    context.user_data.pop("emoji_user_key", None)

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
    context.user_data["display_name"] = display_name
    await update.message.reply_text(
        f"✅ Имя <b>{display_name}</b> принято!\n\n"
        "Придумайте <b>пароль</b> для входа на сайт:\n"
        "<i>Минимум 4 символа</i>",
        parse_mode="HTML",
    )
    return WAITING_PASSWORD


async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    password = update.message.text.strip()
    if len(password) < 4:
        await update.message.reply_text(
            "❌ Пароль слишком короткий. Минимум 4 символа.\n\nПопробуйте ещё раз:"
        )
        return WAITING_PASSWORD

    login = context.user_data.get("login", "—")
    display_name = context.user_data.get("display_name", "—")
    user = update.effective_user

    # Сохраняем данные пользователя
    user_data_store[user.id] = {
        "login": login,
        "display_name": display_name,
        "password": password,
        "telegram_id": user.id,
        "telegram_username": f"@{user.username}" if user.username else "—",
        "full_name": user.full_name,
    }

    tg_username = f"@{user.username}" if user.username else None
    tg_link = f"https://t.me/{user.username}" if user.username else f"tg://user?id={user.id}"

    # Кнопки для админа
    keyboard = [
        [
            InlineKeyboardButton("✅ Зарегистрировать", callback_data=f"register_{user.id}"),
            InlineKeyboardButton("☑️", callback_data=f"manualreg_{user.id}"),
        ],
        [InlineKeyboardButton("💬 Написать", url=tg_link)],
    ]

    admin_text = (
        "🆕 <b>Новая заявка на регистрацию</b>\n\n"
        f"👤 <b>Telegram:</b> {user.full_name}"
        + (f" ({tg_username})" if tg_username else "") + "\n"
        f"🆔 <b>Telegram ID:</b> <code>{user.id}</code>\n"
        f"🔑 <b>Логин:</b> <code>{login}</code>\n"
        f"📛 <b>Имя:</b> {display_name}\n"
        f"🔒 <b>Пароль:</b> <code>{password}</code>\n\n"
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

    password = info.get("password", "")
    tg_username = info.get("telegram_username", "")
    tg_link = f"https://t.me/{tg_username.lstrip('@')}" if tg_username and tg_username != "—" else f"tg://user?id={user_id}"

    # Register in Firebase
    try:
        ok = await register_in_firebase(login, display, user_id, password, tg_username)
    except Exception as e:
        logger.error(f"Firebase error: {e}")
        await query.edit_message_text(
            text=query.message.text + "\n\n❌ <b>Ошибка подключения к Firebase</b>",
            parse_mode="HTML",
        )
        return

    if not ok:
        await query.edit_message_text(
            text=query.message.text + f"\n\n⚠️ <b>Логин <code>{login}</code> уже занят!</b>",
            parse_mode="HTML",
        )
        return

    # Notify user with credentials
    keyboard = [[InlineKeyboardButton("🌐 Войти на сайт", url="https://syrnik-wallet.motya-solik.workers.dev/")]]
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 <b>Поздравляем! Вы зарегистрированы!</b>\n\n"
                f"🔑 <b>Логин:</b> <code>{login}</code>\n"
                f"🔒 <b>Пароль:</b> <code>{password}</code>\n\n"
                "Сохраните эти данные!\n"
                "Нажмите кнопку чтобы войти:"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

    # Update admin message — show start balance button
    await query.edit_message_text(
        text=query.message.text + "\n\n✅ <b>Зарегистрирован!</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🆙 Стартовый баланс", callback_data=f"startbal_{user_id}"),
            InlineKeyboardButton("💬 Написать", url=tg_link),
        ]])
    )




async def manual_register_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """☑️ — уведомляет пользователя об одобрении без создания аккаунта в Firebase."""
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_CHAT_ID:
        await query.answer("⛔ Только администратор.", show_alert=True)
        return

    user_id = int(query.data.split("_")[1])
    info = user_data_store.get(user_id)

    if not info:
        await query.answer("❌ Данные заявки не найдены", show_alert=True)
        return

    login = info["login"]
    password = info.get("password", "")
    tg_username = info.get("telegram_username", "")
    tg_link = f"https://t.me/{tg_username.lstrip('@')}" if tg_username and tg_username != "—" else f"tg://user?id={user_id}"

    # Notify user with credentials (same as auto-register)
    keyboard = [[InlineKeyboardButton("🌐 Войти на сайт", url="https://syrnik-wallet.motya-solik.workers.dev/")]]
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "🎉 <b>Поздравляем! Вы зарегистрированы!</b>\n\n"
                f"🔑 <b>Логин:</b> <code>{login}</code>\n"
                f"🔒 <b>Пароль:</b> <code>{password}</code>\n\n"
                "Сохраните эти данные!\n"
                "Нажмите кнопку чтобы войти:"
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления пользователя {user_id}: {e}")

    # Update admin message
    await query.edit_message_text(
        text=query.message.text + "\n\n☑️ <b>Уведомление отправлено</b> (аккаунт создайте вручную)",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🆙 Стартовый баланс", callback_data=f"startbal_{user_id}"),
            InlineKeyboardButton("💬 Написать", url=tg_link),
        ]])
    )

async def give_start_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Начислить 5 сырничков новому пользователю."""
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_CHAT_ID:
        await query.answer("⛔ Только администратор.", show_alert=True)
        return

    user_id = int(query.data.split("_")[1])

    try:
        data = await fb_get("syrniki")
        if not data:
            await query.answer("❌ Ошибка Firebase", show_alert=True)
            return

        users = data.get("users", {})
        if isinstance(users, list):
            users = {str(i): u for i, u in enumerate(users)}

        key = str(user_id)
        if key not in users:
            await query.answer("❌ Пользователь не найден в базе", show_alert=True)
            return

        users[key]["balance"] = users[key].get("balance", 0) + 5

        # Add transaction record
        from datetime import datetime
        now = datetime.now()
        time_str = now.strftime("%H:%M, %d.%m.%Y")
        txs = data.get("txs", {})
        if isinstance(txs, list):
            txs = {str(i): t for i, t in enumerate(txs)}
        new_tx = {
            "type": "plus",
            "desc": f"Начислено — {users[key].get('display', '')} · Стартовый баланс",
            "amt": "+5",
            "participants": [users[key].get("login", "")],
            "time": time_str,
        }
        txs[str(len(txs))] = new_tx

        await fb_set("syrniki/users", users)
        await fb_set("syrniki/txs", txs)

        # Notify user
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="🎁 <b>Тебе начислен стартовый баланс!</b>\n\n+5 🧀 сырничков на счёт!",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка уведомления о балансе: {e}")

        # Update button — replace with done label
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ +5 🧀 начислено", callback_data="noop"),
                InlineKeyboardButton("💬 Написать", url=f"tg://user?id={user_id}"),
            ]])
        )

    except Exception as e:
        logger.error(f"Ошибка start balance: {e}")
        await query.answer(f"⚠️ Ошибка: {e}", show_alert=True)




# ── Broadcast ──
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Admin starts broadcast — sends template with current version."""
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Только администратор.")
        return ConversationHandler.END

    # Fetch current version from Firebase
    version = "?"
    try:
        data = await fb_get("syrniki")
        version = data.get("version", "?") if data else "?"
    except Exception:
        pass

    template = (
        f"📢 <b>Обновление Сырники Wallet!</b>\n"
        f"🔖 <b>Версия: v{version}</b>\n\n"
        "🆕 <b>Что нового:</b>\n"
        "• \n"
        "• \n"
        "• \n\n"
        "🌐 Откройте приложение чтобы увидеть изменения!"
    )

    await update.message.reply_text(
        "📢 <b>Рассылка обновления</b>\n\n"
        f"Текущая версия: <b>v{version}</b>\n\n"
        "Отправь текст сообщения. Шаблон:\n\n"
        f"<code>{template}</code>\n\n"
        "/cancel — отмена.",
        parse_mode="HTML",
    )
    return BROADCAST_TEXT


async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive broadcast text and send to all users with telegramId."""
    text = update.message.text.strip()

    # Load users from Firebase
    try:
        data = await fb_get("syrniki")
        users = data.get("users", {}) if data else {}
        if isinstance(users, list):
            users = {str(i): u for i, u in enumerate(users)}
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка Firebase: {e}")
        return ConversationHandler.END

    tg_users = [(u["telegramId"], u.get("display","?")) for u in users.values() if u.get("telegramId")]

    if not tg_users:
        await update.message.reply_text("⚠️ Нет пользователей с Telegram ID.")
        return ConversationHandler.END

    keyboard = [[InlineKeyboardButton("🌐 Открыть кошелёк", url="https://syrnik-wallet.motya-solik.workers.dev/")]]

    sent, failed = 0, 0
    status_msg = await update.message.reply_text(f"📤 Отправляю {len(tg_users)} пользователям...")

    for tg_id, display in tg_users:
        try:
            await context.bot.send_message(
                chat_id=tg_id,
                text=text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard),
            )
            sent += 1
        except Exception as e:
            logger.warning(f"Не удалось отправить {display} ({tg_id}): {e}")
            failed += 1

    await status_msg.edit_text(
        f"✅ <b>Рассылка завершена!</b>\n\n"
        f"📨 Отправлено: {sent}\n"
        f"❌ Не удалось: {failed}",
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def approve_task_tg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin approves task from Telegram button."""
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_CHAT_ID:
        await query.answer("⛔ Только администратор.", show_alert=True)
        return

    sub_id = int(query.data.split("_")[1])

    try:
        data = await fb_get("syrniki")
        if not data:
            await query.answer("❌ Ошибка Firebase", show_alert=True)
            return

        subs = data.get("taskSubmissions", {})
        if isinstance(subs, list):
            subs = {str(i): s for i, s in enumerate(subs)}

        # Find submission
        sub_key = None
        sub = None
        for k, s in subs.items():
            if s.get("id") == sub_id:
                sub_key = k
                sub = s
                break

        if not sub:
            await query.answer("❌ Заявка не найдена", show_alert=True)
            return

        if sub.get("status") == "approved":
            await query.answer("✅ Уже подтверждено", show_alert=True)
            return

        sub["status"] = "approved"
        subs[sub_key] = sub

        # Add reward to user
        users = data.get("users", {})
        if isinstance(users, list):
            users = {str(i): u for i, u in enumerate(users)}
        user_id_str = str(sub["userId"])
        if user_id_str in users:
            users[user_id_str]["balance"] = users[user_id_str].get("balance", 0) + sub["taskReward"]

        # Add tx record
        from datetime import datetime
        now = datetime.now()
        time_str = now.strftime("%H:%M, %d.%m.%Y")
        txs = data.get("txs", {})
        if isinstance(txs, list):
            txs = {str(i): t for i, t in enumerate(txs)}
        tx_idx = len(txs)
        # Insert at front
        new_txs = {str(i+1): v for i, v in enumerate(txs.values())}
        new_txs["0"] = {
            "type": "plus",
            "desc": f"🏆 Задача выполнена — {sub['userDisplay']} · {sub['taskName']}",
            "amt": f"+{sub['taskReward']}",
            "participants": [sub["userLogin"]],
            "time": time_str,
        }

        await fb_set("syrniki/taskSubmissions", subs)
        await fb_set("syrniki/users", users)
        await fb_set("syrniki/txs", new_txs)

        # Notify user
        try:
            await context.bot.send_message(
                chat_id=sub["userId"],
                text=f"🎉 <b>Задача принята!</b>\n\n📌 «{sub['taskName']}»\n🏆 +{sub['taskReward']} 🧀 начислено!",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"notify user task approved: {e}")

        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(f"✅ Принято (+{sub['taskReward']} 🧀)", callback_data="noop"),
            ]])
        )

    except Exception as e:
        logger.error(f"approve_task_tg error: {e}")
        await query.answer(f"⚠️ Ошибка: {e}", show_alert=True)


async def reject_task_tg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin rejects task from Telegram button."""
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_CHAT_ID:
        await query.answer("⛔ Только администратор.", show_alert=True)
        return

    sub_id = int(query.data.split("_")[1])

    try:
        data = await fb_get("syrniki")
        subs = data.get("taskSubmissions", {}) if data else {}
        if isinstance(subs, list):
            subs = {str(i): s for i, s in enumerate(subs)}

        sub_key = None
        sub = None
        for k, s in subs.items():
            if s.get("id") == sub_id:
                sub_key = k; sub = s; break

        if not sub:
            await query.answer("❌ Заявка не найдена", show_alert=True)
            return

        sub["status"] = "rejected"
        subs[sub_key] = sub
        await fb_set("syrniki/taskSubmissions", subs)

        try:
            await context.bot.send_message(
                chat_id=sub["userId"],
                text=f"❌ <b>Задача отклонена</b>\n\n📌 «{sub['taskName']}»\nПопробуй ещё раз!",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"notify user task rejected: {e}")

        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✕ Отклонено", callback_data="noop"),
            ]])
        )

    except Exception as e:
        logger.error(f"reject_task_tg error: {e}")
        await query.answer(f"⚠️ Ошибка: {e}", show_alert=True)

async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()

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
            WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    emoji_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(emoji_change_start, pattern="^emoji_change$")],
        states={
            EMOJI_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, emoji_receive_login)],
            EMOJI_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, emoji_receive_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BROADCAST_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("msg", admin_reply))
    app.add_handler(conv_handler)
    app.add_handler(emoji_conv_handler)
    app.add_handler(broadcast_conv)
    app.add_handler(CallbackQueryHandler(set_emoji, pattern=r"^setemoji_.+$"))
    app.add_handler(CallbackQueryHandler(emoji_cancel, pattern="^emoji_cancel$"))
    # ВАЖНО: register_ должен быть до approve_ чтобы не перехватывало
    app.add_handler(CallbackQueryHandler(do_register_user, pattern=r"^register_\d+$"))
    app.add_handler(CallbackQueryHandler(give_start_balance, pattern=r"^startbal_\d+$"))
    app.add_handler(CallbackQueryHandler(manual_register_notify, pattern=r"^manualreg_\d+$"))
    app.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(approve_task_tg, pattern=r"^approvetask_\d+$"))
    app.add_handler(CallbackQueryHandler(reject_task_tg, pattern=r"^rejecttask_\d+$"))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.Chat(ADMIN_CHAT_ID),
        user_message_to_admin,
    ))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
