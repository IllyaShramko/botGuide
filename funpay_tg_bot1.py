# bot_email_only_mx.py
import json
import sqlite3
import random
import re
from pathlib import Path
from typing import Dict, Any
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import dns.resolver   # MX-проверка

from telegram import (
    InlineKeyboardButton, InlineKeyboardMarkup, Update,
    ReplyKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, ConversationHandler, filters
)

# =================== Настройки ===================
import os
from dotenv import load_dotenv

# Загружаем .env
load_dotenv()

# Telegram bot
BOT_TOKEN = os.getenv("BOT_TOKEN")

# SMTP
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_LOGIN = os.getenv("SMTP_LOGIN")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM")

# Поддержка
SUPPORT_CHAT_ID = int(os.getenv("SUPPORT_CHAT_ID", "0"))
REVIEWS_CHAT_LINK = os.getenv("REVIEWS_CHAT_LINK", "https://t.me/")


DATA_FILE = Path("data.json")
# =================================================

# Conversation states
ASK_EMAIL, ASK_EMAIL_CODE = range(2)
back_keyboard = ReplyKeyboardMarkup([["⬅️ Назад"]], resize_keyboard=True)

# Default data (услуги и т.д.)
DEFAULT_DATA = {
    "services": [
        {"id": 1, "title": "белые темки ", "price": "25тг звезд", "desc": "прим"},
        {"id": 2, "title": "темные темки", "price": "100тг звезд", "desc": "прим"},
        {"id": 3, "title": "Фермы аккаунтов,валют и тд", "price": "275 тг звезд", "desc": "прим"},
        {"id": 4, "title": "Steam-ФП", "price": "300", "desc":"прим"},
        {"id": 5, "title": "уник1", "price": "225", "desc":"прим"},
        {"id": 6, "title": "уник2", "price": "300", "desc":"прим"},
    ],
    "reviews": [],
    "orders": [],
}
START_TEXT = (
    "Это информационное табло по гайду заработок на фанпее\n\n"
    "Здесь вы можете ознакомиться с услугами, задать вопросы в F.A.Q, обратиться в поддержку или оставить отзыв.\n\n"
    "Выберите один из пунктов ниже:"
)

FAQ_TEXT = (
    "❓ Часто задаваемые вопросы:\n\n"
    "Как оформить заказ?\n➡️ Выберите услугу → нажмите Заказать.\n\n"
    "Как связаться с поддержкой?\n➡️ Нажмите Поддержка и напишите @mountideWW.\n"
)

SUPPORT_TEXT = (
    "Поддержка:\n\n"
    "Если у вас возникла проблема или вопросы, напишите сюда: @mountideWW\n\n"
)
# ---- Database helpers ----
def init_db():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER UNIQUE,
            username TEXT,
            email TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS email_confirmations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            email TEXT,
            code TEXT,
            confirmed INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            username TEXT,
            service_id INTEGER,
            service_title TEXT,
            price TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

