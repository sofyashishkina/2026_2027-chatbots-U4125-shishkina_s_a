"""Telegram-бот мастерской You Art. Запуск: python bot.py."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
import sqlite3
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Awaitable, Callable, TypeVar
from urllib.parse import urlparse

from dotenv import load_dotenv
from PIL import Image, UnidentifiedImageError
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
    Update,
    User,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    Defaults,
    MessageHandler,
    TypeHandler,
    filters,
)

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
DB_PATH = BASE_DIR / "data" / "bot.db"
LOGGER = logging.getLogger("you_art")
T = TypeVar("T")

# Тексты интерфейса собраны в одном месте. Контакты подставляются из .env.
TEXT = {
    "welcome": """Добро пожаловать в You Art 🤍

Мы — творческая мастерская в Санкт-Петербурге.

Здесь можно посмотреть наши мастер-классы, актуальные цены, афишу, узнать об условиях бронирования и организации праздников.

А ещё здесь мы будем делиться новостями мастерской, новыми направлениями, тематическими мастер-классами и событиями You Art.

Выберите нужный раздел ниже 👇""",
    "menu": "Выберите нужный раздел ниже 👇",
    "classes": """🎨 МАСТЕР-КЛАССЫ И ЦЕНЫ

Каталог мастер-классов с карточками, прайс и афиша групповых мастер-классов 👇""",
    "visit": """✍️ ЗАПИСЬ И БРОНИРОВАНИЕ

Как записаться, условия бронирования и ответы на частые вопросы 👇""",
    "gifts": """🎉 ПРАЗДНИКИ И ПОДАРКИ

Праздники в мастерской и подарочные сертификаты 👇""",
    "catalog": """🎨 КАТАЛОГ МАСТЕР-КЛАССОВ

В You Art более 20 творческих направлений.

Выберите мастер-класс ниже — пришлём карточку с описанием, длительностью, стоимостью и возрастом 🤍""",
    "price": """💰 ПРАЙС YOU ART

Здесь собрали актуальную стоимость наших мастер-классов 🤍

Стоимость указана за одного участника, если на карточке не указано иное.

Для больших групп и выездных мероприятий стоимость рассчитывается индивидуально.""",
    "price_empty": "Прайс сейчас обновляется 🤍\n\nАктуальную стоимость можно уточнить при записи.",
    "events": """📅 АФИША YOU ART

Здесь появляются групповые мастер-классы с готовыми датами и временем.

На них можно прийти одному, вдвоём или небольшой компанией 🤍""",
    "events_empty": """Сейчас в афише нет открытых групповых мастер-классов 🤍

Новые даты будут появляться здесь по мере формирования программы.

Следите за новостями бота — мы расскажем о новых мероприятиях.""",
    "party": """🎂 ПРАЗДНИКИ В YOU ART

В нашей мастерской можно провести день рождения, девичник, встречу с друзьями, свидание, семейный праздник или небольшое корпоративное мероприятие.

Вы выбираете один из 20+ мастер-классов, а мы готовим материалы и пространство к вашему приходу.

К мероприятию можно дополнительно добавить оформление, сервировку, кейтеринг, проектор, фото- и видеосъёмку.

В студии есть отдельная зона, где после мастер-класса можно продолжить праздник, пообщаться, принести торт, напитки и закуски.

В нашей мастерской комфортно размещаются компании до 18 человек.

Для больших групп мы можем организовать выездной мастер-класс на другой площадке.

📍 Санкт-Петербург
Суворовский проспект, 35
2 этаж, офис 208""",
    "signup": """✍️ ЗАПИСЬ НА МАСТЕР-КЛАСС

Мастер-классы в You Art проходят по предварительной записи.

У нас нет фиксированного расписания — вы выбираете удобную дату и время, а мы подтверждаем возможность проведения.

Запись на мастер-классы — от 2 человек.

Для подтверждения бронирования необходима предоплата 50%.

Чтобы записаться, напишите:

👉 {booking_link}

В сообщении укажите:
• какой мастер-класс вас интересует;
• желаемую дату;
• желаемое время;
• количество участников.

Если вы пока не определились с мастер-классом, сначала можно посмотреть каталог и прайс 🤍""",
    "booking": """📌 УСЛОВИЯ БРОНИРОВАНИЯ

В You Art нет фиксированного расписания мастер-классов.

Вы выбираете мастер-класс, удобную дату и время, после чего мы подтверждаем возможность проведения.

Запись на мастер-классы — от 2 человек.

Для подтверждения бронирования необходима предоплата 50%.

Все необходимые материалы для мастер-класса входят в стоимость, если в описании конкретного формата не указано иное.

В нашей мастерской комфортно размещаются компании до 18 человек.

Если участников больше, мы можем организовать выездной мастер-класс на другой площадке.""",
    "faq": """❓ ЧАСТЫЕ ВОПРОСЫ

<b>Как записаться?</b>
Напишите нам: {booking_link}
Укажите мастер-класс, желаемую дату, время и количество участников.

<b>Есть ли расписание?</b>
Фиксированного расписания нет — вы выбираете удобную дату и время, а мы подтверждаем возможность проведения. Групповые мастер-классы с готовыми датами появляются в Афише.