def save_user(chat_id: int, username: str):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT OR IGNORE INTO users (chat_id, username, email)
        VALUES (?, ?, ?)
    """, (chat_id, username, None))
    conn.commit()
    conn.close()

# ---- JSON data ----
def load_data() -> Dict[str, Any]:
    if not DATA_FILE.exists():
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA.copy()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data: Dict[str, Any]):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ---- Email sending ----
def send_email_code(email: str, code: str) -> bool:
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_FROM
        msg["To"] = email
        msg["Subject"] = "Код подтверждения"
        body = f"Ваш код подтверждения: {code}"
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_LOGIN, SMTP_PASSWORD)
        server.sendmail(EMAIL_FROM, email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print("Ошибка при отправке письма:", e)
        return False

# ---- Email validation ----
EMAIL_RE = re.compile(r"(^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$)")

def is_valid_email_format(email: str) -> bool:
    return bool(EMAIL_RE.match(email.strip()))

def has_mx_record(email: str) -> bool:
    """ Проверка MX-записи домена """
    domain = email.split("@")[-1]
    try:
        answers = dns.resolver.resolve(domain, "MX")
        return len(answers) > 0
    except Exception:
        return False

# ---- Keyboards / menus ----
def main_menu():
    kb = [
        [InlineKeyboardButton("1. Услуги", callback_data="services")],
        [InlineKeyboardButton("2. F.A.Q", callback_data="faq")],
        [InlineKeyboardButton("3. Поддержка", callback_data="support")],
        [InlineKeyboardButton("4. Отзывы", url=REVIEWS_CHAT_LINK)],  # сразу ссылка
    ]
    return InlineKeyboardMarkup(kb)


def services_menu(store):
    kb = [[InlineKeyboardButton(f"{s['title']} — {s['price']}", callback_data=f"service_{s['id']}")] for s in store["services"]]
    kb.append([InlineKeyboardButton("⬅️ Назад", callback_data="back")])
    return InlineKeyboardMarkup(kb)

# ---- Handlers ----
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    username = update.effective_user.username or "NoUsername"
    save_user(chat_id, username)
    await update.message.reply_text(START_TEXT, reply_markup=main_menu())

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    store = load_data()

    if data == "services":
        await query.message.edit_text("Выберите услугу:", reply_markup=services_menu(store))
        return

    if data.startswith("service_"):
        sid = int(data.split("_")[1])
        s = next((x for x in store["services"] if x["id"] == sid), None)
        if not s:
            await query.message.edit_text("Услуга не найдена.")
            return
        text = f"{s['title']} — {s['price']}\n\n{s['desc']}"
        kb = [
            [InlineKeyboardButton("Заказать", callback_data=f"order_{sid}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="services")],
        ]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data.startswith("order_"):
        sid = int(data.split("_")[1])
        s = next((x for x in store["services"] if x["id"] == sid), None)
        if not s:
            await query.message.edit_text("Услуга не найдена.")
            return

        chat_id = query.from_user.id
        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        conn.close()

        if not row or not row[0]:
            text = (
                "✍️ Для завершения заказа нужно пройти верификацию почты.\n\n"
                "📧 Введи свой E-mail /setemail"
            )
            kb = [[InlineKeyboardButton("⬅️ Назад", callback_data="services")]]
            await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))
            return
        
        # Сохраняем заказ
        order = {
            "user_id": query.from_user.id,
            "user_name": query.from_user.full_name,
            "service": s
        }
        # Сохраняем заказ в БД
        conn = sqlite3.connect("users.db")
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO orders (chat_id, username, service_id, service_title, price)
            VALUES (?, ?, ?, ?, ?)
        """, (
            query.from_user.id,
            query.from_user.full_name,
            s["id"],
            s["title"],
            s["price"]
        ))
        conn.commit()
        conn.close()

        data_store = load_data()
        data_store["orders"].append(order)
        save_data(data_store)

        # Сообщение пользователю
        kb = [[InlineKeyboardButton("⬅️ Назад в меню", callback_data="back")]]
        await query.message.edit_text(
            f"Спасибо! Ваш заказ принят:\n"
            f"{s['title']} — {s['price']}\n"
            f"Отпишите в поддержку @mountideWW для оплаты и получения лота.",
            reply_markup=InlineKeyboardMarkup(kb)
        )

        # ⚡️ Уведомление админу
        await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            text=f"📩 Новый заказ!\n\n"
                 f"👤 Пользователь: {query.from_user.full_name} (@{query.from_user.username})\n"
                 f"🆔 ChatID: {query.from_user.id}\n"
                 f"📧 Email: {row[0]}\n"
                 f"🛒 Услуга: {s['title']} — {s['price']}"
        )


        # ⚡️ Уведомление админу
        await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            text=f"📩 Новый заказ!\n\n"
                 f"👤 Пользователь: {query.from_user.full_name} (@{query.from_user.username})\n"
                 f"🆔 ChatID: {query.from_user.id}\n"
                 f"📧 Email: {row[0]}\n"
                 f"🛒 Услуга: {s['title']} — {s['price']}"
        )
        return


    if data == "faq":
        await query.message.edit_text(FAQ_TEXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]))
        return

    if data == "support":
        await query.message.edit_text(SUPPORT_TEXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back")]]))
        return

    if data == "reviews":
        reviews = store.get("reviews", [])
        if not reviews:
            text = "Пока нет отзывов. Смотрите полный список и оставляйте в нашем Telegram-чате по ссылке ниже:"
        else:
            parts = [f"— {r['user_name']}: {r['text']}" for r in reviews[-10:]]
            text = "Отзывы:\n\n" + "\n\n".join(parts)
        kb = [
            [InlineKeyboardButton("Перейти в отзывы", url=REVIEWS_CHAT_LINK)],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back")],
        ]
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return

    if data == "back":
        await query.message.edit_text(START_TEXT, reply_markup=main_menu())
        return

    await query.message.edit_text("Неизвестная команда.")

# ---- Email verification flow ----
async def setemail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введите адрес электронной почты:", reply_markup=ReplyKeyboardRemove())
    return ASK_EMAIL

async def ask_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    email = update.message.text.strip()
    chat_id = update.effective_chat.id

    if not is_valid_email_format(email) or not has_mx_record(email):
        await update.message.reply_text("❌ Введите действительный адрес электронной почты (пример: user@gmail.com):", reply_markup=ReplyKeyboardRemove())
        return ASK_EMAIL

    code = str(random.randint(1000, 9999))

    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM email_confirmations WHERE chat_id = ?", (chat_id,))
    cursor.execute("INSERT INTO email_confirmations (chat_id, email, code, confirmed) VALUES (?, ?, ?, 0)", (chat_id, email, code))
    conn.commit()
    conn.close()

    if send_email_code(email, code):
        await update.message.reply_text("✅ Код подтверждения отправлен на почту!\nВведите код:", reply_markup=back_keyboard)
    else:
        await update.message.reply_text("❌ Ошибка при отправке письма. Проверьте настройки SMTP.", reply_markup=ReplyKeyboardRemove())

    return ASK_EMAIL_CODE

async def ask_email_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT email, code FROM email_confirmations WHERE chat_id = ? AND confirmed = 0", (chat_id,))
    row = cursor.fetchone()

    if row and row[1] == text:
        email = row[0]
        cursor.execute("UPDATE email_confirmations SET confirmed = 1 WHERE chat_id = ?", (chat_id,))
        cursor.execute("INSERT OR REPLACE INTO users (chat_id, username, email) VALUES (?, COALESCE((SELECT username FROM users WHERE chat_id = ?), ?), ?)",
                       (chat_id, chat_id, update.effective_user.username or "NoUsername", email))
        conn.commit()
        conn.close()

        store = load_data()
        await update.message.reply_text("✅ Верификация завершена! Теперь выберите услугу:", reply_markup=services_menu(store))
    else:
        await update.message.reply_text("❌ Неверный код, попробуйте снова через /setemail", reply_markup=ReplyKeyboardRemove())

    return ConversationHandler.END

# ---- Cancel ----
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Операция отменена ❌", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# ---- Fallback handlers ----
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Нажми на /start для открытия меню ")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Используй только кнопки!")

# ---- Run bot ----
def main():
    if BOT_TOKEN == "" or SMTP_LOGIN == "" or SMTP_PASSWORD == "":
        print("Заполни BOT_TOKEN, SMTP_LOGIN и SMTP_PASSWORD в начале файла.")
        return

    init_db()

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callback_handler))

    email_conv = ConversationHandler(
        entry_points=[CommandHandler("setemail", setemail)],
        states={
            ASK_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_email)],
            ASK_EMAIL_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_email_code)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(email_conv)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))

    print("Запуск бота...")
    app.run_polling()

if __name__ == "__main__":
    main()