<b>Можно ли прийти одному?</b>
Запись на мастер-классы — от 2 человек. Одному можно прийти на групповой мастер-класс из Афиши.

<b>Сколько человек может быть в группе?</b>
От 2 до 17 человек. Для больших компаний организуем выездной мастер-класс на другой площадке.

<b>Нужна ли предоплата?</b>
Да, для подтверждения бронирования необходима предоплата 50%.

<b>Что входит в стоимость?</b>
Все необходимые материалы, если в описании формата не указано иное. Стоимость указана за одного участника.

<b>Сколько длится мастер-класс?</b>
Обычно от 1,5 до 3 часов. Тафтинг и плетение сумок из бусин — 2 занятия по 4 часа.

<b>С какого возраста можно?</b>
Зависит от мастер-класса: от 5 до 16 лет. Возраст указан в карточке каждого мастер-класса в каталоге.

<b>Можно ли прийти вдвоём?</b>
Да, есть парный мастер-класс: +1500 ₽ к любому мастер-классу, только для 2 участников, с романтической атмосферой.

<b>Можно ли отметить праздник?</b>
Да: день рождения, девичник, семейный праздник или корпоратив. Подробнее — в разделе «Праздники в You Art».

<b>Есть ли подарочные сертификаты?</b>
Да, подробнее — в разделе «Подарочные сертификаты».

<b>Где вы находитесь?</b>
Санкт-Петербург, Суворовский проспект, 35, 2 этаж, офис 208.""",
    "certificates":"""🎁 ПОДАРОЧНЫЕ СЕРТИФИКАТЫ YOU ART

В You Art можно приобрести подарочный сертификат на творческий мастер-класс.

Получатель сможет выбрать интересующий мастер-класс и согласовать удобную дату посещения.

Сертификат можно подарить близкому человеку, другу, коллеге или тому, кому хочется подарить новые впечатления и время для творчества 🤍

Для оформления сертификата напишите нам.""",
    "contacts": """📍 КАК НАС НАЙТИ

Творческая мастерская You Art

Санкт-Петербург
Суворовский проспект, 35
2 этаж
офис 208

☎️ {phone}

Для записи:
{booking_link}""",
    "error": "Что-то пошло не так 🤍\n\nПопробуйте ещё раз или вернитесь в главное меню.",
    "partial_media": "Некоторые изображения сейчас недоступны 🤍\nМы обновляем материалы. Попробуйте заглянуть позже.",
    "unknown": "Выберите раздел с помощью кнопок или команды /menu 🤍",
    "admin_only": "Эта команда доступна только администратору в личном чате с ботом.",
    "broadcast_prompt": "Отправьте сообщение, которое хотите отправить всем пользователям.",
    "broadcast_supported": "Отправьте одно текстовое сообщение, одну фотографию с подписью или одно видео с подписью. Для отмены — /cancel.",
    "broadcast_album": "Для рассылки отправьте одну фотографию или одно видео отдельным сообщением, без альбома. Для отмены — /cancel.",
    "preview": "Предварительный просмотр рассылки:",
    "confirm": "Отправить это сообщение всем активным пользователям?",
    "cancelled": "Рассылка отменена.",
    "no_draft": "Нет рассылки для отмены.",
    "stale_draft": "Это подтверждение уже недействительно. Создайте новую рассылку командой /broadcast.",
    "broadcast_running": "Рассылка уже выполняется. Дождитесь итогового сообщения.",
    "broadcast_started": "Начинаю рассылку. После завершения пришлю результат.",
    "broadcast_result": "Рассылка завершена.\n\nВсего пользователей: {total}\nУспешно отправлено: {success}\nНе удалось отправить: {failed}",
    "id": "Ваш Telegram ID: <code>{telegram_id}</code>",
    # Описание на пустом экране чата (до 512 символов) и «About» в профиле (до 120).
    "description": """Творческая мастерская You Art в Санкт-Петербурге 🤍

Здесь можно посмотреть каталог мастер-классов с ценами, прайс и афишу, узнать об условиях бронирования, праздниках и подарочных сертификатах, а также ответы на частые вопросы.

Запустите бота, чтобы открыть меню 👇""",
    "about": "Творческая мастерская You Art в Санкт-Петербурге: 20+ мастер-классов, праздники и подарочные сертификаты 🤍",
}

LABELS = {
    "menu": "⬅️ Главное меню",
    "classes": "🎨 Мастер-классы и цены",
    "visit": "✍️ Запись и бронирование",
    "gifts": "🎉 Праздники и подарки",
    "catalog": "🎨 Каталог мастер-классов",
    "price": "💰 Прайс",
    "events": "📅 Афиша",
    "party": "🎂 Праздники в You Art",
    "signup": "✍️ Запись",
    "booking": "📌 Условия бронирования",
    "faq": "❓ Частые вопросы",
    "certificates": "🎁 Подарочные сертификаты",
    "contacts": "📍 Контакты и как нас найти",
    "view_catalog": "🎨 Посмотреть мастер-классы",
    "back_catalog": "⬅️ Назад к каталогу",
    "sign_up": "✍️ Записаться",
    "write_signup": "✍️ Написать для записи",
    "write": "✍️ Написать",
    "vk": "ВКонтакте",
    "telegram": "Telegram",
    "map": "🗺 Как пройти",
    "send": "✅ Отправить всем",
    "cancel": "❌ Отмена",
}

COMMANDS = [
    BotCommand("start", "Запуск бота и главное меню"),
    BotCommand("menu", "Главное меню"),
    BotCommand("catalog", "Каталог мастер-классов"),
    BotCommand("price", "Прайс"),
    BotCommand("events", "Афиша"),
    BotCommand("party", "Праздники в You Art"),
    BotCommand("booking", "Условия бронирования"),
    BotCommand("faq", "Частые вопросы"),
    BotCommand("signup", "Запись на мастер-класс"),
    BotCommand("certificates", "Подарочные сертификаты"),
    BotCommand("contacts", "Контакты и адрес"),
    BotCommand("id", "Мой Telegram ID"),
]
SECTIONS = ("menu", "catalog", "price", "events", "party", "booking", "faq", "signup", "certificates", "contacts")
# Группы главного меню открываются кнопками; команды по-прежнему ведут прямо в разделы.
GROUPS = {
    "classes": ("catalog", "price", "events"),
    "visit": ("signup", "booking", "faq"),
    "gifts": ("party", "certificates"),
}


@dataclass(frozen=True)
class Workshop:
    slug: str
    label: str
    title: str
    photo: str
    result: str
    duration: str
    prices: tuple[str, ...]
    age: str = ""


# Данные взяты с карточек assets/catalog и из прайс-листа; возраст — только из прайса.
GROUP_SIZE = "от 2 до 17 человек"
WORKSHOPS = (
    Workshop("dessert", "🧁 Десертные свечи", "Десертные свечи", "1.jpg",
             "Делаем свечу в стакане: крем, декор, аромат и полный эффект маленького десерта.",
             "2 часа", ("2500 ₽",), "от 6 лет"),
    Workshop("painting", "🖼 Живопись", "Живопись на холсте", "2.jpg",
             "Пишем картину акрилом на холсте по выбранному референсу — мастер помогает с композицией, цветом и деталями.",
             "2,5 часа", ("холст 20×30 — 2400 ₽", "холст 30×40 — 2600 ₽", "холст 40×50 — 2900 ₽"), "от 12 лет"),
    Workshop("epoxy", "💎 Смола: 3 изделия", "Эпоксидная смола: 3 изделия", "3.jpg",
             "Создаём 3 изделия на выбор: кулоны, серьги, кольца, броши, брелоки, подставки, закладки, гребешки или шармы.",
             "2 часа", ("2700 ₽",), "от 10 лет"),
    Workshop("phone_case", "📱 Смола: чехол", "Эпоксидная смола: чехол на телефон", "4.jpg",
             "Декорируем чехол смолой, сухоцветами, блёстками и маленькими деталями под ваш стиль.",
             "1,5 часа", ("2600 ₽",)),
    Workshop("gypsum", "🏺 Гипсовые изделия", "Создание гипсовых изделий", "5.jpg",
             "Заливаем гипсовые формы и создаём 2 изделия на выбор: подставку, кашпо, шкатулку, поднос, органайзер или вазу.",
             "1,5 часа", ("2400 ₽",), "от 6 лет"),
    Workshop("gypsum_painting", "🎨 Роспись гипса", "Роспись гипсовых изделий", "6.jpg",
             "Расписываем гипсовое кашпо или подставку акрилом: выбираем палитру, рисунок и декоративные детали.",
             "2 часа", ("2100 ₽",), "от 6 лет"),
    Workshop("pillow", "🧶 Вязание подушки", "Вязание подушки", "7.jpg",
             "Вяжем мягкую интерьерную подушку 40×40 см руками — без спиц и крючка.",
             "2 часа", ("2100 ₽",), "от 12 лет"),
    Workshop("candles", "🕯 Создание свечей", "Создание свечей", "8.jpg",
             "Создаём 2 свечи: формовую свечу и контейнерную в гипсовом кашпо, которое тоже создаём на мастер-классе.",
             "2 часа", ("2700 ₽",), "от 6 лет"),
    Workshop("soap", "🧼 Мыловарение", "Мыловарение", "9.jpg",
             "Создаём 2 формовых мыла: выбираем форму, цвет, аромат и декоративные детали.",
             "2 часа", ("2400 ₽",), "от 5 лет"),
    Workshop("texture", "🖌 Фактурная картина", "Фактурная картина", "10.jpg",
             "Создаём объёмную интерьерную картину на холсте: работаем с пастой, мастихинами, фактурой и цветом.",
             "2,5 часа", ("2900 ₽",), "от 12 лет"),
    Workshop("custom", "👕 Кастом одежды", "Кастом шоппера, футболки или кепки", "11.jpg",
             "Создаём свой дизайн на ткани: выбираем референс, переносим рисунок и расписываем изделие.",
             "2,5 часа", ("шоппер — 2400 ₽", "футболка — 3300 ₽", "кепка — 2900 ₽"), "от 6 лет"),
    Workshop("clay", "🍄 Полимерная глина", "Полимерная глина", "12.jpg",
             "Лепим 2 изделия из полимерной глины: кольцо, брелок, кулон, серьги или маленькую фигурку.",
             "2 часа", ("1900 ₽",), "от 6 лет"),
    Workshop("watercolor", "💧 Акварельный этюд", "Акварельный этюд", "13.jpg",
             "Создаём 2 лёгкие акварельные работы: мягкие переходы, нежные оттенки и аккуратные детали.",
             "2 часа", ("2100 ₽",), "от 6 лет"),
    Workshop("wreath", "🎄 Новогодний венок", "Новогодний венок", "14.jpg",
             "Создаём декоративный венок для дома: собираем основу, добавляем хвойные элементы, ленты, шишки, ягоды и праздничный декор.",
             "2 часа", ("2300 ₽",), "от 6 лет"),
    Workshop("tiffany", "🔷 Тиффани", "Витражное украшение в технике «Тиффани»", "15.jpg",
             "Создаём кулон или брелок из стекла в технике Тиффани: форма согласуется заранее.",
             "3 часа", ("3300 ₽",), "от 16 лет"),
    Workshop("tufting", "🧵 Тафтинг", "Тафтинг", "16.jpg",
             "Создаём мини-коврик 20×20 см вручную: выбираем рисунок, цвета и работаем с мягкой фактурой.",
             "2 занятия по 4 часа", ("5000 ₽",), "от 12 лет"),
    Workshop("beads", "👜 Сумка из бусин", "Плетение сумок из бусин", "17.jpg",
             "Плетём сумку из бусин по выбранной форме и цветовой гамме — стильный аксессуар своими руками.",
             "2 занятия по 4 часа", ("6500 ₽",), "от 12 лет"),
)
WORKSHOPS_BY_SLUG = {workshop.slug: workshop for workshop in WORKSHOPS}


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_id: int
    booking_contact: str
    booking_url: str
    phone: str
    vk_url: str = ""
    telegram_url: str = ""
    map_url: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        # Путь не зависит от папки, из которой запускают бота.
        load_dotenv(BASE_DIR / ".env", encoding="utf-8-sig")
        required = ("BOT_TOKEN", "ADMIN_ID", "BOOKING_CONTACT", "BOOKING_URL", "PHONE")
        values = {name: os.getenv(name, "").strip() for name in required}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError("Заполните в lab1/.env: " + ", ".join(missing))
        if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,}", values["BOT_TOKEN"]):
            raise ValueError("BOT_TOKEN должен содержать токен, полученный у BotFather.")
        try:
            admin_id = int(values["ADMIN_ID"])
        except ValueError as exc:
            raise ValueError("ADMIN_ID должен быть положительным числовым Telegram ID.") from exc
        if admin_id <= 0:
            raise ValueError("ADMIN_ID должен быть положительным числовым Telegram ID.")
        urls = {name: os.getenv(name, "").strip() for name in ("BOOKING_URL", "VK_URL", "TELEGRAM_URL", "MAP_URL")}
        for name, value in urls.items():
            if value:
                parsed = urlparse(value)
                if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
                    raise ValueError(f"{name} должен быть полной ссылкой https://… или http://…")
        return cls(
            bot_token=values["BOT_TOKEN"], admin_id=admin_id,
            booking_contact=values["BOOKING_CONTACT"], booking_url=urls["BOOKING_URL"],
            phone=values["PHONE"], vk_url=urls["VK_URL"],
            telegram_url=urls["TELEGRAM_URL"], map_url=urls["MAP_URL"],
        )


class UserStore:
    """Небольшая постоянная база; все операции выполняются в потоке бота."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        with self.connection:
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER NOT NULL UNIQUE,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                    blocked_at TEXT
                )
            """)

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="microseconds")

    def register(self, user: User) -> None:
        now = self.now()
        with self.connection:
            self.connection.execute("""
                INSERT INTO users (telegram_id, username, first_name, last_name,
                                   first_seen_at, last_seen_at, is_active)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username=excluded.username, first_name=excluded.first_name,
                    last_name=excluded.last_name, last_seen_at=excluded.last_seen_at,
                    is_active=1, blocked_at=NULL
            """, (user.id, user.username, user.first_name, user.last_name, now, now))

    def touch(self, user: User) -> None:
        # Просмотр меню сам по себе не создаёт подписку и не отменяет блокировку.
        with self.connection:
            self.connection.execute("""
                UPDATE users SET username=?, first_name=?, last_name=?, last_seen_at=?
                WHERE telegram_id=?
            """, (user.username, user.first_name, user.last_name, self.now(), user.id))

    def deactivate(self, telegram_id: int) -> None:
        with self.connection:
            self.connection.execute("""
                UPDATE users SET is_active=0, blocked_at=COALESCE(blocked_at, ?)
                WHERE telegram_id=?
            """, (self.now(), telegram_id))

    def active_ids(self) -> list[int]:
        return [row[0] for row in self.connection.execute(
            "SELECT telegram_id FROM users WHERE is_active=1 ORDER BY id"
        )]

    def close(self) -> None:
        # sqlite3.Connection.close() допускает повторный вызов.
        self.connection.close()


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return re.sub(r"[0-9]+:[A-Za-z0-9_-]{20,}", "<BOT_TOKEN>", rendered)


def configure_logging() -> None:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    logfile = RotatingFileHandler(log_dir / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    for handler in (console, logfile):
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=[console, logfile], force=True)
    # HTTP-запросы могут содержать токен в URL.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def section_button(section: str, label: str | None = None) -> InlineKeyboardButton:
    return InlineKeyboardButton(label or LABELS[section], callback_data=f"section:{section}")


def keyboard(settings: Settings, section: str = "menu") -> InlineKeyboardMarkup:
    if section == "menu":
        return InlineKeyboardMarkup([[section_button(name)] for name in (*GROUPS, "contacts")])
    rows = []
    if section in GROUPS:
        rows = [[section_button(name)] for name in GROUPS[section]]
    elif section == "catalog":
        # Короткие подписи помещаются по две в ряд даже на узком экране.
        buttons = [InlineKeyboardButton(workshop.label, callback_data=f"workshop:{workshop.slug}")
                   for workshop in WORKSHOPS]
        rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    elif section == "price":
        rows = [[section_button("signup")], [section_button("catalog")]]
    elif section == "events":
        rows = [[section_button("signup")]]
    elif section == "booking":
        rows = [[section_button("signup")], [section_button("faq")]]
    elif section == "faq":
        rows = [[InlineKeyboardButton(LABELS["sign_up"], url=settings.booking_url)], [section_button("catalog")]]
    elif section == "party":
        rows = [[section_button("catalog", LABELS["view_catalog"])],
                [section_button("signup", LABELS["sign_up"])], [section_button("booking")]]
    elif section == "signup":
        rows = [[InlineKeyboardButton(LABELS["write_signup"], url=settings.booking_url)],
                [section_button("catalog")], [section_button("price")]]
    elif section == "certificates":
        rows = [[InlineKeyboardButton(LABELS["write"], url=settings.booking_url)],
                [section_button("catalog", LABELS["view_catalog"])]]
    elif section == "contacts":
        rows = [[InlineKeyboardButton(LABELS["signup"], url=settings.booking_url)]]
        for label, url in (("vk", settings.vk_url), ("telegram", settings.telegram_url), ("map", settings.map_url)):
            if url:
                rows.append([InlineKeyboardButton(LABELS[label], url=url)])
    rows.append([section_button("menu")])
    return InlineKeyboardMarkup(rows)


def workshop_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(LABELS["sign_up"], url=settings.booking_url)],
        [section_button("catalog", LABELS["back_catalog"])],
    ])


def workshop_caption(workshop: Workshop) -> str:
    if len(workshop.prices) == 1:
        price = f"💰 Стоимость: {escape(workshop.prices[0])} за участника"
    else:
        price = "💰 Стоимость за участника:\n" + "\n".join(f"• {escape(item)}" for item in workshop.prices)
    lines = [
        f"🎨 <b>{escape(workshop.title)}</b>", "", escape(workshop.result), "",
        f"⏱ Длительность: {escape(workshop.duration)}", price, f"👥 Группа: {GROUP_SIZE}",
    ]
    if workshop.age:
        lines.append(f"🎂 Возраст: {escape(workshop.age)}")
    return "\n".join(lines)


async def call_with_retry(operation: Callable[[], Awaitable[T]]) -> T:
    """Повторяем только явный отказ из-за лимита, без дублей после таймаута."""
    for attempt in range(3):
        try:
            return await operation()
        except RetryAfter as exc:
            if attempt == 2:
                raise
            delay = exc.retry_after
            seconds = delay.total_seconds() if isinstance(delay, timedelta) else float(delay)
            LOGGER.warning("Лимит Telegram, повтор через %.1f с", seconds)
            await asyncio.sleep(seconds + 0.1)
    raise RuntimeError("Не удалось выполнить запрос")  # Недостижимо, для проверки типов.


def load_images(directory: Path) -> list[Path]:
    try:
        return sorted((path for path in directory.iterdir()
                       if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}),
                      key=lambda path: path.name.casefold())
    except OSError:
        LOGGER.warning("Не удалось прочитать папку изображений: %s", directory, exc_info=True)
        return []


def read_photo(path: Path) -> bytes | None:
    """Проверяем файл до отправки: битая карточка не прерывает весь каталог."""
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("Фотография больше 10 МБ")
        data = path.read_bytes()
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                if image.format not in {"JPEG", "PNG"}:
                    raise ValueError("Ожидается JPEG или PNG")
                if width + height > 10_000 or max(width, height) / min(width, height) > 20:
                    raise ValueError("Размеры фотографии превышают ограничения Telegram")
                image.verify()
            # verify проверяет структуру, load — фактическое декодирование пикселей.
            with Image.open(BytesIO(data)) as image:
                image.load()
        return data
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        LOGGER.warning("Пропускаю недоступное изображение %s", path, exc_info=True)
        return None


async def send_images(bot, chat_id: int, paths: list[Path]) -> int:
    sent = 0
    for offset in range(0, len(paths), 10):
        batch = []
        for path in paths[offset:offset + 10]:
            data = await asyncio.to_thread(read_photo, path)
            if data is not None:
                batch.append((path, data))
        if not batch:
            continue
        if len(batch) > 1:
            try:
                await call_with_retry(lambda: bot.send_media_group(
                    chat_id=chat_id, media=[InputMediaPhoto(data) for _, data in batch]))
                sent += len(batch)
                continue
            except BadRequest:
                # Telegram может отвергнуть изображение, которое Pillow прочитал.
                # Пробуем карточки по одной, сохраняя порядок.
                LOGGER.warning("Альбом отклонён Telegram, пробую отдельные фотографии", exc_info=True)
        for path, data in batch:
            try:
                await call_with_retry(lambda: bot.send_photo(chat_id=chat_id, photo=data))
                sent += 1
            except BadRequest:
                LOGGER.warning("Telegram отклонил фотографию %s", path, exc_info=True)
    return sent


async def send_text(context, chat_id: int, text: str, section: str | None = None):
    markup = keyboard(context.bot_data["settings"], section) if section else None
    return await call_with_retry(lambda: context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup))


async def show_section(update: Update, context: ContextTypes.DEFAULT_TYPE, section: str) -> None:
    chat_id = update.effective_chat.id
    settings = context.bot_data["settings"]
    if section == "menu" or section in GROUPS:
        await send_text(context, chat_id, TEXT[section], section)
    elif section == "catalog":
        # Только список названий: карточка приходит после выбора мастер-класса.
        await send_text(context, chat_id, TEXT["catalog"], "catalog")
    elif section == "events":
        await send_text(context, chat_id, TEXT["events"])
        paths = load_images(ASSETS_DIR / "events")
        sent = await send_images(context.bot, chat_id, paths)
        if not sent:
            await send_text(context, chat_id, TEXT["events_empty"], "events")
        else:
            text = TEXT["partial_media"] if sent < len(paths) else TEXT["menu"]
            await send_text(context, chat_id, text, "events")
    elif section == "price":
        await send_text(context, chat_id, TEXT["price"])
        # Прайс показываем целиком: обе готовые карточки одним альбомом.
        photos = [await asyncio.to_thread(read_photo, ASSETS_DIR / "price" / name)
                  for name in ("price_1.jpg", "price_2.jpg")]
        if all(photo is not None for photo in photos):
            try:
                await call_with_retry(lambda: context.bot.send_media_group(
                    chat_id=chat_id, media=[InputMediaPhoto(photo) for photo in photos]))
            except BadRequest:
                LOGGER.warning("Telegram отклонил изображения прайса", exc_info=True)
                await send_text(context, chat_id, TEXT["price_empty"], "price")
            else:
                await send_text(context, chat_id, TEXT["menu"], "price")
        else:
            await send_text(context, chat_id, TEXT["price_empty"], "price")
    else:
        # Не вставляем данные из настроек в HTML без экранирования.
        booking_link = f'<a href="{escape(settings.booking_url, quote=True)}">{escape(settings.booking_contact)}</a>'
        text = TEXT[section].format(booking_link=booking_link, phone=escape(settings.phone))
        await send_text(context, chat_id, text, section)


async def track_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat and update.effective_chat.type == ChatType.PRIVATE and update.effective_user:
        context.bot_data["store"].touch(update.effective_user)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.bot_data["store"].register(update.effective_user)
    context.user_data.pop("broadcast_draft", None)
    context.user_data.pop("broadcast_waiting", None)
    await send_text(context, update.effective_chat.id, TEXT["welcome"], "menu")


async def section_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    section = update.effective_message.text.split()[0].split("@")[0].removeprefix("/").lower()
    await show_section(update, context, section)


async def section_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    section = query.data.partition(":")[2]
    if section not in SECTIONS and section not in GROUPS:
        await send_text(context, update.effective_chat.id, TEXT["error"], "menu")
        return
    await show_section(update, context, section)


async def show_workshop(update: Update, context: ContextTypes.DEFAULT_TYPE, slug: str) -> None:
    chat_id = update.effective_chat.id
    workshop = WORKSHOPS_BY_SLUG.get(slug)
    if workshop is None:
        # Например, кнопка из старого сообщения после обновления каталога.
        await send_text(context, chat_id, TEXT["error"], "menu")
        return
    caption = workshop_caption(workshop)
    markup = workshop_keyboard(context.bot_data["settings"])
    photo = await asyncio.to_thread(read_photo, ASSETS_DIR / "catalog" / workshop.photo)
    if photo is not None:
        try:
            await call_with_retry(lambda: context.bot.send_photo(
                chat_id=chat_id, photo=photo, caption=caption, reply_markup=markup))
            return
        except BadRequest:
            LOGGER.warning("Telegram отклонил фотографию мастер-класса %s", slug, exc_info=True)
    # Без фотографии карточка остаётся полезной: те же данные и кнопки текстом.
    await call_with_retry(lambda: context.bot.send_message(chat_id=chat_id, text=caption, reply_markup=markup))


async def workshop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    await show_workshop(update, context, query.data.partition(":")[2])


async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await send_text(context, update.effective_chat.id, TEXT["id"].format(telegram_id=update.effective_user.id))


def is_admin(update: Update, context) -> bool:
    return bool(update.effective_user and update.effective_chat
                and update.effective_chat.type == ChatType.PRIVATE
                and update.effective_user.id == context.bot_data["settings"].admin_id)


@dataclass(frozen=True)
class BroadcastDraft:
    source_chat_id: int
    source_message_id: int
    token: str
    confirmation_message_id: int = 0


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update, context):
        await send_text(context, update.effective_chat.id, TEXT["admin_only"])
        return
    if context.bot_data.get("broadcast_running"):
        await send_text(context, update.effective_chat.id, TEXT["broadcast_running"])
        return
    context.user_data.pop("broadcast_draft", None)
    context.user_data["broadcast_waiting"] = True
    await send_text(context, update.effective_chat.id, TEXT["broadcast_prompt"])


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update, context):
        await send_text(context, update.effective_chat.id, TEXT["admin_only"])
        return
    if context.bot_data.get("broadcast_running"):
        await send_text(context, update.effective_chat.id, TEXT["broadcast_running"])
        return
    draft = context.user_data.pop("broadcast_draft", None)
    waiting = context.user_data.pop("broadcast_waiting", False)
    if draft:
        await remove_confirmation(context.bot, draft)
    await send_text(context, update.effective_chat.id, TEXT["cancelled" if draft or waiting else "no_draft"])


async def receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not is_admin(update, context) or not context.user_data.get("broadcast_waiting"):
        await send_text(context, update.effective_chat.id, TEXT["unknown"], "menu")
        return
    if message.media_group_id:
        await send_text(context, update.effective_chat.id, TEXT["broadcast_album"])
        return
    if not (message.text or message.photo or message.video):
        await send_text(context, update.effective_chat.id, TEXT["broadcast_supported"])
        return
    await send_text(context, message.chat_id, TEXT["preview"])
    # Telegram сам сохраняет entities и подписи; пользовательский HTML не разбираем.
    preview = await call_with_retry(lambda: context.bot.copy_message(
        chat_id=message.chat_id, from_chat_id=message.chat_id, message_id=message.message_id))
    token = secrets.token_hex(8)
    confirmation = await call_with_retry(lambda: context.bot.send_message(
        chat_id=message.chat_id, text=TEXT["confirm"], reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(LABELS["send"], callback_data=f"broadcast:send:{token}")],
            [InlineKeyboardButton(LABELS["cancel"], callback_data=f"broadcast:cancel:{token}")],
        ])))
    context.user_data["broadcast_draft"] = BroadcastDraft(
        message.chat_id, preview.message_id, token, confirmation.message_id)
    context.user_data["broadcast_waiting"] = False


async def remove_confirmation(bot, draft: BroadcastDraft) -> None:
    try:
        await bot.edit_message_reply_markup(chat_id=draft.source_chat_id,
                                            message_id=draft.confirmation_message_id, reply_markup=None)
    except TelegramError:
        LOGGER.warning("Не удалось убрать кнопки подтверждения", exc_info=True)


async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_admin(update, context):
        await query.answer(TEXT["admin_only"], show_alert=True)
        return
    await query.answer()
    draft = context.user_data.get("broadcast_draft")
    parts = query.data.split(":")
    if (len(parts) != 3 or parts[1] not in ("send", "cancel") or not draft
            or parts[2] != draft.token or query.message.message_id != draft.confirmation_message_id):
        await send_text(context, update.effective_chat.id, TEXT["stale_draft"])
        return
    if context.bot_data.get("broadcast_running"):
        await send_text(context, update.effective_chat.id, TEXT["broadcast_running"])
        return
    # Удаляем черновик ДО первого await: повторное нажатие не запустит дубль.
    context.user_data.pop("broadcast_draft", None)
    context.user_data["broadcast_waiting"] = False
    if parts[1] == "cancel":
        await remove_confirmation(context.bot, draft)
        await send_text(context, update.effective_chat.id, TEXT["cancelled"])
        return
    context.bot_data["broadcast_running"] = True
    # Зарегистрированную задачу Application дождётся при корректном завершении.
    context.application.create_task(run_broadcast(context.application, draft, update.effective_chat.id), update=update)
    await remove_confirmation(context.bot, draft)


def cannot_receive(error: BadRequest) -> bool:
    text = str(error).lower()
    return any(reason in text for reason in ("chat not found", "user is deactivated", "peer_id_invalid", "bot was blocked"))


async def run_broadcast(application: Application, draft: BroadcastDraft, admin_chat_id: int) -> None:
    store = application.bot_data["store"]
    bot = application.bot
    success = 0
    failed = 0
    try:
        recipients = store.active_ids()
        try:
            await bot.send_message(chat_id=admin_chat_id, text=TEXT["broadcast_started"])
        except TelegramError:
            LOGGER.warning("Не удалось сообщить о начале рассылки", exc_info=True)
        for telegram_id in recipients:
            try:
                await call_with_retry(lambda: bot.copy_message(
                    chat_id=telegram_id, from_chat_id=draft.source_chat_id, message_id=draft.source_message_id))
                success += 1
            except Forbidden:
                failed += 1
                store.deactivate(telegram_id)
                LOGGER.warning("Получатель %s больше не принимает сообщения", telegram_id, exc_info=True)
            except BadRequest as exc:
                failed += 1
                if cannot_receive(exc):
                    store.deactivate(telegram_id)
                LOGGER.warning("Ошибка рассылки получателю %s", telegram_id, exc_info=True)
            except NetworkError:
                failed += 1
                LOGGER.warning("Временная ошибка сети для получателя %s", telegram_id, exc_info=True)
            except Exception:
                failed += 1
                LOGGER.exception("Не удалось отправить рассылку получателю %s", telegram_id)
            # Не превышаем обычный бесплатный лимит Telegram; RetryAfter учитывается отдельно.
            await asyncio.sleep(0.05)
        summary = TEXT["broadcast_result"].format(total=len(recipients), success=success, failed=failed)
        LOGGER.info("Рассылка: всего=%s, успешно=%s, ошибки=%s", len(recipients), success, failed)
        await call_with_retry(lambda: bot.send_message(chat_id=admin_chat_id, text=summary))
    finally:
        application.bot_data["broadcast_running"] = False


async def blocked_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    member = update.my_chat_member
    if member.chat.type == ChatType.PRIVATE and member.new_chat_member.status == "kicked":
        context.bot_data["store"].deactivate(member.chat.id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    LOGGER.error("Необработанная ошибка", exc_info=(type(error), error, error.__traceback__))
    if not isinstance(update, Update) or not update.effective_chat:
        return
    if update.effective_chat.type != ChatType.PRIVATE:
        return
    if isinstance(error, Forbidden) or (isinstance(error, BadRequest) and cannot_receive(error)):
        context.bot_data["store"].deactivate(update.effective_chat.id)
        return
    try:
        await send_text(context, update.effective_chat.id, TEXT["error"], "menu")
    except TelegramError:
        LOGGER.warning("Не удалось отправить уведомление об ошибке", exc_info=True)


async def post_init(application: Application) -> None:
    try:
        await application.bot.set_my_commands(COMMANDS)
        await application.bot.set_my_commands(
            COMMANDS + [BotCommand("broadcast", "Создать общую рассылку"), BotCommand("cancel", "Отменить черновик рассылки")],
            scope=BotCommandScopeChat(application.bot_data["settings"].admin_id),
        )
    except TelegramError:
        # Администратор мог ещё не открыть личный чат: работа остальных разделов доступна.
        LOGGER.warning("Не удалось настроить список команд Telegram", exc_info=True)
    try:
        # То же, что /setdescription и /setabouttext в BotFather, но тексты хранятся в коде.
        await application.bot.set_my_description(TEXT["description"])
        await application.bot.set_my_short_description(TEXT["about"])
    except TelegramError:
        LOGGER.warning("Не удалось обновить описание бота", exc_info=True)
    LOGGER.info("Бот запущен, база пользователей: %s", DB_PATH)


async def post_shutdown(application: Application) -> None:
    application.bot_data["store"].close()
    LOGGER.info("Бот остановлен, база данных закрыта")


def build_application(settings: Settings, store: UserStore) -> Application:
    application = (Application.builder().token(settings.bot_token)
                   .defaults(Defaults(parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)))
                   .concurrent_updates(False).post_init(post_init).post_shutdown(post_shutdown).build())
    application.bot_data.update(settings=settings, store=store, broadcast_running=False)
    application.add_handler(TypeHandler(Update, track_activity), group=-1)
    private = filters.ChatType.PRIVATE
    application.add_handler(CommandHandler("start", start, filters=private))
    application.add_handler(CommandHandler(list(SECTIONS), section_command, filters=private))
    application.add_handler(CommandHandler("id", id_command, filters=private))
    # Эти команды проверяют и ID, и тип чата внутри обработчика.
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("cancel", cancel_command))
    application.add_handler(CallbackQueryHandler(broadcast_callback, pattern=r"^broadcast:"))
    application.add_handler(CallbackQueryHandler(section_callback, pattern=r"^section:"))
    application.add_handler(CallbackQueryHandler(workshop_callback, pattern=r"^workshop:"))
    application.add_handler(ChatMemberHandler(blocked_update, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(MessageHandler(private & ~filters.COMMAND, receive_message))
    application.add_handler(MessageHandler(private & filters.COMMAND, receive_message))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    configure_logging()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        LOGGER.error("Ошибка настройки: %s", exc)
        raise SystemExit(1) from None
    store = UserStore(DB_PATH)
    try:
        application = build_application(settings, store)
        options = {"stop_signals": None} if os.name == "nt" else {}
        application.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER], **options)
    except TelegramError:
        LOGGER.exception("Не удалось запустить бота. Проверьте токен и подключение к Telegram.")
        raise SystemExit(1) from None
    finally:
        store.close()


if __name__ == "__main__":
    main()
