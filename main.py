# -*- coding: utf-8 -*-
import asyncio, logging, json, os, re, shutil, sys, locale
import functools, sqlite3, aiosqlite, pytz
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F, md
from aiogram.filters import Command, CommandStart, BaseFilter, CommandObject
from aiogram.filters.callback_data import CallbackData
#from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery, ForceReply
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import ReplyKeyboardBuilder, KeyboardButton


# ---- НОВЫЕ ИМПОРТЫ ДЛЯ ВЕБХУКОВ ----
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

# Для генерации подписи Robokassa (примерный, нужно проверить актуальность)
#import decimal
import hashlib
#from urllib import parse
#from urllib.parse import urlparse

# Фикс кодировки для консоли Windows
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Глобальные переменные уровня модуля (объявляем типы для ясности)
bot: Bot
dp: Dispatcher
settings: dict
COURSE_GROUPS: list

# Конфигурационные переменные, которые станут глобальными после загрузки
# Они будут загружены из os.getenv() в функции main()
BOT_TOKEN_CONF: str
ADMIN_IDS_CONF: list[int] = []

# Имена ниже соответствуют вашему .env
WEBHOOK_HOST_CONF: str       # Публичный URL (BASE_PUBLIC_URL)
WEBAPP_PORT_CONF: int        # Внутренний порт приложения (INTERNAL_APP_PORT)
WEBAPP_HOST_CONF: str        # Внутренний хост приложения (INTERNAL_APP_HOST)
WEBHOOK_PATH_CONF: str       # Базовый путь вебхука (BASE_WEBHOOK_PATH)

# Загрузка переменных из .env
load_dotenv()

# Инициализация определителя часовых поясов

DEFAULT_TIMEZONE = "Europe/Moscow"  # Установка часового пояса по умолчанию

# Установка локали для русского языка
locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')

MAX_LOG_SIZE = 50 * 1024  # 50 kB
LOG_BACKUP_COUNT = 1

class LocalTimeFormatter(logging.Formatter):
    # Укажите ваш целевой часовой пояс
    default_tz = pytz.timezone('Europe/Moscow')  # Например, Москва (UTC+3)

    def formatTime(self, record, datefmt=None):
        # record.created - это timestamp (время создания записи лога в UTC)
        ct = datetime.fromtimestamp(record.created, tz=pytz.utc)
        # Конвертируем в ваш целевой часовой пояс
        ct_local = ct.astimezone(self.default_tz)
        if datefmt:
            s = ct_local.strftime(datefmt)
        else:
            try:
                s = ct_local.isoformat(timespec='milliseconds')
            except TypeError:
                s = ct_local.isoformat()
        return s


def setup_logging():
    """Настройка логирования с ротацией и UTF-8 и локальным временем"""
    log_file = 'bot.log'

    # Создаем форматтеры
    # server_formatter = logging.Formatter('%(asctime)s %(lineno)d [%(funcName)s] - %(message)s  %(levelname)s', datefmt='%H:%M:%S')
    local_time_formatter = LocalTimeFormatter('%(asctime)s %(lineno)d [%(funcName)s] - %(message)s  %(levelname)s',
                                              datefmt='%H:%M:%S')

    # RotatingFileHandler будет использовать ваш кастомный форматтер
    rotating_handler = RotatingFileHandler(
        log_file,
        maxBytes=MAX_LOG_SIZE,
        backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8'
    )
    rotating_handler.setFormatter(local_time_formatter)

    # StreamHandler может использовать стандартный форматтер или тоже кастомный
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(local_time_formatter)  # или server_formatter для времени сервера в консоли

    logging.basicConfig(
        level=logging.INFO,
        # format и datefmt здесь будут переопределены хэндлерами, если у них свои форматтеры
        handlers=[rotating_handler, stream_handler]
    )

setup_logging()
logger = logging.getLogger(__name__)  # Создание логгера для текущего модуля

# == Константы и конфиг ==
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения.")
logger.info(f"BOT_TOKEN: {BOT_TOKEN}")

ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID', 0))
logger.info(f"ADMIN_GROUP_ID: {ADMIN_GROUP_ID}")
SETTINGS_FILE = "settings.json"

DB_FILE = "bot.db"
MAX_LESSONS_PER_PAGE = 7  # пагинация для view_completed_course
DEFAULT_COUNT_MESSAGES = 7  # макс количество сообщений при выводе курсов


# ---- НОВЫЕ ПЕРЕМЕННЫЕ ДЛЯ ВЕБХУКА (из .env или напрямую) ----
# Эти значения лучше брать из переменных окружения
WEB_SERVER_HOST = "0.0.0.0"  # Слушать на всех интерфейсах
WEB_SERVER_PORT = int(os.getenv("WEB_SERVER_PORT", 8080))  # Порт, на котором будет слушать ваше приложение
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"  # Секретный путь для вебхука
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")  # Например, "https://your.domain.com"

# Загрузка инструкции по оплате
PAYMENT_INSTRUCTIONS_TEMPLATE = os.getenv("PAYMENT_INSTRUCTIONS", "Инструкции по оплате у поддержки.")

# --- Constants ---
MAX_DB_RETRIES = 5
DB_RETRY_DELAY = 0.2  # seconds


# Initialize bot and dispatcher
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2)
)
dp = Dispatcher()

class ReplySupportCallback(CallbackData, prefix="reply_support"):
    user_id: int
    message_id: int


# Callback data classes
class CourseCallback(CallbackData, prefix="course"):
    action: str
    course_id: int
    lesson_num: int = 0


# Callback data classes
class AdminHomeworkCallback(CallbackData, prefix="admin_hw"):
    action: str  # approve_hw/reject_hw/approve_reason/reject_reason
    user_id: int
    course_id: int
    lesson_num: int
    message_id: int

class Form(StatesGroup):
    """Feedback Form"""
    feedback = State()

class SupportRequest(StatesGroup):
    waiting_for_response = State() #  New state for admin
    waiting_for_message = State() #  Original state

class CourseReviewForm(StatesGroup):
    waiting_for_review_text = State() # Новое состояние для отзыва о курсе


class BuyCourseCallback(CallbackData, prefix="buy_course"):
    course_id_str: str # Текстовый ID курса для покупки

class RestartCourseCallback(CallbackData, prefix="restart_course"):
    course_id_str: str
    action: str # "next_level" или "restart_current_level"

class AwaitingPaymentConfirmation(StatesGroup):
    waiting_for_activation_code_after_payment = State()


class MainMenuAction(CallbackData, prefix="main_menu"):
    action: str # "stop_course", "switch_course" (или "my_courses" как сейчас)
    course_id_numeric: int = 0 # Для действия stop_course, если нужно знать какой курс останавливаем



# декоратор для обработки ошибок в БД
def db_exception_handler(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except sqlite3.OperationalError as e:
            logger.error(f"Database is locked {func.__name__}: {e}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("База данных заблокирована. Попробуйте позже.")
                    break
            return None
        except aiosqlite.Error as e:
            logger.error(f"Database error in {func.__name__}: {e}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла ошибка при работе с базой данных.")
                    break
            return None
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла неизвестная ошибка.")
                    break
            return None
    return wrapper


### End filters... # 14-04
async def populate_course_versions(settings):
    """Заполняет таблицу course_versions данными из settings.json."""
    #logger.info("Заполнение таблицы course_versions...")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            for code, data in settings["activation_codes"].items():
                course_id = data["course"]
                version_id = data["version"]

                # Check if course_id and version_id already exist in course_versions
                cursor = await conn.execute("SELECT 1 FROM course_versions WHERE course_id = ? AND version_id = ?", (course_id, version_id))
                existing_record = await cursor.fetchone()

                if not existing_record:
                    # Get title and price from settings
                    version_title = settings["tariff_names"].get(version_id, "Базовый")
                    version_price = data["price"]

                    # Insert the record if it doesn't exist
                    await conn.execute("""
                        INSERT INTO course_versions (course_id, version_id, title, price)
                        VALUES (?, ?, ?, ?)
                    """, (course_id, version_id, version_title, version_price))
                    logger.debug(f"Добавлена запись в course_versions: {course_id=}, {version_id=}, {version_title=}, {version_price=}")
                else:
                     logger.debug(f"Запись уже существует в course_versions: {course_id=}, {version_id=}")
            await conn.commit()
        logger.info("Таблица course_versions успешно заполнена.")
    except Exception as e:
        logger.error(f"Ошибка при заполнении таблицы course_versions: {e}")


async def load_settings():
    """Загружает настройки из файла settings.json и заполняет таблицу course_versions."""
    logger.info(f"333444 load_settings ")
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                logger.info(f"Загрузка настроек из файла: {SETTINGS_FILE}")
                settings = json.load(f)
                logger.info(f"Настройки settings.json {len(settings)=} {settings.keys()=}")
                logger.info(f"Настройки успешно загружены. {settings['groups']=}")

                # Заполнение таблицы course_versions
                asyncio.create_task(populate_course_versions(settings))

                return settings
        except json.JSONDecodeError:
            logger.error("8889 Ошибка при декодировании JSON.")
            return {"groups": {}, "activation_codes": {}}
    else:
        logger.warning("Файл настроек не найден, используются настройки по умолчанию.")
        return {"groups": {}, "activation_codes": {}}

settings=dict() # делаем глобальный пустой словарь

COURSE_GROUPS = []

# Глобальная переменная для хранения стека уроков
lesson_stack = {}

# Глобальная переменная для хранения информации о последнем сообщении в канале
last_message_info = {}

user_support_state = {}

# Переменные для хранения задач и времени последней отправки статистики
lesson_check_tasks = {}
last_stats_sent = None # 14-04 todo нафига

# Создаем кэш для хранения информации о курсе и тарифе
course_info_cache = {}



# 14-04
async def is_course_active(user_id: int, course_id: str) -> bool:
    """Проверяет, активен ли курс у пользователя."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT 1 FROM user_courses WHERE user_id = ? AND course_id = ? AND status = 'active'", (user_id, course_id))
            result = await cursor.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Ошибка при проверке активности курса: {e}")
        return False

# 14-04 todo нафига. use get_user_active_courses. get_user_active_courses and is_course_active
async def get_user_courses(user_id: int) -> list:
    """Получает список всех курсов пользователя."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT course_id, status FROM user_courses WHERE user_id = ?", (user_id,))
            rows = await cursor.fetchall()
            return rows
    except Exception as e:
        logger.error(f"Ошибка при получении курсов пользователя: {e}")
        return []

# course_numeric_id = await get_course_id_int(course_id)
async def get_course_id_int(course_id: str) -> int:
    """Получает название курса по ID."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT id FROM courses WHERE course_id = ?", (course_id,))
            result = await cursor.fetchone()
            if result:
                logger.info(f"get_course_id_int {result=} берём return result[0]")
                return result[0]
            else:
                logger.error(f"Курс с ID {course_id=} не найден в базе данных.")
                return 0
    except Exception as e:
        logger.error(f"Ошибка при получении course_id курса: {e}")
        return 0

# course_id = get_course_id_str(course_numeric_id)
async def get_course_id_str(course_numeric_id: int) -> str:
    """Получает название курса по ID."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT course_id FROM courses WHERE id = ?", (course_numeric_id,))
            result = await cursor.fetchone()
            if result:
                logger.info(f"{result=} берём return result[0]")
                return result[0]
            else:
                logger.error(f"Курс с ID {course_numeric_id} не найден в базе данных.")
                return "Неизвестный курс"
    except Exception as e:
        logger.error(f"Ошибка при получении course_id курса: {e}")
        return "Неизвестный курс"

# 14-04
async def get_course_title(course_id: str) -> str:
    """Получает название курса по ID."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT title FROM courses WHERE course_id = ?", (course_id,))
            result = await cursor.fetchone()
            if result:
                return result[0]
            else:
                return "Неизвестный курс"
    except Exception as e:
        logger.error(f"Ошибка при получении названия курса: {e}")
        return "Неизвестный курс"

# 14-04
async def is_valid_activation_code(code: str) -> bool:
    """Проверяет, существует ли код активации в базе данных."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT 1 FROM course_activation_codes WHERE code_word = ?", (code,))
            result = await cursor.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Ошибка при проверке кода активации: {e}")
        return False


async def activate_course(user_id: int, activation_code: str, level:int = 1):
    """
    Активирует курс для пользователя. Если курс уже активен с другим тарифом,
    предлагает сменить тариф. Если курс был неактивен/завершен, активирует новый тариф.
    """
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # 1. Получаем данные по коду активации
            cursor_code = await conn.execute(
                "SELECT course_id, version_id FROM course_activation_codes WHERE code_word = ?", (activation_code,)
            )
            code_data = await cursor_code.fetchone()

            if not code_data:
                return False, "❌ Неверный код активации."

            new_course_id, new_version_id = code_data
            new_tariff_name = settings.get("tariff_names", {}).get(new_version_id, f"Тариф {new_version_id}")
            course_title = await get_course_title(new_course_id)  # Получаем название курса

            logger.info(
                f"Попытка активации: user_id={user_id}, code={activation_code} -> course_id='{new_course_id}', version_id='{new_version_id}' ({new_tariff_name})")

            # 2. Проверяем, есть ли у пользователя УЖЕ КАКАЯ-ЛИБО запись для этого курса (любой version_id, любой статус)
            cursor_existing = await conn.execute(
                "SELECT version_id, status, current_lesson FROM user_courses WHERE user_id = ? AND course_id = ?",
                (user_id, new_course_id)
            )
            existing_user_course_records = await cursor_existing.fetchall()

            now_utc = datetime.now(pytz.utc)
            now_utc_str = now_utc.strftime('%Y-%m-%d %H:%M:%S')

            activation_log_details = ""
            user_message = ""

            if existing_user_course_records:
                # У пользователя уже есть записи для этого курса.
                # Найдем активную запись, если она есть.
                active_record = next((r for r in existing_user_course_records if r[1] == 'active'), None)

                if active_record:
                    current_active_version_id, _, current_active_lesson = active_record
                    current_active_tariff_name = settings.get("tariff_names", {}).get(current_active_version_id,
                                                                                      f"Тариф {current_active_version_id}")

                    if current_active_version_id == new_version_id:
                        user_message = f"✅ Курс «{escape_md(course_title)}» с тарифом «{escape_md(new_tariff_name)}» у вас уже активен."
                        activation_log_details = f"Попытка повторной активации того же тарифа '{new_version_id}' для курса '{new_course_id}'. Курс уже активен."
                        logger.info(activation_log_details)
                        # Запускаем шедулер на всякий случай, если он был остановлен
                        await start_lesson_schedule_task(user_id)
                        return True, user_message  # Считаем успешной, т.к. курс уже активен

                    else:
                        # Активен другой тариф! Обновляем.
                        logger.info(
                            f"Смена тарифа для user_id={user_id}, course_id='{new_course_id}' с '{current_active_version_id}' на '{new_version_id}'.")
                        # Деактивируем все старые версии этого курса для этого пользователя
                        await conn.execute(
                            "UPDATE user_courses SET status = 'inactive' WHERE user_id = ? AND course_id = ?",
                            (user_id, new_course_id)
                        )
                        # Обновляем или вставляем новую запись с новым тарифом
                        # Сбрасываем прогресс при смене тарифа (current_lesson = 0)
                        await conn.execute("""
                            INSERT INTO user_courses (user_id, course_id, version_id, status, current_lesson, activation_date, first_lesson_sent_time, last_lesson_sent_time, level)
                            VALUES (?, ?, ?, 'active', 0, ?, ?, ?, ?)
                            ON CONFLICT(user_id, course_id, version_id) DO UPDATE SET
                                status = 'active',
                                current_lesson = 0,
                                activation_date = excluded.activation_date,
                                first_lesson_sent_time = excluded.first_lesson_sent_time,
                                last_lesson_sent_time = excluded.last_lesson_sent_time,
                                level = 1, 
                                hw_status = 'none', hw_type = NULL, is_completed = 0
                        """, (user_id, new_course_id, new_version_id, now_utc_str, now_utc_str, now_utc_str, level))

                        user_message = (f"✅ Тариф для курса «{escape_md(course_title)}» успешно изменен\\!\n"
                                        f"Раньше был: «{escape_md(current_active_tariff_name)}».\n"
                                        f"Теперь активен: «{escape_md(new_tariff_name)}».\n"
                                        "Прогресс по курсу начнется заново.")
                        activation_log_details = f"Смена тарифа для курса '{new_course_id}' с '{current_active_version_id}' на '{new_version_id}'. Прогресс сброшен."
                else:
                    # Есть записи, но ни одна не активна (все inactive или completed)
                    logger.info(
                        f"Повторная активация курса '{new_course_id}' с тарифом '{new_version_id}' для user_id={user_id}. Предыдущие статусы были неактивны.")
                    # Деактивируем все старые версии на всякий случай
                    await conn.execute(
                        "UPDATE user_courses SET status = 'inactive' WHERE user_id = ? AND course_id = ? AND version_id != ?",
                        (user_id, new_course_id, new_version_id)
                    )
                    # Вставляем или обновляем (если запись с таким version_id уже была, но inactive)
                    await conn.execute("""
                        INSERT INTO user_courses (user_id, course_id, version_id, status, current_lesson, activation_date, first_lesson_sent_time, last_lesson_sent_time, level)
                        VALUES (?, ?, ?, 'active', 0, ?, ?, ?, 1)
                        ON CONFLICT(user_id, course_id, version_id) DO UPDATE SET
                            status = 'active',
                            current_lesson = 0,
                            activation_date = excluded.activation_date,
                            first_lesson_sent_time = excluded.first_lesson_sent_time,
                            last_lesson_sent_time = excluded.last_lesson_sent_time,
                            level = 1,
                            hw_status = 'none', hw_type = NULL, is_completed = 0
                    """, (user_id, new_course_id, new_version_id, now_utc_str, now_utc_str, now_utc_str))
                    user_message = f"✅ Курс «{escape_md(course_title)}» с тарифом «{escape_md(new_tariff_name)}» успешно активирован (или возобновлен)\\! Прогресс начнется заново."
                    activation_log_details = f"Активирован/возобновлен курс '{new_course_id}' с тарифом '{new_version_id}'. Прогресс сброшен."
            else:
                # Это первая активация этого курса для пользователя
                logger.info(
                    f"Первая активация курса '{new_course_id}' с тарифом '{new_version_id}' для user_id={user_id}.")
                await conn.execute("""
                    INSERT INTO user_courses (user_id, course_id, version_id, status, current_lesson, activation_date, first_lesson_sent_time, last_lesson_sent_time, level)
                    VALUES (?, ?, ?, 'active', 0, ?, ?, ?, 1)
                """, (user_id, new_course_id, new_version_id, now_utc_str, now_utc_str, now_utc_str))
                user_message = f"✅ Курс «{escape_md(course_title)}» с тарифом «{escape_md(new_tariff_name)}» успешно активирован\\!"
                activation_log_details = f"Курс '{new_course_id}' (тариф '{new_version_id}') успешно активирован."

            await conn.commit()

            # Логирование действия в БД
            if "Смена тарифа" in activation_log_details:
                # При смене тарифа мы знаем old_value (старый тариф)
                # current_active_version_id должен быть доступен в этом блоке кода
                await log_action(
                    user_id=user_id,
                    action_type="TARIFF_CHANGE",
                    course_id=new_course_id,
                    old_value=current_active_version_id, # <--- Убедитесь, что эта переменная здесь доступна
                    new_value=new_version_id,
                    details=activation_log_details
                )
            elif "Попытка повторной активации того же тарифа" in activation_log_details:
                 await log_action(
                    user_id=user_id,
                    action_type="COURSE_REACTIVATION_ATTEMPT",
                    course_id=new_course_id,
                    new_value=new_version_id, # Это тот же тариф, что и был
                    details=activation_log_details
                )
            else: # Обычная активация или возобновление
                await log_action(
                    user_id=user_id,
                    action_type="COURSE_ACTIVATION",
                    course_id=new_course_id,
                    new_value=new_version_id,
                    details=activation_log_details
                )

            # Отправка уведомления админам
            if ADMIN_GROUP_ID:
                user_info = await bot.get_chat(user_id)
                user_display_name = user_info.full_name or f"ID:{user_id}"
                if user_info.username: user_display_name += f" @{user_info.username}"
                admin_notification = (
                    f"🔔 Активация курса для пользователя {escape_md(user_display_name)}\n"
                    f"Курс: {escape_md(course_title)} {escape_md(new_course_id)}\n"
                    f"Тариф: {escape_md(new_tariff_name)} {escape_md(new_version_id)}\n"
                    f"Детали: {escape_md(activation_log_details)}"
                )
                try:
                    await bot.send_message(ADMIN_GROUP_ID, admin_notification, parse_mode=ParseMode.MARKDOWN_V2)
                except Exception as e_admin_notify:
                    logger.error(f"Не удалось отправить уведомление админам об активации: {e_admin_notify}")

            await start_lesson_schedule_task(user_id)
            return True, user_message

    except Exception as e:
        logger.error(f"Ошибка при активации курса (код {activation_code}) для user_id={user_id}: {e}", exc_info=True)
        return False, "⚠️ Произошла серьезная ошибка при активации курса. Пожалуйста, свяжитесь с поддержкой."


# 14-04
async def deactivate_course(user_id: int, course_id: str):
    """Деактивирует курс для пользователя."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Шаг 1: Деактивируем курс
            await conn.execute("""
                UPDATE user_courses SET status = 'inactive' WHERE user_id = ? AND course_id = ?
            """, (user_id, course_id))
            await conn.commit()

            # Шаг 2: Останавливаем проверку расписания для пользователя
            await stop_lesson_schedule_task(user_id)

            return True, "✅ Курс успешно деактивирован."
    except Exception as e:
        logger.error(f"Ошибка при деактивации курса: {e}")
        return False, "⚠️ Произошла ошибка при деактивации курса. Попробуйте позже."


@db_exception_handler
async def check_lesson_schedule(user_id: int, hours=24, minutes=0):
    """Проверяет расписание уроков и отправляет урок, если пришло время."""
    logger.info(f"🔄 Проверка расписания для user_id={user_id}, принудительно (h/m): {hours}/{minutes}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:  # Единое соединение для всех операций
            logger.info(f"Подключились к БД для проверки расписания user_id={user_id}")

            # 1. Получаем данные пользователя и его активного курса
            cursor_user_data = await conn.execute("""
                SELECT course_id, current_lesson, version_id, 
                       first_lesson_sent_time, last_lesson_sent_time, 
                       hw_status, last_menu_message_id, status, level
                FROM user_courses 
                WHERE user_id = ? AND status = 'active' 
            """, (user_id,))
            user_data = await cursor_user_data.fetchone()

            if not user_data:
                logger.warning(f"У пользователя {user_id} нет активных курсов. Проверка расписания завершена.")
                # Попытка остановить задачу, если больше нет активных курсов
                cursor_active_count = await conn.execute(
                    "SELECT COUNT(*) FROM user_courses WHERE user_id = ? AND status = 'active'", (user_id,))
                active_count_data = await cursor_active_count.fetchone()
                if active_count_data and active_count_data[0] == 0:
                    logger.info(f"У пользователя {user_id} больше нет активных курсов, остановка задачи шедулера.")
                    await stop_lesson_schedule_task(user_id)
                return

            (course_id, current_lesson_db, version_id,
             first_sent_time_str, last_sent_time_str,
             hw_status, menu_message_id, course_status_db, user_course_level) = user_data

            logger.info(
                f"Данные для {user_id}: {course_id=}, current_lesson_db={current_lesson_db}, {version_id=}, "
                f"first_sent_time_str='{first_sent_time_str}', last_sent_time_str='{last_sent_time_str}', "
                f"{hw_status=}, {menu_message_id=}, course_status_db='{course_status_db}', {user_course_level=}"
            )

            # 2. Проверка статуса ДЗ
            if hw_status not in ('approved', 'not_required', 'none'):
                logger.info(
                    f"Для {user_id} (курс {course_id}) ожидаем ДЗ ({hw_status=}). Следующий урок не отправляем.")
                return

            # 3. Получение интервала отправки сообщений
            message_interval_hours = float(settings.get("message_interval", 24.0))
            logger.info(f"Для {user_id} (курс {course_id}): message_interval_hours={message_interval_hours}")

            # 4. Логика отправки урока
            if last_sent_time_str:  # Если уроки уже отправлялись
                logger.info(f"Для {user_id} (курс {course_id}): last_sent_time_str='{last_sent_time_str}'")

                if not first_sent_time_str:
                    logger.error(
                        f"Критическая ошибка: отсутствует first_lesson_sent_time для user_id={user_id}, "
                        f"course_id={course_id}, хотя last_sent_time есть. Невозможно рассчитать время."
                    )
                    return

                try:
                    first_sent_naive_utc = datetime.strptime(first_sent_time_str, '%Y-%m-%d %H:%M:%S')
                    first_sent_aware_utc = pytz.utc.localize(first_sent_naive_utc)

                    next_lesson_event_time_utc = first_sent_aware_utc + timedelta(
                        hours=message_interval_hours) * current_lesson_db
                    current_time_aware_utc = datetime.now(pytz.utc)
                    time_left = next_lesson_event_time_utc - current_time_aware_utc

                    logger.info(
                        f"Для {user_id} (курс {course_id}): first_sent_aware_utc={first_sent_aware_utc}, "
                        f"next_lesson_event_time_utc={next_lesson_event_time_utc}, "
                        f"current_time_aware_utc={current_time_aware_utc}, time_left_seconds={time_left.total_seconds()}"
                    )

                    if time_left.total_seconds() > 10 and not (hours == 0 and minutes == 0):
                        display_next_lesson_time = await get_next_lesson_time(user_id, course_id, current_lesson_db)
                        status_time_message = f"⏳ Следующий урок: {display_next_lesson_time}\n"
                        logger.info(f"Для {user_id} (курс {course_id}): {status_time_message.strip()}")

                        if menu_message_id:
                            try:
                                course_numeric_id = await get_course_id_int(course_id)
                                keyboard = get_main_menu_inline_keyboard(
                                    course_numeric_id=course_numeric_id,
                                    lesson_num=current_lesson_db,
                                    user_tariff=version_id,
                                    homework_pending=(hw_status == 'pending')
                                )
                                logger.info(f"Попытка обновить menu_message_id={menu_message_id} для user_id={user_id}")
                                await asyncio.sleep(0.1)
                                await bot.edit_message_text(
                                    chat_id=user_id,
                                    message_id=menu_message_id,
                                    text=escape_md(status_time_message),
                                    reply_markup=keyboard,
                                    parse_mode=ParseMode.MARKDOWN_V2
                                )
                                logger.info(f"Сообщение меню {menu_message_id} обновлено для user_id={user_id}")
                            except TelegramBadRequest as e_edit:
                                logger.warning(
                                    f"Не удалось обновить сообщение меню {menu_message_id} для user_id={user_id}: {e_edit}")
                                if "message to edit not found" in str(e_edit).lower() or \
                                        "message is not modified" in str(e_edit).lower():
                                    logger.info(
                                        f"Сбрасываем last_menu_message_id для user_id={user_id} из-за ошибки редактирования.")
                                    await conn.execute(
                                        "UPDATE user_courses SET last_menu_message_id = NULL WHERE user_id = ? AND course_id = ?",
                                        (user_id, course_id)
                                    )
                                    await conn.commit()
                            except Exception as e_update_menu:
                                logger.error(
                                    f"Неожиданная ошибка при обновлении меню для user {user_id}: {e_update_menu}",
                                    exc_info=True)
                        else:
                            logger.info(
                                f"Для {user_id} (курс {course_id}) нет menu_message_id для обновления, время до урока еще не вышло.")

                    else:  # Время пришло отправлять следующий урок
                        next_lesson_to_send = current_lesson_db + 1
                        logger.info(
                            f"Время пришло! Отправляем урок {next_lesson_to_send} курса {course_id} для user_id={user_id}")
                        await send_lesson_to_user(user_id, course_id, next_lesson_to_send)
                        logger.info(
                            f"✅ Урок {next_lesson_to_send} (попытка отправки) для {user_id} завершена из check_lesson_schedule.")

                except ValueError as e_parse:
                    logger.error(
                        f"⚠️ Ошибка преобразования времени в check_lesson_schedule: {e_parse} для "
                        f"first_sent_time_str='{first_sent_time_str}' или last_sent_time_str='{last_sent_time_str}'",
                        exc_info=True)
                    await bot.send_message(user_id, escape_md(
                        "📛 Ошибка времени урока (неверный формат в базе)! Свяжитесь с поддержкой."),
                                           parse_mode=ParseMode.MARKDOWN_V2)
                    return
                except Exception as e_time_calc:
                    logger.error(
                        f"💥 Неожиданная ошибка в расчете времени урока в check_lesson_schedule для user_id={user_id}: {e_time_calc}",
                        exc_info=True)
                    await bot.send_message(user_id, escape_md("📛 Ошибка при расчете времени урока! Мы уже чиним."),
                                           parse_mode=ParseMode.MARKDOWN_V2)
                    return

            else:  # last_sent_time_str отсутствует
                if current_lesson_db == 0 and first_sent_time_str:
                    logger.info(
                        f"Отправка первого урока (урок 1), так как current_lesson_db=0 и last_sent_time_str отсутствует. user_id={user_id}, course_id={course_id}")
                    await send_lesson_to_user(user_id, course_id, 1)
                elif current_lesson_db == 0 and not first_sent_time_str:  # Этого не должно быть, если активация прошла корректно
                    logger.error(
                        f"Критично: current_lesson_db=0, и отсутствует first_sent_time_str для user_id={user_id}, course_id={course_id}. Невозможно начать курс.")
                else:  # current_lesson_db > 0, но last_sent_time_str почему-то пуст
                    logger.warning(
                        f"Нелогичное состояние: last_sent_time_str отсутствует, но current_lesson_db={current_lesson_db} для user_id={user_id}, course_id={course_id}. "
                        "Возможно, это первый урок после миграции данных или сбоя. Попытка отправить урок current_lesson_db + 1."
                    )
                    # Можно попробовать отправить следующий урок, но это рискованно без last_sent_time.
                    # Или просто ничего не делать и ждать, пока данные исправятся или ситуация прояснится.
                    # Для безопасности, пока просто логируем.
                    # await send_lesson_to_user(user_id, course_id, current_lesson_db + 1)

    # Блоки except для ошибок БД и глобальных ошибок остаются на этом уровне
    except sqlite3.OperationalError as e_sqlite_op:
        logger.error(
            f"Database is locked (OperationalError) в check_lesson_schedule для user_id={user_id}: {e_sqlite_op}")
    except aiosqlite.Error as e_aiosqlite:
        logger.error(f"Database error (aiosqlite) в check_lesson_schedule для user_id={user_id}: {e_aiosqlite}")
    except Exception as e_global:
        logger.error(f"💥 Общая неизвестная ошибка в check_lesson_schedule для user_id={user_id}: {e_global}",
                     exc_info=True)
        # Consider not spamming user for generic background errors unless critical for them
        # await bot.send_message(user_id, "📛 Общая ошибка расписания. Мы уже чиним робота!", parse_mode=None)
    finally:
        logger.info(f"🏁🏁 Функция check_lesson_schedule для user_id={user_id} полностью завершена.")




async def send_admin_stats():
    """Отправляет статистику администраторам каждые 5 часов."""
    global last_stats_sent
    while True:
        now = datetime.now(pytz.utc)
        # Если статистику еще не отправляли или прошло 5 часов
        if last_stats_sent is None or now - last_stats_sent >= timedelta(hours=5):
            # Собираем статистику (магия данных!)
            stats = await gather_course_statistics()

            # Формируем сообщение с эмодзи для красоты
            stat_message = f"📊 Статистика бота:\n\n{stats}"

            # Отправляем в группу админов (теперь не спам, а групповой чат)
            try:
                await bot.send_message(ADMIN_GROUP_ID, stat_message, parse_mode=None)
            except Exception as e:
                logger.error(f"❌ Не удалось отправить статистику админам: {e}")

            # Запоминаем время последней отправки (чтобы не доставать админов чаще)
            last_stats_sent = now
        await asyncio.sleep(5 * 3600)  # Каждые 5 часов

async def gather_course_statistics():
    """Собирает статистику по курсам."""
    total_users = 0
    active_courses = 0
    solo_tariff_count = 0
    group_tariff_count = 0

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Общее количество пользователей
            cursor = await conn.execute("SELECT COUNT(DISTINCT user_id) FROM user_courses")
            total_users = (await cursor.fetchone())[0]

            # Количество активных курсов
            cursor = await conn.execute("SELECT COUNT(*) FROM user_courses WHERE status = 'active'")
            active_courses = (await cursor.fetchone())[0]

            # Распределение пользователей по тарифам (пример для тарифов "Соло" и "Группа")
            cursor = await conn.execute("SELECT COUNT(*) FROM user_courses WHERE version_id = 'v1' AND status = 'active'")
            solo_tariff_count = (await cursor.fetchone())[0]

            cursor = await conn.execute("SELECT COUNT(*) FROM user_courses WHERE version_id = 'v2' AND status = 'active'")
            group_tariff_count = (await cursor.fetchone())[0]
    except Exception as e:
        logger.error(f"Ошибка при сборе статистики: {e}")

    return (
        f"Всего пользователей: {total_users}\n"
        f"Активных курсов: {active_courses}\n"
        f"Тариф \"Соло\": {solo_tariff_count}\n"
        f"Тариф \"Группа\": {group_tariff_count}\n"
    )

async def start_lesson_schedule_task(user_id: int):
    """Запускает периодическую проверку расписания уроков для пользователя."""
    if user_id not in lesson_check_tasks:
        task = asyncio.create_task(scheduled_lesson_check(user_id))
        lesson_check_tasks[user_id] = task
        logger.info(f" 500 start_lesson_schedule_task Запущена задача проверки расписания уроков для пользователя {user_id}.")

async def stop_lesson_schedule_task(user_id: int):
    """Останавливает периодическую проверку расписания уроков для пользователя."""
    if user_id in lesson_check_tasks:
        task = lesson_check_tasks[user_id]
        task.cancel()
        del lesson_check_tasks[user_id]
        logger.info(f"Остановлена задача проверки расписания уроков для пользователя {user_id}.")


def save_settings(settings):
    """Сохраняет настройки в файл settings.json."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
        logger.info("Настройки успешно сохранены.")
    except Exception as e:
        logger.error(f"Ошибка при сохранении настроек: {e}")

@db_exception_handler
async def process_add_course_to_db(course_id, group_id, code1, code2, code3):
    """Добавляет информацию о курсе и кодах активации в базу данных."""
    logger.info(f"3338883333 process_add_course_to_db ")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем максимальный id из таблицы, если таблица пуста — ставим 1000
            cursor = await conn.execute("SELECT MAX(id) FROM courses")
            row = await cursor.fetchone()
            max_id = row[0] if row[0] is not None else 999  # если таблица пуста, начнем с 1000
            new_id = max_id + 1

            await conn.execute("""
                INSERT OR REPLACE INTO courses (id, course_id, group_id, title, description)
                VALUES (?, ?, ?, ?, ?)
            """, (new_id, course_id, group_id, f"{course_id} basic", f"Описание для {course_id}"))
            logger.info(
                f"Добавлена запись в process_add_course_to_db: {new_id=}, {course_id=}, {group_id=}")

            # Обработка кодов активации
            for code in [code1, code2, code3]:
                code_info = settings["activation_codes"].get(code)
                if code_info:
                    await conn.execute("""
                        INSERT OR IGNORE INTO course_activation_codes 
                        (code_word, course_id, version_id, price_rub)
                        VALUES (?, ?, ?, ?)
                    """, (
                        code,
                        code_info["course"],
                        code_info["version"],
                        code_info["price"]
                    ))

            await conn.commit()
            logger.info(f"Курс {course_id} успешно добавлен в базу данных.")

            await update_settings_file()  # Обновляем файл settings.json
            await backup_settings_file()  # Создаем бэкап файла settings.json

    except Exception as e:
        logger.error(f"Ошибка при добавлении курса {course_id} в базу данных: {e}")


async def backup_settings_file():
    """Создает бэкап файла settings.json."""
    try:
        timestamp = datetime.now(pytz.utc).strftime("%Y-%m-%d_%H-%M-%S")
        backup_file = f"settings_{timestamp}.json"
        shutil.copy("settings.json", backup_file)
        logger.info(f"Создан бэкап файла settings.json: {backup_file}")

    except Exception as e:
        logger.error(f"Ошибка при создании бэкапа файла settings.json: {e}")


@db_exception_handler
async def init_db():
    """Инициализирует базу данных, создавая необходимые таблицы, если они еще не существуют.

        Функция предполагает следующую структуру данных:
        - users: Содержит информацию о пользователях бота (user_id, username, first_name, last_name).
        - courses: Хранит данные о курсах (course_id, group_id, title, description).
        - homework_gallery: Содержит информацию о домашних заданиях, отправленных пользователями (user_id, course_id, lesson_num, message_id, approved_by).
        - admin_context: Используется для хранения контекстных данных администраторов (admin_id, context_data).
        - user_states: Хранит состояние пользователя, включая ID текущего курса (user_id, current_course_id).
        - course_versions: Содержит информацию о версиях курсов (тарифы) (course_id, version_id, title, price, activation_code, description).
        - user_courses: Связывает пользователей с курсами и хранит их прогресс (user_id, course_id, version_id, status, current_lesson, last_lesson_sent_time, is_completed, activation_date).
        - group_messages: Хранит сообщения из групп, используемые в уроках (group_id, lesson_num, course_id, content_type, is_homework, text, file_id).
        - course_activation_codes: Содержит коды активации для курсов (code_word, course_id, version_id, price_rub).
        """
    logger.info(f"Initializing database...")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Создаем таблицу users
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA busy_timeout = 310")  #
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT COLLATE NOCASE,
                    first_name TEXT COLLATE NOCASE,
                    last_name TEXT COLLATE NOCASE,
                    timezone TEXT DEFAULT 'Europe/Moscow',
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await conn.commit()

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS courses (
                    course_id TEXT PRIMARY KEY,
                    id INTEGER,
                    group_id TEXT,
                    title TEXT NOT NULL COLLATE NOCASE,
                    message_interval REAL NOT NULL DEFAULT 24,
                    description TEXT COLLATE NOCASE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await conn.commit()

            # # 09-04 perplexity - галерея домашек
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS homework_gallery (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                course_id TEXT NOT NULL,
                lesson_num INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                approved_by INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (course_id) REFERENCES courses(course_id)
            )
             ''')

            # 09-04 perplexity - для домашек
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS admin_context(
                    user_id INTEGER PRIMARY KEY,
                    course_id TEXT NOT NULL,    
                    lesson_num INTEGER NOT NULL, 
                    text TEXT
                )
            ''')
            await conn.commit()

            # 08-04 perplexity
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_states(
                user_id INTEGER PRIMARY KEY,
                current_course_id TEXT, -- ID текущего курса
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id),
                FOREIGN KEY(current_course_id) REFERENCES courses(course_id)
                )
            ''')
            # для хранения информации о версиях курсов (тарифы).
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS course_versions (
                    course_id TEXT,
                    version_id TEXT,
                    title TEXT NOT NULL COLLATE NOCASE,
                    price REAL DEFAULT 0,
                    activation_code TEXT, 
                    description TEXT COLLATE NOCASE,
                    PRIMARY KEY (course_id, version_id),
                    FOREIGN KEY (course_id) REFERENCES courses(course_id)
                )
            ''')
            await conn.commit()

            # для связывания пользователей с курсами и хранения их прогресса.
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_courses (
                    user_id INTEGER,
                    course_id TEXT,
                    version_id TEXT,
                    status TEXT DEFAULT 'active',
                    hw_status TEXT DEFAULT 'none',
                    hw_type TEXT DEFAULT 'none',
                    current_lesson INTEGER DEFAULT 0,
                    level integer DEFAULT 1,
                    first_lesson_sent_time DATETIME,
                    last_lesson_sent_time DATETIME,
                    is_completed INTEGER DEFAULT 0,
                    last_menu_message_id INTEGER,
                    activation_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, course_id, version_id),
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (course_id, version_id) REFERENCES course_versions(course_id, version_id)
                )
            ''')
            await conn.commit()

            # Создаем таблицу group_messages
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS group_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    group_id TEXT NOT NULL,
                    lesson_num integer,
                    course_id TEXT,      
                    content_type TEXT NOT NULL,
                    is_homework BOOLEAN DEFAULT FALSE,
                    hw_type TEXT,
                    text TEXT,
                    file_id TEXT,
                    level integer DEFAULT 1,
                    message_id INTEGER NOT NULL,
                    is_forwarded BOOLEAN DEFAULT FALSE,
                    forwarded_from_chat_id INTEGER,
                    forwarded_message_id INTEGER,
                    snippet TEXT COLLATE NOCASE, -- Сниппет урока todo: 
                    is_bouns BOOLEAN DEFAULT FALSE,
                    open_time DATETIME,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (course_id) REFERENCES courses(course_id)
                )
            ''')

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS course_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    course_id TEXT,
                    review_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_actions_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    action_type TEXT NOT NULL, -- e.g., 'COURSE_ACTIVATION', 'TARIFF_CHANGE', 'LESSON_SENT', 'HOMEWORK_SUBMITTED', 'HOMEWORK_APPROVED'
                    course_id TEXT,
                    lesson_num INTEGER,
                    old_value TEXT, -- Например, старый тариф
                    new_value TEXT, -- Например, новый тариф
                    details TEXT,   -- Дополнительные детали в JSON или текстом
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            await conn.commit()

            # Создаем таблицу activation_codes
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS course_activation_codes (
                    code_word TEXT PRIMARY KEY,
                    course_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    price_rub INTEGER NOT NULL,
                    FOREIGN KEY (course_id) REFERENCES courses(course_id),
                    FOREIGN KEY (course_id, version_id) REFERENCES course_versions(course_id, version_id)
                      
                )
            ''')
            await conn.commit()

            logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise  # Allows bot to exit on startup if database cannot be initialized


# Функция для экранирования спецсимволов в тексте для использования в MarkdownV2
def escape_md(text):
    """Экранирует специальные символы для MarkdownV2."""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([{}])'.format(re.escape(escape_chars)), r'\\\1', text)


# логирование действий пользователя
async def log_action(user_id: int, action_type: str, course_id: str = None, lesson_num: int = None,
                     old_value: str = None, new_value: str = None, details: str = None):
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute(
                """INSERT INTO user_actions_log 
                   (user_id, action_type, course_id, lesson_num, old_value, new_value, details, timestamp) 
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (user_id, action_type, course_id, lesson_num, old_value, new_value, details, datetime.now(pytz.utc))
            )
            await conn.commit()
        logger.info(f"Лог действия: user_id={user_id}, action={action_type}, course={course_id}, lesson={lesson_num}, old={old_value}, new={new_value}, details={details}")
    except Exception as e:
        logger.error(f"Ошибка логирования действия {action_type} для user_id={user_id}: {e}")

# Пример использования в новой activate_course:
# await log_action(user_id, "TARIFF_CHANGE", new_course_id,
#                  old_value=current_active_version_id, new_value=new_version_id,
#                  details="Прогресс сброшен")
# await log_action(user_id, "COURSE_ACTIVATION", new_course_id, new_value=new_version_id)

# функция для разрешения ID пользователя по алиасу или ID
@db_exception_handler
async def resolve_user_id(user_identifier):
    """Resolve user_id from alias or numeric ID"""
    try:
        if user_identifier.isdigit():
            return int(user_identifier)
        else:
            # Try to find by alias
            async with aiosqlite.connect(DB_FILE) as conn:
                cursor = await conn.execute(
                    "SELECT user_id FROM user_profiles WHERE alias = ?",
                    (user_identifier,)
                )
                result = await cursor.fetchone()
                if result:
                    return result[0]
        return None
    except Exception as e:
        logger.error(f"Error resolving user ID: {e}")
        return None


@db_exception_handler
async def old_send_lesson_to_user(user_id: int, course_id: str, lesson_num: int, repeat: bool = False, level: int = 1):
    """Отправляет урок, обновляет время отправки и обрабатывает ДЗ."""
    logger.info(
        f"🚀 send_lesson_to_user: user_id={user_id}, course_id={course_id}, lesson_num={lesson_num}, repeat={repeat}, level={level}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем общее количество уроков в курсе (с lesson_num > 0)
            cursor_total = await conn.execute("""
                SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ? AND lesson_num > 0
            """, (course_id,))
            total_lessons_data = await cursor_total.fetchone()
            total_lessons = total_lessons_data[0] if total_lessons_data and total_lessons_data[0] is not None else 0
            logger.info(
                f"Для курса {course_id} найдено {total_lessons} уроков (с lesson_num > 0). Запрошен урок {lesson_num}.")

            # --- Логика обработки завершения курса или отсутствия урока ---
            if lesson_num > total_lessons and total_lessons > 0:  # Запрошен урок, который больше максимального существующего
                logger.info(
                    f"Курс {course_id} завершен для пользователя {user_id}. Последний урок был {total_lessons}, запрошен {lesson_num}.")
                course_title_safe = escape_md(await get_course_title(course_id))
                message_text = (
                    f"🎉 Поздравляем с успешным завершением курса «{course_title_safe}»\\! 🎉\n\n"
                    "Вы прошли все уроки и Что вы хотите сделать дальше?"
                )
                builder = InlineKeyboardBuilder()
                # Кнопка для продвинутого курса (если есть логика и такой курс)
                # Убедитесь, что callback_data f"activate_advanced_{course_id}" обрабатывается
                # if level == 1: # или другая ваша логика для отображения этой кнопки
                #     builder.button(
                #         text=f"Продвинутый курс {escape_md(await get_course_title(course_id))}", # Экранируем и здесь
                #         callback_data=f"activate_advanced_{course_id}"
                #     )
                builder.button(text="Выбрать другой курс", callback_data="select_other_course")
                builder.button(text="Оставить отзыв", callback_data="leave_feedback")

                await bot.send_message(
                    chat_id=user_id,
                    text=message_text,  # Текст уже частично экранирован
                    reply_markup=builder.as_markup(),
                    parse_mode=ParseMode.MARKDOWN_V2  # Явно указываем, если текст содержит Markdown
                )
                # Обновляем статус курса на 'completed'
                await conn.execute("""
                    UPDATE user_courses 
                    SET status = 'completed', is_completed = 1
                    WHERE user_id = ? AND course_id = ?
                """, (user_id, course_id))
                await conn.commit()

                # Логируем завершение курса в отдельную таблицу БД
                await log_action(user_id, "COURSE_COMPLETED", course_id, details=f"Последний урок: {lesson_num}")

                return  # Важно завершить выполнение функции

            # --- Ищем контент запрошенного урока ---
            cursor_lesson = await conn.execute("""
                SELECT text, content_type, file_id, is_homework, hw_type, level
                FROM group_messages
                WHERE course_id = ? AND lesson_num = ?
                ORDER BY id
            """, (course_id, lesson_num))
            lesson_content = await cursor_lesson.fetchall()

            if not lesson_content:  # Урок существует по номеру (lesson_num <= total_lessons), но контента для него нет
                logger.warning(
                    f"⚠️ Контент для урока {lesson_num} не найден в курсе {course_id}, хотя такой номер урока допустим (всего {total_lessons} уроков).")
                course_title_safe = escape_md(await get_course_title(course_id))
                await bot.send_message(
                    user_id,
                    f"Извините, урок №{lesson_num} для курса «{course_title_safe}» временно недоступен или еще не был добавлен. Пожалуйста, попробуйте позже или свяжитесь с поддержкой.",
                    parse_mode=ParseMode.MARKDOWN_V2  # Текст здесь безопасен, т.к. мы его формируем
                )
                # НЕ обновляем статус курса, НЕ отправляем меню завершения.
                # Можно вернуть пользователя в главное меню предыдущего урока или текущего, если это повтор
                # Например, если это не повтор, то current_lesson еще не обновился до lesson_num
                # user_course_info = await conn.execute("SELECT version_id, current_lesson FROM user_courses WHERE user_id = ? AND course_id = ?", (user_id, course_id)).fetchone()
                # if user_course_info:
                #    await send_main_menu(user_id, course_id, user_course_info[1], user_course_info[0], ...)
                return  # Важно завершить выполнение функции

            # --- Если контент урока найден, продолжаем отправку ---
            logger.info(f"Найден контент для урока {lesson_num} курса {course_id}: {len(lesson_content)} частей.")
            is_homework_local = False
            hw_type_local = None
            k = 0

            for piece_text, content_type, file_id, is_homework, hw_type, piece_level in lesson_content:
                k += 1
                current_piece_text = piece_text if piece_text else ""  # Защита от None

                if piece_level > level:
                    logger.info(f"Пропускаем часть {k} урока {lesson_num} (уровень {piece_level} > {level})")
                    continue

                # Экранируем текст/подпись только если parse_mode будет MarkdownV2
                # Для parse_mode=None или parse_mode="" экранирование не нужно, но и разметка не сработает.
                # Так как у бота дефолт MarkdownV2, лучше всегда экранировать динамический текст.
                safe_caption = escape_md(current_piece_text)

                if content_type == "text":
                    if not current_piece_text.strip():  # Проверяем на пустой текст после strip
                        logger.error(f"Пустой текст в части {k} урока {lesson_num} курса {course_id}. Пропуск.")
                        continue
                    await bot.send_message(user_id, safe_caption, parse_mode=ParseMode.MARKDOWN_V2)
                elif file_id:  # Общая проверка для всех медиатипов, что file_id есть
                    if content_type == "photo":
                        await bot.send_photo(user_id, file_id, caption=safe_caption, parse_mode=ParseMode.MARKDOWN_V2)
                    elif content_type == "audio":
                        await bot.send_audio(user_id, file_id, caption=safe_caption, parse_mode=ParseMode.MARKDOWN_V2)
                    elif content_type == "video":
                        await bot.send_video(user_id, file_id, caption=safe_caption, parse_mode=ParseMode.MARKDOWN_V2)
                    elif content_type == "document":
                        await bot.send_document(user_id, file_id, caption=safe_caption,
                                                parse_mode=ParseMode.MARKDOWN_V2)
                    elif content_type == "voice":
                        await bot.send_voice(user_id, file_id, caption=safe_caption, parse_mode=ParseMode.MARKDOWN_V2)
                    elif content_type == "animation":
                        await bot.send_animation(user_id, file_id, caption=safe_caption,
                                                 parse_mode=ParseMode.MARKDOWN_V2)
                    else:
                        logger.warning(
                            f"Неизвестный content_type '{content_type}' с file_id для части {k} урока {lesson_num}.")
                else:  # file_id отсутствует для медиатипа
                    logger.error(
                        f"Отсутствует file_id для медиа ({content_type}) части {k} урока {lesson_num}, курс {course_id}. Подпись была: '{current_piece_text}'")

                if is_homework:
                    logger.info(f"Часть {k} урока {lesson_num} является ДЗ типа: {hw_type}")
                    is_homework_local = True
                    hw_type_local = hw_type

            logger.info(f"Отправлено {k} (обработано) частей урока {lesson_num}.")

            # --- Обновление информации о пользователе и курсе ---
            # (Этот блок был вложен, вынес его на один уровень с циклом отправки)
            # async with aiosqlite.connect(DB_FILE) as conn_user_update: # Можно использовать существующее conn
            cursor_user_course = await conn.execute("""
                        SELECT version_id, hw_status
                        FROM user_courses
                        WHERE user_id = ? AND course_id = ? AND status = 'active'
                    """, (user_id, course_id))
            row_user_course = await cursor_user_course.fetchone()

            if row_user_course is None:
                logger.error(
                    f"❌ User {user_id} не найден в user_courses для курса {course_id} при обновлении статуса урока.")
                # В этом случае неясно, какой version_id использовать для send_main_menu.
                # Можно либо не отправлять меню, либо использовать дефолтный.
                return

            version_id, hw_status_db = row_user_course  # hw_status_db - текущий статус ДЗ из БД

            now_utc = datetime.now(pytz.utc)
            now_utc_str = now_utc.strftime('%Y-%m-%d %H:%M:%S')

            new_hw_status_for_db = hw_status_db  # По умолчанию не меняем
            new_hw_type_for_db = None  # Будет установлено, если это новый урок с ДЗ

            if not repeat:  # Только если это не повторная отправка урока
                logger.info(f"✅ Новый урок {lesson_num} отправлен. Время: {now_utc_str}. Это ДЗ: {is_homework_local}")
                new_hw_status_for_db = 'pending' if is_homework_local else 'none'
                if is_homework_local:
                    new_hw_type_for_db = hw_type_local

                await conn.execute("""
                        UPDATE user_courses 
                        SET hw_status = ?, hw_type = ?, current_lesson = ?, last_lesson_sent_time = ?
                        WHERE user_id = ? AND course_id = ? AND status = 'active'
                    """, (new_hw_status_for_db, new_hw_type_for_db, lesson_num, now_utc_str, user_id, course_id))

                # В конце send_lesson_to_user, после обновления user_courses и перед send_main_menu
                if not repeat:
                    await log_action(user_id, "LESSON_SENT", course_id, lesson_num,
                                     new_value=str(level))  # level - текущий уровень урока

            else:  # Если это повторная отправка
                logger.info(f"🔁 Урок {lesson_num} отправлен повторно. Время: {now_utc_str}")
                # При повторе не меняем current_lesson, hw_status, hw_type, только last_lesson_sent_time
                await conn.execute("""
                        UPDATE user_courses 
                        SET last_lesson_sent_time = ? 
                        WHERE user_id = ? AND course_id = ? AND status = 'active'
                    """, (now_utc_str, user_id, course_id))

            await conn.commit()

            # Определяем, ожидает ли ДЗ после этой отправки
            # Если это не повтор и был is_homework_local, то hw_status теперь 'pending'
            # Если это повтор, то hw_status берем из базы (hw_status_db)
            final_homework_pending_for_menu = (not repeat and is_homework_local) or \
                                              (repeat and hw_status_db == 'pending')

            # В send_lesson_to_user, перед вызовом send_main_menu
            db_hw_type_row = await (
                await conn.execute("SELECT hw_type FROM user_courses WHERE user_id=? AND course_id=?",
                                   (user_id, course_id))).fetchone()
            db_hw_type = db_hw_type_row[0] if db_hw_type_row else None

            final_hw_type_for_menu = hw_type_local if not repeat and is_homework_local else db_hw_type

            logger.info(
                f"Перед send_main_menu: homework_pending={final_homework_pending_for_menu}, hw_status в БД={new_hw_status_for_db if not repeat else hw_status_db}, hw_type для меню={final_hw_type_for_menu}")
            await send_main_menu(
                user_id=user_id,
                course_id=course_id,
                lesson_num=lesson_num,  # Отправляем номер текущего урока
                version_id=version_id,
                homework_pending=final_homework_pending_for_menu,
                hw_type=final_hw_type_for_menu
            )

        logger.info(
            f"✅ Урок {lesson_num} (или сообщение о завершении/ошибке) полностью обработан для пользователя {user_id}.")

    except TelegramBadRequest as e:
        logger.error(f"💥 Ошибка Telegram API в send_lesson_to_user: {e}", exc_info=True)
        await bot.send_message(user_id,
                               escape_md("📛 Произошла ошибка при отправке урока (Telegram API). Мы уже разбираемся!"),
                               parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(f"💥 Общая ошибка в send_lesson_to_user: {e}", exc_info=True)
        await bot.send_message(user_id, escape_md(
            "📛 Что-то пошло не так при подготовке урока. Робот уже вызвал ремонтную бригаду!"),
                               parse_mode=ParseMode.MARKDOWN_V2)


@db_exception_handler
async def send_lesson_to_user(user_id: int, course_id: str, lesson_num: int, repeat: bool = False, level: int = 1):
    """Отправляет урок или сообщение о завершении курса, обновляет статус пользователя."""
    logger.info(
        f"🚀 send_lesson_to_user: user_id={user_id}, course_id={course_id}, lesson_num={lesson_num}, "
        f"repeat={repeat}, user_course_level={level}"
    )

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # 1. Получаем общее количество уроков в курсе (с lesson_num > 0)
            cursor_total = await conn.execute(
                "SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ? AND lesson_num > 0", (course_id,)
            )
            total_lessons_data = await cursor_total.fetchone()
            total_lessons = total_lessons_data[0] if total_lessons_data and total_lessons_data[0] is not None else 0
            logger.info(f"Для курса '{course_id}' найдено {total_lessons} уроков. Запрошен урок {lesson_num}.")

            # 2. Проверяем, не является ли запрошенный урок выходящим за рамки курса
            if lesson_num > total_lessons and total_lessons > 0:
                await _handle_course_completion(conn, user_id, course_id, lesson_num, total_lessons)
                return  # Завершение курса обработано

            # 3. Ищем контент запрошенного урока
            lesson_content = await _get_lesson_content_from_db(conn, course_id, lesson_num)

            if not lesson_content:  # Урок по номеру должен быть, но контента нет
                await _handle_missing_lesson_content(user_id, course_id, lesson_num, total_lessons)
                return  # Ошибка обработана

            # 4. Отправляем части урока
            is_homework_local, hw_type_local = await _send_lesson_parts(user_id, course_id, lesson_num, level,
                                                                        lesson_content)

            # 5. Обновляем статус пользователя и отправляем главное меню (если курс не завершен этой отправкой)
            # Если это был последний урок и он НЕ был домашкой, то завершение уже обработано выше (в _send_lesson_parts, если добавить)
            # или будет обработано в handle_homework_result.
            # Сейчас send_main_menu будет вызван всегда после отправки контента урока.
            # Но если это был последний урок И он НЕ ДЗ, то _handle_course_completion_after_sending_last_lesson вызовет сообщение о завершении.

            # Если это был последний урок и он не является домашкой, значит курс завершен этой отправкой
            if not repeat and not is_homework_local and lesson_num >= total_lessons and total_lessons > 0:
                logger.info(
                    f"Последний урок {lesson_num} (не ДЗ) курса '{course_id}' отправлен. Завершаем курс для user {user_id}.")
                await _update_user_course_after_lesson(conn, user_id, course_id, lesson_num, is_homework_local,
                                                       hw_type_local, repeat, level)
                await _handle_course_completion(conn, user_id, course_id, lesson_num,
                                                total_lessons)  # Отправляем сообщение о завершении
            else:
                # Обновляем данные пользователя и отправляем обычное меню
                version_id, new_hw_status, final_hw_type = await _update_user_course_after_lesson(
                    conn, user_id, course_id, lesson_num, is_homework_local, hw_type_local, repeat, level
                )
                if version_id:  # Если удалось получить version_id
                    final_homework_pending = (not repeat and is_homework_local) or \
                                             (
                                                         repeat and new_hw_status == 'pending')  # new_hw_status будет из базы для repeat

                    await send_main_menu(
                        user_id=user_id, course_id=course_id, lesson_num=lesson_num,
                        version_id=version_id, homework_pending=final_homework_pending,
                        hw_type=final_hw_type
                    )
                else:
                    logger.error(
                        f"Не удалось получить version_id для user {user_id}, курс {course_id} после отправки урока. Меню не отправлено.")

        logger.info(f"✅ Обработка для урока {lesson_num} курса '{course_id}' (user {user_id}) завершена.")

    except TelegramBadRequest as e:
        logger.error(
            f"💥 Ошибка Telegram API в send_lesson_to_user для user {user_id}, курс {course_id}, урок {lesson_num}: {e}",
            exc_info=True)
        await bot.send_message(user_id,
                               escape_md("📛 Произошла ошибка при отправке урока (Telegram API). Мы уже разбираемся!"),
                               parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error(
            f"💥 Общая ошибка в send_lesson_to_user для user {user_id}, курс {course_id}, урок {lesson_num}: {e}",
            exc_info=True)
        await bot.send_message(user_id, escape_md(
            "📛 Что-то пошло не так при подготовке урока. Робот уже вызвал ремонтную бригаду!"),
                               parse_mode=ParseMode.MARKDOWN_V2)


async def _get_lesson_content_from_db(conn, course_id: str, lesson_num: int) -> list:
    """Вспомогательная функция для получения контента урока из БД."""
    cursor_lesson = await conn.execute("""
        SELECT text, content_type, file_id, is_homework, hw_type, level 
        FROM group_messages
        WHERE course_id = ? AND lesson_num = ?
        ORDER BY id
    """, (course_id, lesson_num))
    return await cursor_lesson.fetchall()


async def _send_lesson_parts(user_id: int, course_id: str, lesson_num: int, user_course_level: int,
                             lesson_content: list) -> tuple[bool, str | None]:
    """Вспомогательная функция для отправки частей урока. Возвращает (is_homework, hw_type)."""
    is_homework_local = False
    hw_type_local = None
    parts_sent_count = 0

    logger.info(
        f"Отправка частей урока {lesson_num} ({len(lesson_content)} шт.) для {course_id}, user_level={user_course_level}")

    for k, (piece_text, content_type, file_id, is_homework, hw_type, piece_level) in enumerate(lesson_content, 1):
        current_piece_text = piece_text if piece_text else ""

        if piece_level > user_course_level:
            logger.info(
                f"Пропуск части {k} урока {lesson_num} (уровень сообщения {piece_level} > уровня пользователя {user_course_level})")
            continue

        safe_caption = escape_md(current_piece_text)

        try:
            if content_type == "text":
                if not current_piece_text.strip():
                    logger.error(f"Пустой текст в части {k} урока {lesson_num} ({course_id}). Пропуск.")
                    continue
                await bot.send_message(user_id, safe_caption, parse_mode=ParseMode.MARKDOWN_V2)
            elif file_id:
                # Динамический вызов метода отправки
                send_method_name = f"send_{content_type}"
                if hasattr(bot, send_method_name):
                    send_method = getattr(bot, send_method_name)
                    await send_method(user_id, file_id, caption=safe_caption, parse_mode=ParseMode.MARKDOWN_V2)
                else:
                    logger.warning(
                        f"Неизвестный content_type '{content_type}' с file_id для части {k} урока {lesson_num}.")
            elif content_type not in ["text"]:  # Если это не текст и нет file_id
                logger.error(
                    f"Отсутствует file_id для медиа ({content_type}) части {k} урока {lesson_num}, курс {course_id}. Подпись была: '{current_piece_text}'")

            parts_sent_count += 1
        except TelegramBadRequest as e_send_part:
            logger.error(
                f"Ошибка Telegram API при отправке части {k} ({content_type}, file_id: {file_id}) урока {lesson_num}: {e_send_part}",
                exc_info=True)
            # Решаем, прерывать ли весь урок или продолжать со следующей частью
            if "wrong file identifier" in str(e_send_part):
                logger.error(f"Обнаружен неверный file_id: '{file_id}'. Эта часть урока не будет отправлена.")
            # Можно добавить await bot.send_message(user_id, "Часть урока не удалось отправить...")
            continue  # Пробуем отправить следующую часть

        if is_homework:
            is_homework_local = True
            hw_type_local = hw_type
            logger.info(f"Часть {k} урока {lesson_num} является ДЗ типа: {hw_type_local}")

    logger.info(f"Обработано/отправлено {parts_sent_count} из {len(lesson_content)} частей урока {lesson_num}.")
    return is_homework_local, hw_type_local


async def _update_user_course_after_lesson(conn, user_id: int, course_id: str, lesson_num: int, is_homework: bool, hw_type: str | None,
                                           repeat: bool, user_course_level: int) -> tuple[str | None, str | None, str | None]:
    """Обновляет данные user_courses после отправки урока. Возвращает (version_id, new_hw_status, final_hw_type)."""
    cursor_user_course = await conn.execute(
        "SELECT version_id, hw_status FROM user_courses WHERE user_id = ? AND course_id = ? AND status = 'active'",
        (user_id, course_id)
    )
    row_user_course = await cursor_user_course.fetchone()

    if not row_user_course:
        logger.error(f"User {user_id} не найден в user_courses для {course_id} при обновлении статуса урока.")
        return None, None, None

    version_id, hw_status_db = row_user_course

    now_utc = datetime.now(pytz.utc)
    now_utc_str = now_utc.strftime('%Y-%m-%d %H:%M:%S')

    new_hw_status_for_db = hw_status_db
    new_hw_type_for_db = (await (await conn.execute("SELECT hw_type FROM user_courses WHERE user_id=? AND course_id=?",
                                                    (user_id, course_id))).fetchone() or (None,))[0]

    if not repeat:
        logger.info(f"Новый урок {lesson_num} отправлен для {user_id}. Время: {now_utc_str}. Это ДЗ: {is_homework}")
        new_hw_status_for_db = 'pending' if is_homework else 'none'
        new_hw_type_for_db = hw_type if is_homework else None  # hw_type_local из _send_lesson_parts

        await conn.execute(
            """UPDATE user_courses 
               SET hw_status = ?, hw_type = ?, current_lesson = ?, last_lesson_sent_time = ?
               WHERE user_id = ? AND course_id = ? AND status = 'active'""",
            (new_hw_status_for_db, new_hw_type_for_db, lesson_num, now_utc_str, user_id, course_id)
        )
        await log_action(user_id, "LESSON_SENT", course_id, lesson_num, new_value=str(user_course_level))
    else:
        logger.info(f"Урок {lesson_num} отправлен повторно для {user_id}. Время: {now_utc_str}")
        await conn.execute(
            "UPDATE user_courses SET last_lesson_sent_time = ? WHERE user_id = ? AND course_id = ? AND status = 'active'",
            (now_utc_str, user_id, course_id)
        )

    await conn.commit()

    # Определяем, какой hw_type показывать в меню
    # Если это новый урок и он ДЗ, то hw_type_local (который = hw_type из group_messages)
    # Если это повтор, или новый урок но не ДЗ, то берем hw_type из user_courses (который может быть от предыдущего ДЗ)
    final_hw_type_for_menu = new_hw_type_for_db if not repeat and is_homework else new_hw_type_for_db

    return version_id, new_hw_status_for_db if not repeat else hw_status_db, final_hw_type_for_menu


async def _handle_course_completion(conn, user_id: int, course_id: str, requested_lesson_num: int,
                                    total_lessons_current_level: int):
    """Обрабатывает завершение курса: отправляет сообщение и обновляет статус в БД."""
    logger.info(
        f"Курс {course_id} завершен для user_id={user_id}. Последний урок был {total_lessons_current_level}, запрошен {requested_lesson_num}.")
    course_title_safe = escape_md(await get_course_title(course_id))

    message_text = (
        f"🎉 Поздравляем с успешным завершением курса «{course_title_safe}»\\! 🎉\n\n"
        f"{escape_md('Вы прошли все уроки текущего уровня. Что вы хотите сделать дальше?')}"
    )

    builder = InlineKeyboardBuilder()

    # Проверяем, есть ли следующий уровень для этого курса и какой текущий уровень у пользователя
    cursor_user_level = await conn.execute(
        "SELECT level FROM user_courses WHERE user_id = ? AND course_id = ?",
        (user_id, course_id)
    )
    user_level_data = await cursor_user_level.fetchone()
    current_user_level = user_level_data[0] if user_level_data else 1  # По умолчанию 1, если вдруг нет записи

    next_level_to_check = current_user_level + 1
    cursor_next_level_lessons = await conn.execute(
        "SELECT 1 FROM group_messages WHERE course_id = ? AND level = ? LIMIT 1",
        (course_id, next_level_to_check)
    )
    has_next_level_lessons = await cursor_next_level_lessons.fetchone()

    if has_next_level_lessons:
        builder.button(
            text=escape_md(f"🚀 Начать {next_level_to_check}-й уровень!"),
            callback_data=RestartCourseCallback(course_id_str=course_id, action="next_level").pack()  # Добавим action
        )

    # Кнопка повторить ТЕКУЩИЙ уровень (если он не первый, или если хотим всегда давать возможность)
    # Можно добавить условие, if current_user_level > 0 или просто всегда показывать
    builder.button(
        text=escape_md(f"🔁 Повторить {current_user_level}-й уровень"),
        callback_data=RestartCourseCallback(course_id_str=course_id, action="restart_current_level").pack()
    )

    builder.button(text=escape_md("Выбрать другой курс"), callback_data="select_other_course")
    builder.button(text=escape_md("Оставить отзыв"), callback_data="leave_feedback")
    builder.adjust(1)  # Все кнопки в один столбец

    await bot.send_message(
        chat_id=user_id,
        text=message_text,
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    # Статус 'completed' теперь будет означать, что завершен ТЕКУЩИЙ УРОВЕНЬ КУРСА.
    # Если пользователь перейдет на следующий уровень, статус снова станет 'active'.
    await conn.execute(
        "UPDATE user_courses SET status = 'completed', is_completed = 1 WHERE user_id = ? AND course_id = ?",
        # is_completed для текущего уровня
        (user_id, course_id)
    )
    await conn.commit()
    await log_action(user_id, "COURSE_LEVEL_COMPLETED", course_id, lesson_num=requested_lesson_num,
                     # lesson_num - это current_lesson
                     details=f"Завершен уровень {current_user_level}. Всего уроков на уровне (примерно): {total_lessons_current_level}")

async def _handle_missing_lesson_content(user_id: int, course_id: str, lesson_num: int, total_lessons: int):
    """Обрабатывает ситуацию, когда контент урока не найден."""
    logger.warning(
        f"⚠️ Контент для урока {lesson_num} не найден в курсе {course_id}, "
        f"хотя такой номер урока допустим (всего {total_lessons} уроков)."
    )
    course_title_safe = escape_md(await get_course_title(course_id))
    await bot.send_message(
        user_id,
        f"Извините, урок №{lesson_num} для курса «{course_title_safe}» временно недоступен или еще не был добавлен. "
        f"Пожалуйста, попробуйте позже или свяжитесь с поддержкой.",
        parse_mode=ParseMode.MARKDOWN_V2  # Текст формируется безопасно
    )

async def scheduled_lesson_check(user_id: int):
    """Запускает проверку расписания уроков для пользователя каждые 7 минут."""
    while True:
        await check_lesson_schedule(user_id)
        await asyncio.sleep(1 * 60)  # Каждую 1 минуту



# функция для кэширования статуса курса пользователя
@lru_cache(maxsize=100)
async def get_course_status(user_id: int) -> tuple | None:
    """Кэшируем статус курса на 5 минут"""
    logger.info(f"кэш get_course_status {user_id=}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT uc.course_id, c.title, uc.version_id, uc.current_lesson 
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ?
            """, (user_id,))
            return await cursor.fetchone()
    except Exception as e:
        logger.error(f"Error getting course status for user {user_id}: {e}")
        return None


@dp.message(Command("set_timezone"))
async def set_timezone(message: types.Message):
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(
        text="🕒 Автоопределение часового пояса",
        callback_data="auto_timezone"
    ))
    await message.answer(
        "Выберите ваш часовой пояс:",
        reply_markup=keyboard.as_markup()
    )


async def get_next_lesson_time(user_id: int, course_id: str, current_lesson_for_display: int) -> str:
    """
    Получает отформатированное время следующего урока для пользователя в его часовом поясе.
    Если текущий урок последний, возвращает соответствующее сообщение.

    Args:
        user_id: ID пользователя.
        course_id: ID курса.
        current_lesson_for_display: Номер урока, который СЕЙЧАС отображается в меню
                                     (то есть, последний отправленный пользователю).
    """
    logger.info(
        f"🚀 get_next_lesson_time: user_id={user_id}, course_id={course_id}, current_lesson_for_display={current_lesson_for_display}")
    try:
        # Получаем группу пользователя
        async with aiosqlite.connect(DB_FILE) as conn:
            # 1. Получаем общее количество уроков в курсе
            cursor_total_lessons = await conn.execute(
                "SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ? AND lesson_num > 0", (course_id,)
            )
            total_lessons_data = await cursor_total_lessons.fetchone()
            total_lessons = total_lessons_data[0] if total_lessons_data and total_lessons_data[0] is not None else 0

            if total_lessons > 0 and current_lesson_for_display >= total_lessons:
                logger.info(
                    f"Урок {current_lesson_for_display} является последним для курса {course_id} (всего {total_lessons}).")
                return "🎉 Это был последний урок курса!"

            # 2. Получаем данные о курсе пользователя
            cursor_user_course = await conn.execute("""
                        SELECT first_lesson_sent_time, activation_date, current_lesson
                        FROM user_courses
                        WHERE user_id = ? AND course_id = ? AND status = 'active'
                    """, (user_id, course_id))
            user_course_data = await cursor_user_course.fetchone()

            if not user_course_data:
                logger.warning(
                    f"Нет данных об активном курсе {course_id} для пользователя {user_id} в get_next_lesson_time.")
                return "в ближайшее время (нет данных о курсе)"

            first_lesson_sent_time_str, activation_date_str, db_current_lesson = user_course_data
            # db_current_lesson - это номер последнего отправленного урока, сохраненный в user_courses

            # 3. Определяем время отправки первого урока в UTC
            # (Предполагаем, что в БД время хранится как строка в формате UTC, но "наивное")
            base_time_str_for_calc = first_lesson_sent_time_str if first_lesson_sent_time_str else activation_date_str
            if not base_time_str_for_calc:
                logger.error(
                    f"Отсутствует и first_lesson_sent_time, и activation_date для user_id={user_id}, course_id={course_id}")
                return "ошибка расчета времени (нет базовой даты)"

            try:
                # Пытаемся сначала как ISO, потом как ваш формат. Это делает код гибче.
                try:
                    first_lesson_naive_utc = datetime.fromisoformat(base_time_str_for_calc)
                except ValueError:
                    first_lesson_naive_utc = datetime.strptime(base_time_str_for_calc, '%Y-%m-%d %H:%M:%S')
            except ValueError as e_parse:
                logger.error(f"Ошибка парсинга даты '{base_time_str_for_calc}' для user_id={user_id}: {e_parse}")
                return "ошибка расчета времени (формат даты)"

            first_lesson_aware_utc = pytz.utc.localize(first_lesson_naive_utc)
            logger.info(
                f"Для user_id={user_id}, course_id={course_id}: first_lesson_aware_utc={first_lesson_aware_utc}, db_current_lesson={db_current_lesson}")

            # 4. Получаем интервал отправки уроков
            lesson_interval_hours = float(settings.get("message_interval", 24.0))  # Убедимся, что это float

            # 5. Рассчитываем время следующего урока (db_current_lesson + 1) в UTC
            # db_current_lesson - это номер последнего отправленного урока.
            # Значит, (db_current_lesson)-й интервал после first_lesson_aware_utc определяет время начала (db_current_lesson + 1)-го урока.
            # Если db_current_lesson = 0 (только активирован, еще ничего не отправлено), то 0-й интервал - это сам first_lesson_aware_utc (для урока 1)
            # Если db_current_lesson = 1 (урок 1 отправлен), то 1-й интервал - это first_lesson_aware_utc + timedelta (для урока 2)
            next_lesson_to_send_number = db_current_lesson + 1

            # Если db_current_lesson = 0, значит, первый урок (№1) еще не отправлен по расписанию,
            # его время - это first_lesson_aware_utc.
            # Если db_current_lesson > 0, то следующий урок это db_current_lesson + 1,
            # и он наступит через db_current_lesson интервалов после first_lesson_aware_utc
            if db_current_lesson == 0:  # Если current_lesson = 0, то следующий - это первый урок.
                # Если первый урок отправляется сразу после активации (т.е. first_lesson_sent_time - это оно),
                # то время для 1-го урока - это first_lesson_aware_utc.
                # Но get_next_lesson_time обычно вызывается ПОСЛЕ отправки текущего урока,
                # поэтому db_current_lesson уже будет > 0.
                # Если же current_lesson=0 и мы хотим узнать время для урока 1, то это first_lesson_aware_utc
                time_of_lesson_event_utc = first_lesson_aware_utc
            else:
                time_of_lesson_event_utc = first_lesson_aware_utc + timedelta(
                    hours=lesson_interval_hours) * db_current_lesson

            logger.info(f"Расчетное время для урока {next_lesson_to_send_number} (UTC): {time_of_lesson_event_utc}")

            # 6. Получаем часовой пояс пользователя
            user_timezone_str = DEFAULT_TIMEZONE
            cursor_user_tz = await conn.execute("SELECT timezone FROM users WHERE user_id = ?", (user_id,))
            user_tz_data = await cursor_user_tz.fetchone()
            if user_tz_data and user_tz_data[0]:
                user_timezone_str = user_tz_data[0]

            try:
                user_actual_timezone = pytz.timezone(user_timezone_str)
            except pytz.exceptions.UnknownTimeZoneError:
                logger.warning(
                    f"Неизвестный часовой пояс '{user_timezone_str}' для пользователя {user_id}. Используется DEFAULT_TIMEZONE.")
                user_actual_timezone = pytz.timezone(DEFAULT_TIMEZONE)

            # 7. Конвертируем время следующего урока в часовой пояс пользователя
            next_lesson_time_local = time_of_lesson_event_utc.astimezone(user_actual_timezone)

            # 8. Форматирование для вывода
            MONTHS_GENITIVE = [
                "января", "февраля", "марта", "апреля", "мая", "июня",
                "июля", "августа", "сентября", "октября", "ноября", "декабря"
            ]
            # locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8') # Убедитесь, что локаль установлена глобально или здесь
            # day_name_local = next_lesson_time_local.strftime("%a") # Может быть на английском без локали

            # Для гарантированно русских дней недели (если strftime %a не работает как надо)
            days_ru = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            day_name_local = days_ru[next_lesson_time_local.weekday()]

            month_genitive = MONTHS_GENITIVE[next_lesson_time_local.month - 1]
            formatted_time = next_lesson_time_local.strftime(
                f"%H:%M  ({day_name_local}, %d {month_genitive} %Y)")  # Добавил год для ясности

            logger.info(
                f"Для user_id={user_id} следующий урок ({next_lesson_to_send_number}) в {user_timezone_str}: {formatted_time}")
            return formatted_time

    except Exception as e:
        logger.error(
            f"Ошибка при получении времени следующего урока для user_id={user_id}, course_id={course_id}: {e}",
            exc_info=True)
        return "ошибка расчета времени"


@dp.callback_query(F.data == "menu_support")
@db_exception_handler
async def cmd_support_callback(query: types.CallbackQuery, state: FSMContext):
    """Обработчик для кнопки 'Поддержка'."""
    user_id = query.from_user.id
    logger.info(f"10 cmd_support_callback user_id={user_id}")

    # Устанавливаем состояние ожидания сообщения от пользователя
    await state.set_state(SupportRequest.waiting_for_message)

    # Изменяем текст сообщения для пользователя
    await query.message.edit_text(
        "⏳ Пожалуйста, напишите ваш вопрос в чат. Ваш запрос будет передан в службу поддержки.",
        parse_mode=None
    )
    await query.answer()  # Отвечаем на callback, чтобы убрать "часики"



def get_main_menu_inline_keyboard(
        course_numeric_id: int,  # ID текущего отображаемого курса
        lesson_num: int,
        user_tariff: str,
        user_has_other_active_courses: bool = False,  # Новый флаг
        homework_pending: bool = False
        # courses_button_text убираем, кнопка "Мои курсы" будет стандартной
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="📚 Текущий урок (прислать повторно)",
            callback_data=CourseCallback(
                action="menu_cur",
                course_id=course_numeric_id,
                lesson_num=lesson_num
            ).pack()
        )
    )

    if user_tariff == "v11" and homework_pending:  # Ваша логика для самоодобрения
        builder.row(
            InlineKeyboardButton(
                text="✅ СамоОдобрить ДЗ",
                callback_data=CourseCallback(
                    action="self_approve_hw",
                    course_id=course_numeric_id,
                    lesson_num=lesson_num
                ).pack()
            )
        )

    # Кнопки управления курсами
    row_buttons = []
    row_buttons.append(InlineKeyboardButton(text="📈 Прогресс", callback_data="menu_progress"))

    # Кнопка "Мои курсы" (ведет к списку для переключения или покупки)
    row_buttons.append(InlineKeyboardButton(text="📚 Все курсы", callback_data="select_other_course"))

    # Кнопка "Остановить текущий курс"
    # Показываем, только если это меню для конкретного активного курса (course_numeric_id > 0)
    if course_numeric_id > 0:  # или другая проверка, что это меню активного курса
        row_buttons.append(InlineKeyboardButton(
            text="⏹️ Остановить этот курс",
            callback_data=MainMenuAction(action="stop_course", course_id_numeric=course_numeric_id).pack()
        ))

    builder.row(*row_buttons)  # Размещаем кнопки в ряд, aiogram сам распределит, если их много
    # или используйте builder.adjust()

    builder.row(InlineKeyboardButton(text="📞 Поддержка", callback_data="menu_support"))
    return builder.as_markup()

# ============= для взаимодействия с группами уроков. Работает при добавлении материала в группу ===========

@db_exception_handler
async def save_message_to_db(group_id: int, message: Message):
    """
    Сохраняет сообщение в базу данных, определяя его тип, принадлежность к уроку/домашнему заданию,
    и обрабатывая различные типы контента (текст, фото, видео, документы, аудио).
    Использует теги в тексте сообщения для определения номера урока и типа контента.

    Args:
        group_id (int): ID группы, из которой пришло сообщение.
        message (Message): Объект сообщения от Telegram.
    """
    global lesson_stack, last_message_info
    group_id_str = str(message.chat.id)  # Получаем ID группы как строку
    mes_id = message.message_id
    logger.info(f"Saving message {mes_id=} from group {group_id_str=}")

    # Шаг 1: Определение course_id для данного group_id из настроек
    logger.info(f"777 ищем course_id для group_id {group_id_str}.")
    course_id = next(
        (course for g, course in settings["groups"].items() if g == group_id_str),
        None
    )

    if not course_id:
        logger.warning(f"777 Не найден course_id для group_id {group_id_str}.")
        return  # Если course_id не найден, прекращаем обработку

    logger.info(f"777 это {course_id=}.")

    # Шаг 2: Извлечение информации из сообщения
    text = message.text or message.caption or ""  # Получаем текст или подпись к медиа
    user_id = message.from_user.id if message.from_user else None
    file_id = None  # Изначально file_id нет
    logger.info(f"333!!! это {user_id=}  {course_id=}")

    # Determine content type and file_id
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.document:
        file_id = message.document.file_id
    elif message.audio and message.audio.file_id:
        file_id = message.audio.file_id
    elif message.voice:  # Add voice message handling
        file_id = message.voice.file_id
    elif message.animation:  # Добавляем обработку animation
        file_id = message.animation.file_id
    else:
        content_type = "text"
        file_id = None # Ensure file_id is None for text messages

    logger.info(f"777!!! это{file_id=}")

    # 3. Extract tags from text
    start_lesson_match = re.search(r"\*START_LESSON (\d+)", text)
    level_match = re.search(r"\*LEVEL (\d+)", text)
    end_lesson_match = re.search(r"\*END_LESSON (\d+)", text)
    hw_start_match = re.search(r"\*HW_START", text)
    hw_type_match = re.search(r"\*HW_TYPE\s*(\w+)", text)
    course_end_match = re.search(r"\*COURSE_END", text)

    lesson_num = None  # Номер урока (изначально None)
    is_homework = False  # Является ли сообщение домашним заданием (изначально False)
    hw_type = 'none'  # Тип домашнего задания (изначально 'none')

    # Шаг 4: Обработка маркеров домашнего задания
    if hw_type_match:
        hw_type = hw_type_match.group(1).lower()  # Получаем тип ДЗ и приводим к нижнему регистру
        logger.info(f"Обнаружен тип домашнего задания: {hw_type}")

    # Шаг 5: Очистка текста сообщения от маркеров
    cleaned_text = re.sub(r"\*START_LESSON (\d+)", "", text)  # Удаляем маркеры начала урока
    cleaned_text = re.sub(r"\*LEVEL (\d+)", "", cleaned_text)  # Удаляем маркеры УРОВНЯ
    cleaned_text = re.sub(r"\*END_LESSON (\d+)", "", cleaned_text)  # Удаляем маркеры конца урока
    cleaned_text = re.sub(r"\*HW_START", "", cleaned_text)  # Удаляем маркеры начала ДЗ
    cleaned_text = re.sub(r"\*HW_END", "", cleaned_text)  # Удаляем маркеры начала ДЗ
    cleaned_text = re.sub(r"\*HW_TYPE\s*(\w+)", "", cleaned_text)  # Удаляем маркеры типа ДЗ
    cleaned_text = re.sub(r"\*COURSE_END", "", cleaned_text)

    # Шаг 6: Соединение с базой данных
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Шаг 7: Обработка маркеров начала и конца уроков
            if start_lesson_match:
                lesson_num = int(start_lesson_match.group(1))  # Получаем номер урока
                if group_id_str not in lesson_stack:
                    lesson_stack[group_id_str] = []  # Инициализируем стек для группы, если его нет
                lesson_stack[group_id_str].append(lesson_num)  # Добавляем номер урока в стек
                logger.info(f"Начало урока {lesson_num} в группе {group_id_str}.")

            elif end_lesson_match:
                lesson_num = int(end_lesson_match.group(1))  # Получаем номер урока
                if group_id_str in lesson_stack and lesson_stack[group_id_str]:
                    if lesson_stack[group_id_str][-1] == lesson_num:
                        lesson_stack[group_id_str].pop()  # Удаляем номер урока из стека, если он совпадает
                        logger.info(f"Окончание урока {lesson_num} в группе {group_id_str}.")
                    else:
                        logger.warning(
                            f"Несоответствие END_LESSON tag для группы {group_id_str}. "
                            f"Ожидалось {lesson_stack[group_id_str][-1]}, получено {lesson_num}."
                        )
                else:
                    logger.warning(f"Неожиданный END_LESSON tag для группы {group_id_str}. Стек пуст.")

            elif hw_start_match:
                # Шаг 8: Обработка маркера начала домашнего задания
                if group_id_str in lesson_stack and lesson_stack[group_id_str]:
                    lesson_num = lesson_stack[group_id_str][-1]  # Получаем номер текущего урока из стека
                else:
                    lesson_num = last_message_info.get(group_id_str, {}).get("lesson_num")  # Берем номер из последнего сообщения
                    logger.warning(
                        f"HW_START Используется последний известный урок: {lesson_num}... "
                        f"без активного урока в группе {group_id_str}."
                    )
                is_homework = True  # Устанавливаем флаг, что это домашнее задание
                logger.info(f"Найдено начало домашнего задания для урока {lesson_num} в группе {group_id_str}.")

            elif course_end_match:
                # Шаг 9: Обработка окончания курса
                await process_course_completion(int(group_id_str), conn)
                logger.info(f"Курс окончен в группе {group_id_str}. Статистика обработана.")
                return  # Прекращаем дальнейшую обработку сообщения

            # Шаг 10: Если есть активные уроки, берем последний
            if group_id_str in lesson_stack and lesson_stack[group_id_str]:
                lesson_num = lesson_stack[group_id_str][-1]  # Получаем номер текущего урока из стека

            # Extract course information from the first message
            course_snippet = None
            course_title = None
            if lesson_stack.get(group_id_str) is None and cleaned_text.startswith("*Курс"):
                # This is the first message
                course_snippet = extract_course_snippet(cleaned_text)
                course_title = extract_course_title(cleaned_text)
                lesson_num = 0  # First message has lesson_num = 0

                # Check if a description already exists
                cursor = await conn.execute("SELECT 1 FROM group_messages WHERE course_id = ? AND lesson_num = 0",
                                            (course_id,))
                existing_record = await cursor.fetchone()

                # If record exists, turn other lesson_num = 0 to negative before inserting the first description
                if existing_record:
                    await conn.execute("UPDATE group_messages SET lesson_num = -message_id WHERE course_id = ? AND lesson_num = 0",
                                        (course_id,))
                    logger.info(f"Old records from {group_id=} was update to negative lesson_num")
                    await conn.commit()

                # Update course title and snippet
                await conn.execute("""
                    UPDATE courses
                    SET title = ?, description = ?
                    WHERE course_id = ?
                """, (course_title, course_snippet, course_id))
                logger.info(f"6000 записали сниппет в базу {group_id} type {message.content_type}")
                await conn.commit()

            # 6. Validate text for text messages
            if message.content_type == "text" and not cleaned_text.strip():
                logger.warning(f"Текст не может быть пустым для текстовых сообщений. Пропускаем сохранение.")
                return  # Прерываем сохранение, если текст пустой

            logger.info(f"13 {file_id=} {hw_type=}")
            level=1
            if level_match:
                level = int(level_match.group(1))  # Получаем номер урока
            # Шаг 13: Сохранение сообщения в базу данных
            await conn.execute("""
                INSERT INTO group_messages (
                    group_id, message_id, content_type, text, file_id,
                    is_forwarded, forwarded_from_chat_id, forwarded_message_id,
                    course_id, lesson_num, level, is_homework, hw_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                group_id_str, message.message_id, message.content_type, cleaned_text,
                file_id, message.forward_origin is not None,
                message.forward_origin.chat.id if message.forward_origin and hasattr(message.forward_origin, 'chat') else None,
                message.forward_origin.message_id if message.forward_origin and hasattr(message.forward_origin, 'id') and message.forward_origin.id else None, # Ensure message_id exists
                course_id, lesson_num, level, is_homework, hw_type
            ))
            await conn.commit()

            # Шаг 14: Обновление информации о последнем сообщении
            last_message_info[group_id_str] = {"lesson_num": lesson_num}
            logger.info(f"last_message_info {group_id_str=} = {lesson_num=}")

            logger.info(
                f"Сообщение сохранено: {group_id_str=}, {lesson_num=}, {course_id=}, "
                f"{message.content_type=}, {is_homework=}, {cleaned_text=}, {file_id=}"
            )

    # Обработка исключений
    except Exception as e:(
        logger.error(f"❌ Ошибка в функции save_message_to_db: {e}", exc_info=True))


@db_exception_handler
async def test_and_send_random_lesson(course_id: str, conn: aiosqlite.Connection):
    """Тестирует курс и отправляет случайный урок администраторам."""
    try:
        # Получаем group_id для курса
        cursor = await conn.execute("""
            SELECT group_id FROM courses 
            WHERE course_id = ?
        """, (course_id,))
        group_id_record = await cursor.fetchone()

        if not group_id_record:
            logger.warning(f"Не найден group_id для курса {course_id}.")
            return

        group_id = group_id_record[0]

        # Получаем случайный урок
        cursor = await conn.execute("""
            SELECT lesson_num FROM group_messages
            WHERE course_id = ? AND lesson_num IS NOT NULL
            ORDER BY RANDOM()
            LIMIT 1
        """, (course_id,))
        random_lesson = await cursor.fetchone()

        if not random_lesson:
            logger.warning(f"Не найдены уроки для курса {course_id}.")
            return

        lesson_num = random_lesson[0]

        # Получаем содержимое урока
        cursor = await conn.execute("""
            SELECT content_type, text, file_id FROM group_messages
            WHERE course_id = ? AND lesson_num = ?
            ORDER BY id ASC
        """, (course_id, lesson_num))
        lesson_messages = await cursor.fetchall()

        if not lesson_messages:
            logger.warning(f"Не найдено содержимое для урока {lesson_num} курса {course_id}.")
            return

        # 4. Send lesson content to admins
        if ADMIN_GROUP_ID:
            course_name = settings["groups"].get(group_id, "Unknown Course")
            await bot.send_message(chat_id=ADMIN_GROUP_ID,
                                   text=f"Случайный урок курса {course_name} ({course_id}), урок {lesson_num}:",
                                   parse_mode=None)

            for content_type, text, file_id in lesson_messages:
                if content_type == "video" and file_id:
                    await bot.send_video(ADMIN_GROUP_ID, video=file_id, caption=text or None, parse_mode=None)
                elif content_type == "photo" and file_id:
                    await bot.send_photo(ADMIN_GROUP_ID, photo=file_id, caption=text or None, parse_mode=None)
                elif content_type == "document" and file_id:
                    await bot.send_document(ADMIN_GROUP_ID, document=file_id, caption=text or None, parse_mode=None)
                elif content_type == "audio" and file_id:
                    await bot.send_audio(ADMIN_GROUP_ID, audio=file_id, caption=text or None, parse_mode=None)
                elif content_type == "animation" and file_id:
                    await bot.send_animation(ADMIN_GROUP_ID, animation=file_id, caption=text or None,
                                             parse_mode=None)
                elif content_type == "voice" and file_id:
                    await bot.send_voice(ADMIN_GROUP_ID, voice=file_id, caption=text or None, parse_mode=None)
                elif text:
                    await bot.send_message(ADMIN_GROUP_ID, text=text, parse_mode=None)

            logger.info(
                f"Случайный урок курса {course_name} ({course_id}), урок {lesson_num} отправлен администраторам.")
        else:
            logger.warning("ADMIN_GROUP_ID не задан. Урок не отправлен.")

    except Exception as e:
        logger.error(f"Ошибка при тестировании и отправке урока: {e}")


@db_exception_handler # как курс закончен - подведём статистику и отправляем админам *COURSE_END — когда приходит
async def process_course_completion(group_id: int, conn: aiosqlite.Connection):
    """Обрабатывает завершение курса и отправляет статистику в группу администраторов."""
    logger.info(f"Processing course completion for group {group_id}")
    try:
        # вставил в самое начало 09-04
        cursor = await conn.execute("""
            SELECT course_id FROM courses 
            WHERE group_id = ?
        """, (group_id,))
        course_id_record = await cursor.fetchone()
        logger.info(f"{course_id_record=} Курс {group_id} завершен")
        if not course_id_record:
            logger.warning(f"Не найден course_id для group_id {group_id}.")
            return
        else:
            logger.info(f" course_id={course_id_record[-1]}")

        course_id = course_id_record[0]

        cursor = await conn.execute("""
            SELECT course_id FROM group_messages 
            WHERE group_id = ? LIMIT 1
        """, (group_id,))
        course_id_record = await cursor.fetchone()

        if not course_id_record:
            logger.warning(f"Не найден course_id для group_id {group_id}.")
            return

        course_id = course_id_record[0]
        # Подсчет статистики
        cursor = await conn.execute("SELECT COUNT(*) FROM group_messages WHERE group_id = ?", (group_id,))
        total_messages = (await cursor.fetchone())[0]

        cursor = await conn.execute("""
            SELECT COUNT(DISTINCT lesson_num) FROM group_messages 
            WHERE group_id = ? AND lesson_num IS NOT NULL
        """, (group_id,))
        total_lessons = (await cursor.fetchone())[0]

        # Формирование сообщения со статистикой
        stats_message = (
            f"Курс {course_id} завершен.\n"
            f"Всего сообщений: {total_messages}\n"
            f"Всего уроков: {total_lessons} (включая вступление)  COUNT(DISTINCT lesson_num) \n"
        )
        # Сохраняем group_id в таблицу courses
        await conn.execute("""
            UPDATE courses SET group_id = ? WHERE course_id = ?
        """, (group_id, course_id)) # group_id == course_id
        await conn.commit()
        logger.info(f"5 Сохранили group_id {group_id} для курса {course_id}")

        # Отправка статистики в группу администраторов
        if ADMIN_GROUP_ID:
            await bot.send_message(chat_id=ADMIN_GROUP_ID, text=stats_message, parse_mode=None)
            logger.info(f"Статистика курса отправлена в группу администраторов ({ADMIN_GROUP_ID}).")
        else:
            logger.warning("ADMIN_GROUP_ID не задан. Статистика не отправлена.")

        # Отправляем урок администраторам
        await test_and_send_random_lesson(course_id, conn)

    except Exception as e:
        logger.error(f"Ошибка при обработке завершения курса: {e}")


def extract_course_snippet(text: str) -> str:
    """Извлекает сниппет курса из первого сообщения."""
    description_start = text.find("Описание курса:")
    if description_start == -1:
        return ""
    description_end = text.find("*Тарифы:*")
    if description_end == -1:
        return text[description_start + len("Описание курса:"):].strip()
    return text[description_start + len("Описание курса:"):description_end].strip()


def extract_course_title(text: str) -> str:
    """Извлекает название курса из первого сообщения."""
    title_start = text.find("*Курс")
    if title_start == -1:
        return ""
    title_end = text.find("*", title_start + 1)
    if title_end == -1:
        return text[title_start + 1:].strip()
    return text[title_start + 1:title_end].strip()


@db_exception_handler
async def import_settings_to_db():
    """Импортирует курсы и коды активации в БД"""
    logger.info("Starting settings import...")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем максимальный id на старте (если таблица пустая — начнем с 999)
            cursor = await conn.execute("SELECT MAX(id) FROM courses")
            row = await cursor.fetchone()
            max_id = row[0] if row[0] is not None else 0

            # Добавляем курсы
            for group_id, course_id in settings["groups"].items():
                max_id += 1  # Увеличиваем id для каждого нового курса
                await conn.execute("""
                    INSERT OR IGNORE INTO courses 
                    (id, course_id, group_id, title, message_interval) 
                    VALUES (?, ?, ?, ?, ?)
                """, (max_id, course_id, group_id, course_id, settings.get("message_interval", 24)))
                logger.info(f"Added course: {course_id} с id={max_id}")

            # Добавляем коды активации
            for code, code_info in settings["activation_codes"].items():
                await conn.execute("""
                    INSERT OR IGNORE INTO course_activation_codes 
                    (code_word, course_id, version_id, price_rub)
                    VALUES (?, ?, ?, ?)
                """, (
                    code,
                    code_info["course"],
                    code_info["version"],
                    code_info["price"]
                ))
                #logger.info(f"Added code: {code}")

            await conn.commit()
            logger.info("Settings imported successfully")

    except Exception as e:
        logger.error(f"Import error: {str(e)}")
        raise



# 14-04 - Проверка доступа в группах
async def check_groups_access(bot: Bot, raw_id: int, gr_name:str):
    """Проверяет доступ бота в указанной группе и возвращает отчет."""
    try:
        group_id = int(raw_id)
        chat = await bot.get_chat(group_id)
        escaped_title = chat.title  # убрали экранирование
        if chat.username:
            link = f"[{escaped_title}](t.me/{chat.username})"
        else:
            link = f"[{escaped_title}](t.me/c/{str(chat.id).replace('-100', '')})"

        if chat.type == "private":
            logger.info(f" {group_id} OK (Private chat) ")
            return f"{group_id} OK (Private chat) "
        else:
            logger.info(f" {group_id} OK {link} ")
            return f"{group_id} OK {link} "

    except TelegramBadRequest as e:
        logger.warning(f"Ошибка: {gr_name} | ID: {raw_id}\n Подробнее: {str(e)}")
        return f"Ошибка: {gr_name} | ID: {raw_id}\n Подробнее: {str(e)}"



async def send_startup_message(bot: Bot, admin_group_id: int):
    """Отправляет сообщение админам о запуске бота и статусе группов."""
    global settings
    logger.info(f"222 {len(settings)=}")
    channel_reports = []
    kanalz=settings.get("groups", {}).items()
    logger.info(f"555555555555555 Внутри функции send_startup_message {kanalz=}")

    for raw_id, gr_name in kanalz:
        logger.info(f"Внутри функции send_startup_message")
        report = await check_groups_access(bot, raw_id, gr_name)
        channel_reports.append(report)

    logger.warning("перед отправкой сообщения админам")
    # Формирование текста сообщения для администраторов
    message_text = "Бот запущен\n\nСтатус групп курсов:\n" + "\n".join(channel_reports) + \
                   "\nможно: /add_course <group_id> <course_id> <code1> <code2> <code3>"

    # Отправка сообщения в группу администраторов
    try:
        await bot.send_message(admin_group_id, message_text, parse_mode=None)
    except Exception as e:
        logger.error(f"Ошибка при отправке стартового сообщения в группу администраторов: {e}")
    logger.info("Стартовое сообщение отправлено администраторам")



# Пользовательский фильтр для проверки ID группы
class IsCourseGroupFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.chat.id in COURSE_GROUPS

#=================================================   обработчики сообщений   ====================================================

#@dp.message(F.chat.id.in_(COURSE_GROUPS))
@dp.message(IsCourseGroupFilter())
@db_exception_handler # Обработчик новых сообщений в группах курсов
async def handle_group_message(message: Message):
    """Обрабатывает сообщения из группы."""
    logger.info(f"COURSE_GROUPS ПРИШЛО в {message.chat.id}, mes_id={message.message_id} {COURSE_GROUPS}")

    if message.chat.type == "private":
        logger.warning(f"!!приватное: {message.chat.id}, message_id={message.message_id}")
        await message.answer("Приватные сообщения не обрабатываются.", parse_mode=None)
        return

    await save_message_to_db(message.chat.id, message)


# Админские команды
#=======================================================================================================================
# Admin command to reply to user


@dp.callback_query(F.chat.id == ADMIN_GROUP_ID,lambda c: c.data in ["export_db", "import_db"])
async def handle_admin_actions(callback: CallbackQuery):
    if callback.data == "export_db":
        await export_db(callback.message)
    elif callback.data == "import_db":
        await import_db(callback.message)

@dp.message(Command("export_db"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def export_db(message: types.Message):  # types.Message instead of Message
    """Экспорт данных из базы данных в JSON-файл. Только для администраторов."""
    logger.info("3 Получена команда /export_db")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Экспорт таблиц
            tables = ["users", "courses", "course_versions", "user_courses", "group_messages",
                      "course_activation_codes", "user_actions_log", "course_reviews", # ДОБАВЛЕНО
                "homework_gallery", "admin_context", "user_states"] # Добавил остальные из вашего init_db
            export_data = {}

            for table in tables:
                cursor = await conn.execute(f"SELECT * FROM {table}")
                rows = await cursor.fetchall()
                columns = [column[0] for column in cursor.description]
                export_data[table] = [dict(zip(columns, row)) for row in rows]

        # Сохранение данных в файл
        export_file = "database_export.json"
        with open(export_file, "w", encoding="utf-8") as f:
            json.dump(export_data, f, ensure_ascii=False, indent=4)

        # Отправка файла администраторам
        with open(export_file, "rb") as f:
            await message.answer_document(
                document=types.BufferedInputFile(f.read(), filename=export_file),
                caption="📦 База данных успешно экспортирована в JSON."
            )

        logger.info("База данных успешно экспортирована.")
    except Exception as e:
        logger.error(f"Ошибка при экспорте базы данных: {e}")
        await message.answer("❌ Произошла ошибка при экспорте базы данных.", parse_mode=None)

@dp.message(Command("import_db"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def import_db(message: types.Message):  # types.Message instead of Message
    """Импорт данных из JSON-файла в базу данных. Только для администраторов."""
    logger.info("4 Получена команда /import_db")

    if not message.document:
        await message.answer("❌ Пожалуйста, отправьте JSON-файл с данными.", parse_mode=None)
        return

    try:
        # Скачиваем файл
        file = await bot.get_file(message.document.file_id)
        file_path = file.file_path
        downloaded_file = await bot.download_file(file_path)

        # Читаем данные из файла
        import_data = json.loads(downloaded_file.read().decode("utf-8"))

        async with aiosqlite.connect(DB_FILE) as conn:
            # Очистка существующих данных (опционально)
            tables = ["users", "courses", "course_versions", "user_courses", "group_messages",
                      "course_activation_codes", "user_actions_log", "course_reviews",  # ДОБАВЛЕНО
                      "homework_gallery", "admin_context", "user_states"]  # Добавил остальные из вашего init_db
            for table in tables:
                await conn.execute(f"DELETE FROM {table}")

            # Импорт данных в таблицы
            for table, rows in import_data.items():
                if rows:
                    columns = rows[0].keys()
                    placeholders = ", ".join(["?"] * len(columns))
                    query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
                    await conn.executemany(query, [tuple(row.values()) for row in rows])

            await conn.commit()

        await message.answer("✅ База данных успешно импортирована из JSON.", parse_mode=None)
        logger.info("База данных успешно импортирована.")
    except Exception as e:
        logger.error(f"Ошибка при импорте базы данных: {e}")
        await message.answer("❌ Произошла ошибка при импорте базы данных.", parse_mode=None)




async def update_settings_file():
    """Обновляет файл settings.json с информацией о курсах."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT course_id, group_id FROM courses")
            courses = await cursor.fetchall()

            settings = {
                "message_interval": 24,
                "groups": {group_id: course_id for course_id, group_id in courses},
                "activation_codes": {}
            }

            cursor = await conn.execute("SELECT code_word, course_id, course_type FROM course_activation_codes")
            activation_codes = await cursor.fetchall()
            for code_word, course_id, course_type in activation_codes:
                settings["activation_codes"][code_word] = f"{course_id}:{course_type}"

            with open("settings.json", "w") as f:
                json.dump(settings, f, indent=4)

            logger.info("Файл settings.json обновлен.")

    except Exception as e:
        logger.error(f"Ошибка при обновлении файла settings.json: {e}")



# ===============================  команды ИИ для работы с ДЗ  ===============================================================
# Вспомогательная функция для извлечения данных из сообщения по ID
async def get_homework_context_by_message_id(admin_group_message_id: int) -> tuple | None:
    try:
        # Пытаемся получить сообщение из админ-группы
        message = await bot.edit_message_reply_markup(chat_id=ADMIN_GROUP_ID, message_id=admin_group_message_id,
                                                      reply_markup=None)  # Пробное редактирование, чтобы убедиться, что сообщение существует и это сообщение бота
        # await bot.edit_message_reply_markup(chat_id=ADMIN_GROUP_ID_CONF, message_id=admin_group_message_id, reply_markup=message.reply_markup) # Возвращаем клавиатуру, если нужно

        if message and message.reply_markup and message.reply_markup.inline_keyboard:
            for row in message.reply_markup.inline_keyboard:
                for button in row:
                    if button.callback_data:
                        try:
                            cb_data = AdminHomeworkCallback.unpack(button.callback_data)
                            return cb_data.user_id, cb_data.course_id, cb_data.lesson_num
                        except:
                            continue
        return None
    except Exception as e:
        logger.error(f"Ошибка при получении контекста ДЗ по message_id {admin_group_message_id}: {e}")
        return None


async def extract_homework_context_from_reply(message: types.Message) -> tuple | None:
    """
    Пытается извлечь контекст ДЗ (user_id, course_numeric_id, lesson_num, original_message_id)
    из сообщения, на которое ответил админ/ИИ.
    """
    if not message.reply_to_message:
        return None

    original_bot_message = message.reply_to_message

    if original_bot_message.reply_markup and original_bot_message.reply_markup.inline_keyboard:
        for row in original_bot_message.reply_markup.inline_keyboard:
            for button in row:
                if button.callback_data:
                    try:
                        cb_data = AdminHomeworkCallback.unpack(button.callback_data)
                        # Возвращаем user_id, course_id (числовой), lesson_num, и ID сообщения бота с ДЗ
                        return cb_data.user_id, cb_data.course_id, cb_data.lesson_num, original_bot_message.message_id
                    except Exception as e:
                        logger.debug(
                            f"Не удалось распаковать callback_data из кнопки в extract_homework_context_from_reply: {e}")
                        continue
    logger.warning("Не удалось извлечь контекст ДЗ из ответного сообщения (нет подходящих callback_data).")
    return None


async def get_homework_context_by_message_id(admin_group_message_id: int) -> tuple | None:
    """
    Пытается извлечь контекст ДЗ (user_id, course_numeric_id, lesson_num)
    из сообщения в админ-группе по его ID, анализируя callback_data кнопок.
    """
    try:
        # Получаем сообщение из админ-группы.
        # bot.edit_message_reply_markup(..., reply_markup=None) вернет объект Message, если успешно.
        # Это немного хак, чтобы просто получить объект сообщения, не меняя его видимого состояния надолго.
        # Более прямой способ - если бы был метод get_message_by_id, но его нет в чистом виде для бота.
        # Однако, если сообщение не имеет клавиатуры или если мы не хотим ее трогать, этот метод не идеален.
        # Проще будет, если ИИ передаст все нужные ID в аргументах команды.
        # Но если мы хотим сделать команду /approve <message_id> более универсальной для админов:

        # Временное решение для получения reply_markup без его изменения:
        # К сожалению, нет прямого метода "get_message_reply_markup".
        # Этот подход с edit_message_reply_markup(None) и потом восстановлением - рискованный.
        # Лучше, если ИИ передает все данные, или мы храним связь message_id с контекстом ДЗ в БД/кэше.

        # Давайте упростим: эта функция будет работать, если ИИ сможет предоставить все данные.
        # Для админа, отвечающего на сообщение, extract_homework_context_from_reply более подходит.
        # Если же админ использует /approve <message_id>, то ему проще указать все данные.

        # Пока оставим эту функцию как заглушку или для будущего, если найдем способ безопасно получить сообщение
        # и его reply_markup по ID без его изменения.
        logger.warning(
            f"Функция get_homework_context_by_message_id ({admin_group_message_id}) пока не реализована надежно для извлечения callback_data.")
        return None

        # Если бы у нас был доступ к объекту Message по ID:
        # if target_message and target_message.reply_markup and target_message.reply_markup.inline_keyboard:
        #     for row in target_message.reply_markup.inline_keyboard:
        #         for button in row:
        #             if button.callback_data:
        #                 try:
        #                     cb_data = AdminHomeworkCallback.unpack(button.callback_data)
        #                     return cb_data.user_id, cb_data.course_id, cb_data.lesson_num
        #                 except:
        #                     continue
        # return None
    except Exception as e:
        logger.error(f"Ошибка при получении контекста ДЗ по message_id {admin_group_message_id}: {e}")
        return None


# Вспомогательная функция для извлечения данных из callback_data кнопок сообщения
def get_context_from_admin_message_markup(message_with_buttons: types.Message) -> tuple | None:
    if message_with_buttons and message_with_buttons.reply_markup and message_with_buttons.reply_markup.inline_keyboard:
        for row in message_with_buttons.reply_markup.inline_keyboard:
            for button in row:
                if button.callback_data:
                    try:
                        # Предполагаем, что хотя бы одна кнопка содержит AdminHomeworkCallback
                        # и все нужные данные (user_id, course_id, lesson_num) там одинаковы
                        cb_data = AdminHomeworkCallback.unpack(button.callback_data)
                        return cb_data.user_id, cb_data.course_id, cb_data.lesson_num
                    except Exception:
                        continue  # Пробуем следующую кнопку
    return None


async def process_homework_command(
        message: types.Message,
        command_args: str | None,
        is_approval: bool
):
    """Общая логика для обработки команд /approve и /reject."""
    admin_id = message.from_user.id

    user_id_student = None
    course_numeric_id_hw = None
    lesson_num_hw = None
    feedback_text_hw = ""
    original_bot_message_id_in_admin_group = None  # ID сообщения, которое нужно будет изменить/удалить

    # Сценарий 1: Команда дана в ответ на сообщение бота с ДЗ
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot.id:
        original_bot_message_in_admin_group = message.reply_to_message
        context_from_reply_markup = get_context_from_admin_message_markup(original_bot_message_in_admin_group)
        if context_from_reply_markup:
            user_id_student, course_numeric_id_hw, lesson_num_hw = context_from_reply_markup
            original_bot_message_id_in_admin_group = original_bot_message_in_admin_group.message_id
            feedback_text_hw = command_args if command_args else \
                ("Домашнее задание требует доработки." if not is_approval else "")  # Дефолтный фидбэк
            logger.info(
                f"Команда ({'/approve' if is_approval else '/reject'}) по REPLY от {admin_id}: user={user_id_student}, c_id={course_numeric_id_hw}, l_num={lesson_num_hw}")

    # Сценарий 2: Команда с аргументами (для ИИ или прямого вызова админом)
    # /cmd <user_id_студента> <course_numeric_id> <lesson_num> [комментарий/причина]
    if not user_id_student and command_args:  # Если контекст не извлечен из reply и есть аргументы
        args = command_args.split(maxsplit=3)  # user_id, course_id, lesson_num, остальное - текст
        if len(args) >= 3:
            try:
                user_id_student = int(args[0])
                course_numeric_id_hw = int(args[1])
                lesson_num_hw = int(args[2])
                feedback_text_hw = args[3] if len(args) > 3 else \
                    ("Домашнее задание требует доработки." if not is_approval else "")
                # В этом сценарии мы не знаем original_bot_message_id_in_admin_group, если только ИИ его не передаст
                # как дополнительный аргумент, что усложнит команду. Пока оставляем None.
                logger.info(
                    f"Команда ({'/approve' if is_approval else '/reject'}) по АРГУМЕНТАМ от {admin_id}: user={user_id_student}, c_id={course_numeric_id_hw}, l_num={lesson_num_hw}")
            except (ValueError, IndexError):
                user_id_student = None  # Сбрасываем, если парсинг аргументов не удался

    if user_id_student and course_numeric_id_hw is not None and lesson_num_hw is not None:
        course_id_str = await get_course_id_str(course_numeric_id_hw)
        await handle_homework_result(
            user_id=user_id_student, course_id=course_id_str, course_numeric_id=course_numeric_id_hw,
            lesson_num=lesson_num_hw, admin_id=admin_id, feedback_text=feedback_text_hw,
            is_approved=is_approval, callback_query=None,
            original_admin_message_id_to_delete=original_bot_message_id_in_admin_group
        )
        action_verb = "одобрено" if is_approval else "отклонено"
        await message.reply(f"✅ ДЗ для user {user_id_student} было {action_verb} командой.")
    else:
        cmd_name = "approve" if is_approval else "reject"
        await message.reply(
            f"Не удалось определить ДЗ для команды `/{cmd_name}`.\n"
            f"Используйте: `/{cmd_name} [комментарий]` в ответ на сообщение с ДЗ\n"
            f"Или: `/{cmd_name} <user_id> <course_num_id> <lesson_num> [комментарий]`",
            parse_mode=ParseMode.MARKDOWN_V2
        )


@dp.message(Command("approve"), F.chat.id == ADMIN_GROUP_ID)  # Используем вашу переменную ADMIN_GROUP_ID
async def cmd_approve_homework_handler(message: types.Message, command: CommandObject):
    await process_homework_command(message, command.args, is_approval=True)


@dp.message(Command("reject"), F.chat.id == ADMIN_GROUP_ID)  # Используем вашу переменную ADMIN_GROUP_ID
async def cmd_reject_homework_handler(message: types.Message, command: CommandObject):
    await process_homework_command(message, command.args, is_approval=False)

# Модифицируем cmd_approve_homework и cmd_reject_homework
@dp.message(Command("approve"), F.chat.id == ADMIN_GROUP_ID)
async def old_cmd_approve_homework(message: types.Message, command: CommandObject, state: FSMContext):
    admin_id = message.from_user.id
    args_str = command.args if command.args else ""

    user_id = None
    course_numeric_id = None
    lesson_num = None
    feedback_text = ""
    original_message_id_to_process = None

    # Сценарий 1: Команда дана в ответ на сообщение бота с ДЗ
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot.id:
        context_from_reply = await extract_homework_context_from_reply(message)
        if context_from_reply:
            user_id, course_numeric_id, lesson_num, original_message_id_to_process = context_from_reply
            feedback_text = args_str  # Весь args - это фидбэк
            logger.info(f"/approve по reply: user={user_id}, c_id={course_numeric_id}, l_num={lesson_num}")

    # Сценарий 2: Команда с аргументами (для ИИ или прямого вызова админом)
    # /approve <user_id_студента> <course_numeric_id> <lesson_num> [комментарий]
    # ИЛИ /approve <original_bot_message_id> [комментарий]
    if not user_id:  # Если контекст не извлечен из reply
        args = args_str.split(maxsplit=3)  # Разделяем на 3 или 4 части
        if len(args) >= 1 and args[0].isdigit():
            # Пытаемся сначала как /approve <original_bot_message_id> [комментарий]
            try:
                temp_msg_id = int(args[0])
                context_from_msg_id = await get_homework_context_by_message_id(temp_msg_id)
                if context_from_msg_id:
                    user_id, course_numeric_id, lesson_num = context_from_msg_id
                    original_message_id_to_process = temp_msg_id
                    feedback_text = args[1] if len(args) > 1 else ""
                    logger.info(f"/approve по msg_id: user={user_id}, c_id={course_numeric_id}, l_num={lesson_num}")
            except ValueError:  # Первый аргумент не число, значит это не msg_id
                pass

        if not user_id and len(
                args) >= 3:  # Если все еще не нашли, пытаемся как /approve <user_id> <course_id> <lesson_num>
            try:
                user_id = int(args[0])
                course_numeric_id = int(args[1])
                lesson_num = int(args[2])
                feedback_text = args[3] if len(args) > 3 else ""
                # В этом случае original_message_id_to_process остается None, если только ИИ не передаст его отдельно
                logger.info(f"/approve по аргументам: user={user_id}, c_id={course_numeric_id}, l_num={lesson_num}")
            except (ValueError, IndexError):
                user_id = None  # Сбрасываем, если парсинг не удался

    if user_id and course_numeric_id is not None and lesson_num is not None:
        course_id_str = await get_course_id_str(course_numeric_id)
        await handle_homework_result(
            user_id=user_id, course_id=course_id_str, course_numeric_id=course_numeric_id,
            lesson_num=lesson_num, admin_id=admin_id, feedback_text=feedback_text,
            is_approved=True, callback_query=None,
            original_admin_message_id_to_delete=original_message_id_to_process
        )
        await message.reply("✅ ДЗ одобрено командой.")
    else:
        await message.reply(
            "Не удалось определить ДЗ для одобрения.\n"
            "Используйте: `/approve [комментарий]` в ответ на сообщение с ДЗ\n"
            "Или: `/approve <id_сообщения_с_ДЗ> [комментарий]`\n"
            "Или: `/approve <user_id> <course_num_id> <lesson_num> [комментарий]`"
        )

# Аналогично для cmd_reject_homework
@dp.message(Command("reject"), F.chat.id == ADMIN_GROUP_ID)
async def old_cmd_reject_homework(message: types.Message, command: CommandObject, state: FSMContext):
    admin_id = message.from_user.id
    args_str = command.args if command.args else ""

    user_id = None
    course_numeric_id = None
    lesson_num = None
    feedback_text = ""
    original_message_id_to_process = None

    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot.id:
        context_from_reply = await extract_homework_context_from_reply(message)
        if context_from_reply:
            user_id, course_numeric_id, lesson_num, original_message_id_to_process = context_from_reply
            feedback_text = args_str if args_str else "Домашнее задание требует доработки."
            logger.info(f"/reject по reply: user={user_id}, c_id={course_numeric_id}, l_num={lesson_num}")

    if not user_id:
        args = args_str.split(maxsplit=3)
        if len(args) >= 1 and args[0].isdigit():
            try:
                temp_msg_id = int(args[0])
                context_from_msg_id = await get_homework_context_by_message_id(temp_msg_id)
                if context_from_msg_id:
                    user_id, course_numeric_id, lesson_num = context_from_msg_id
                    original_message_id_to_process = temp_msg_id
                    feedback_text = args[1] if len(args) > 1 else "Домашнее задание требует доработки."
                    logger.info(f"/reject по msg_id: user={user_id}, c_id={course_numeric_id}, l_num={lesson_num}")
            except ValueError:
                pass

        if not user_id and len(args) >= 3:
            try:
                user_id = int(args[0])
                course_numeric_id = int(args[1])
                lesson_num = int(args[2])
                feedback_text = args[3] if len(args) > 3 else "Домашнее задание требует доработки."
                logger.info(f"/reject по аргументам: user={user_id}, c_id={course_numeric_id}, l_num={lesson_num}")
            except (ValueError, IndexError):
                user_id = None

    if user_id and course_numeric_id is not None and lesson_num is not None:
        course_id_str = await get_course_id_str(course_numeric_id)
        await handle_homework_result(
            user_id=user_id, course_id=course_id_str, course_numeric_id=course_numeric_id,
            lesson_num=lesson_num, admin_id=admin_id, feedback_text=feedback_text,
            is_approved=False, callback_query=None,
            original_admin_message_id_to_delete=original_message_id_to_process
        )
        await message.reply("❌ ДЗ отклонено командой.")
    else:
        await message.reply(
            "Не удалось определить ДЗ для отклонения.\n"
            "Используйте: `/reject [причина]` в ответ на сообщение с ДЗ\n"
            "Или: `/reject <id_сообщения_с_ДЗ> [причина]`\n"
            "Или: `/reject <user_id> <course_num_id> <lesson_num> [причина]`"
        )


# Команды для взаимодействия с пользователем - в конце, аминь.
#=======================================================================================================================
@dp.callback_query(MainMenuAction.filter(F.action == "stop_course"))
async def cb_stop_current_course(query: types.CallbackQuery, callback_data: MainMenuAction, state: FSMContext):
    user_id = query.from_user.id
    course_numeric_id_to_stop = callback_data.course_id_numeric
    course_id_to_stop_str = await get_course_id_str(course_numeric_id_to_stop)

    logger.info(f"Пользователь {user_id} хочет остановить курс {course_id_to_stop_str} ({course_numeric_id_to_stop})")

    try:
        # Деактивируем курс (ставим статус 'inactive' или 'paused')
        # и останавливаем для него scheduled_task
        success, message_text = await deactivate_course(user_id, course_id_to_stop_str)

        await query.answer(escape_md(message_text), show_alert=True)

        if success:
            await query.message.edit_text(
                escape_md(f"Курс «{await get_course_title(course_id_to_stop_str)}» был остановлен.\n"
                          "Вы можете выбрать другой курс или активировать новый."),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            # Перенаправляем на выбор другого курса
            await cb_select_other_course(query, state)  # Переиспользуем существующий обработчик
        else:
            # Если деактивация не удалась, можно просто обновить меню или ничего не делать
            pass

    except Exception as e:
        logger.error(f"Ошибка при остановке курса {course_id_to_stop_str} для {user_id}: {e}")
        await query.answer("Не удалось остановить курс.", show_alert=True)


@dp.message(Command("timezone"))
async def cmd_set_timezone(message: types.Message):
    """Меню настройки часового пояса"""
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(
        text="📍 Определить автоматически",
        request_location=True
    ))
    builder.add(KeyboardButton(
        text="⌨️ Выбрать вручную",
    ))
    await message.answer(
        "Выберите способ определения часового пояса:",
        reply_markup=builder.as_markup(
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )

@dp.message(F.text == "⌨️ Выбрать вручную")
async def manual_timezone_selection(message: types.Message):
    """Предоставляет список часовых поясов для выбора вручную"""
    builder = InlineKeyboardBuilder()
    timezones = pytz.all_timezones
    for tz in timezones:
        builder.add(InlineKeyboardButton(
            text=tz,
            callback_data=f"set_tz_manual:{tz}"
        ))
    builder.adjust(1)  # Одна колонка для удобства просмотра
    await message.answer(
        "Выберите часовой пояс из списка:",
        reply_markup=builder.as_markup()
    )

@dp.callback_query(lambda c: c.data.startswith("set_tz_manual:"))
async def process_manual_timezone(callback: types.CallbackQuery):
    """Обработчик выбора часового пояса вручную"""
    user_id = callback.from_user.id
    timezone_name = callback.data.split(":")[1]

    logger.info(f"Пользователь {user_id} выбрал часовой пояс вручную: {timezone_name}")

    if not is_valid_timezone(timezone_name):
        await callback.answer("Ошибка: Неверный формат часового пояса.", show_alert=True)
        return

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                UPDATE users SET timezone = ? WHERE user_id = ?
            """, (timezone_name, user_id))
            await conn.commit()

        await callback.message.edit_text(  # Редактируем исходное сообщение
            f"Ваш часовой пояс установлен на: `{timezone_name}`",
            parse_mode="MarkdownV2",
            reply_markup=None  # Убираем клавиатуру
        )
        await callback.answer("Часовой пояс сохранен!")
    except Exception as e:
        logger.error(f"Ошибка сохранения часового пояса {timezone_name} для {user_id}: {e}")
        await callback.answer("Не удалось сохранить часовой пояс.", show_alert=True)

@dp.message(F.location)
async def handle_location(message: types.Message):
    """Обработка полученной геолокации"""
    user_id = message.from_user.id
    lat = message.location.latitude
    lng = message.location.longitude

    try:
        # Определяем часовой пояс по координатам




        # Сохраняем в БД (пример для SQLite)
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                UPDATE users SET timezone = ? WHERE user_id = ?
            """, (DEFAULT_TIMEZONE, user_id))
            await conn.commit()

        await message.answer(
            f"✅ Часовой пояс установлен: {DEFAULT_TIMEZONE}",
            reply_markup=types.ReplyKeyboardRemove()  # Убираем клавиатуру
        )

    except Exception as e:
        logger.error(f"Ошибка определения часового пояса: {e}")
        await message.answer(
            "⚠️ Не удалось определить часовой пояс. Используется Europe/Moscow",
            reply_markup=types.ReplyKeyboardRemove()
        )

def is_valid_timezone(tz: str) -> bool:
    """Проверяет, является ли строка допустимым часовым поясом"""
    try:
        pytz.timezone(tz)
        return True
    except pytz.exceptions.UnknownTimeZoneError:
        return False

# Обновленная функция получения времени
async def get_local_time(user_id: int) -> datetime:
    """Возвращает локальное время пользователя"""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT timezone FROM users WHERE user_id = ?
            """, (user_id,))
            result = await cursor.fetchone()
            tz_name = result[0] if result and result[0] else DEFAULT_TIMEZONE
            return datetime.now(pytz.timezone(tz_name))
    except Exception as e:
        logger.error(f"Ошибка при получении часового пояса: {e}")
        return datetime.now(pytz.timezone(DEFAULT_TIMEZONE))





@db_exception_handler
async def check_homework_pending(user_id: int, course_id: str, lesson_num: int) -> bool:
    """Проверяет, есть ли у пользователя непроверенное ДЗ по данному уроку."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT COUNT(*)
                FROM homework_gallery
                WHERE user_id = ? AND course_id = ? AND lesson_num = ? AND approved_by = 0
            """, (user_id, course_id, lesson_num))
            result = await cursor.fetchone()
            return result[0] > 0  # returns true if homework pending
    except Exception as e:
        logger.error(f"Error while checking homework status: {e}")
        return False



@dp.callback_query(F.data.startswith("support_eval:"))
async def process_support_evaluation(callback: types.CallbackQuery):
    """Обрабатывает оценку пользователя после обращения в поддержку."""
    try:
        user_id = callback.from_user.id
        evaluation = callback.data.split(":")[1]  # Извлекаем оценку (1-5)
        message_id = callback.message.message_id
        logger.info(f"Получена оценка {evaluation=} от {user_id=}")

        # Сохраняем оценку в базе данных (пример)
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                INSERT INTO support_evaluations (user_id, message_id, evaluation, timestamp)
                VALUES (?, ?, ?, ?)
            """, (user_id, message_id, evaluation, datetime.now(pytz.utc)))
            await conn.commit()

        # Подтверждение пользователю
        await callback.answer(f"Спасибо за вашу оценку ({evaluation})!", show_alert=True)

        # Отправляем оценку администраторам (опционально)
        if ADMIN_GROUP_ID:
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"Пользователь {callback.from_user.full_name} (ID: {user_id}) оценил поддержку на {evaluation}."
            )
    except Exception as e:
        logger.error(f"Ошибка при обработке оценки поддержки: {e}")
        await callback.answer("Произошла ошибка при обработке вашей оценки.", show_alert=True)


async def check_state(message: types.Message, state: FSMContext) -> bool:
    current_state = await state.get_state()
    logger.info(f"check_state {current_state}")
    if current_state == SupportRequest.waiting_for_response:
        return False  # Пропускаем, если админ ждёт ответа для support
    return True



# добавлено 24-04
@dp.message(SupportRequest.waiting_for_response, F.chat.type == "private")
async def process_support_response(message: types.Message, state: FSMContext):
    logger.info(f"process_support_response {message.from_user.id=}")
    admin_id = message.from_user.id
    data = await state.get_data()
    user_id = data.get("user_id")
    original_message_id = data.get("message_id")

    if not user_id:
        await message.answer("Не могу найти ID пользователя.")
        return

    try:
        escaped_response = escape_md(message.text)  # Экранируем текст
        await bot.send_message(
            chat_id=user_id,
            text=f"Ответ от поддержки:\n\n{escaped_response}", # тут надо экранировать
            parse_mode=ParseMode.MARKDOWN_V2
        )

        # Уведомляем админа об успешной отправке
        await message.answer("Сообщение пользователю отправлено.")
    except Exception as e:
        logger.error(f"Ошибка при отправке ответа пользователю: {e}", exc_info=True)
        await message.answer("Произошла ошибка при отправке сообщения пользователю.")

    await state.clear()



@dp.message(CommandStart())
@db_exception_handler
async def cmd_start(message: types.Message):
    """Обработчик команды /start."""
    logger.info(f"!!!!!!!!!! CMD_START ВЫЗВАН для пользователя {message.from_user.id} !!!!!!!!!!")
    user = message.from_user
    user_id = user.id
    first_name = user.first_name or "Пользователь"
    logger.info(f"cmd_start {user_id=}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверяем, есть ли пользователь в базе данных
            cursor = await conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            user_exists = await cursor.fetchone()
            logger.info(f"cmd_start: user_exists = {user_exists}")

            if not user_exists:
                # Добавляем нового пользователя в базу данных
                await conn.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name)
                    VALUES (?, ?, ?, ?)
                """, (user_id, user.username, user.first_name, user.last_name))
                await conn.commit()
                logger.info(f"New user added: {user_id}")

            # Получаем данные активного курса пользователя из user_courses
            cursor = await conn.execute("""
                SELECT 
                    uc.course_id,
                    uc.current_lesson,
                    uc.version_id,
                    c.title AS course_name,
                    cv.title AS version_name,
                    uc.status,
                    uc.hw_status
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                JOIN course_versions cv ON uc.course_id = cv.course_id AND uc.version_id = cv.version_id
                WHERE uc.user_id = ? AND uc.status = 'active'
            """, (user_id,))
            current_course = await cursor.fetchone()
            logger.info(f"cmd_start: current_course = {current_course}")

            # Если у пользователя нет активного курса, предлагаем активировать
            if not current_course:
                logger.info(f"cmd_start: No active course found for {user_id}, asking for activation code")
                await message.answer(escape_md("❌ Нет активных курсов. Активируйте курс через код"), parse_mode="MarkdownV2")

                try:
                    if not os.path.exists("ask_parol.jpg"):
                        raise FileNotFoundError("Файл ask_parol.jpg не найден")

                    # InputFile должен принимать путь к файлу, а не открытый файл
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=types.FSInputFile("ask_parol.jpg")  # Используем FSInputFile для файловой системы
                    )
                except FileNotFoundError as fnf_error:
                    logger.error(f"Файл не найден: {fnf_error}")
                    await message.answer("⚠️ Произошла ошибка при отправке фотографии.", parse_mode=None)
                except Exception as e:
                    logger.error(f"Ошибка при отправке фото: {e}", exc_info=True)
                    await message.answer("⚠️ Произошла ошибка при отправке фотографии.", parse_mode=None)

                return

            # Распаковываем данные активного курса
            course_id, lesson_num, version_id, course_name, version_name, status, hw_status = current_course
            course_numeric_id = await get_course_id_int(course_id) if course_id else None
            logger.info(
                f"cmd_start: active course - {course_id=}, {lesson_num=}, {version_id=}, {course_name=}, {version_name=}")

            # Получаем статистику по всем курсам пользователя (активные и завершенные)
            cursor = await conn.execute("""
                SELECT 
                    uc.course_id,
                    uc.status
                FROM user_courses uc
                WHERE uc.user_id = ?
            """, (user_id,))
            all_courses = await cursor.fetchall()

            active_courses = [c for c in all_courses if c[1] == 'active']
            completed_courses = [c for c in all_courses if c[1] == 'completed']

            # Получаем общее количество уроков
            cursor = await conn.execute("""
                SELECT 
                    MAX(gm.lesson_num)
                FROM group_messages gm
                WHERE gm.course_id = ?
            """, (course_id,))
            progress_data = await cursor.fetchone()
            total_lessons = progress_data[0] if progress_data and progress_data[
                0] is not None else 1  # Default to 1 if no lessons found

            # Get tariff names from settings
            tariff_names = settings.get("tariff_names", {
                "v1": "Соло",
                "v2": "Группа",
                "v3": "VIP"
            })
            tariff_name = tariff_names.get(version_id, "Базовый")

            # Общее количество курсов для кнопки "Мои курсы"
            total_courses = len(completed_courses) + len(active_courses)
            courses_button_text = f"📚 Мои курсы ({total_courses})"

            logger.info(f"Старт задачи для шедулера для {user_id=}")
            await start_lesson_schedule_task(user_id)
            # Генерация клавиатуры
            # пока выключим - вроде ненад todo разобраться
            #homework_pending = await check_homework_pending(user_id, course_id, lesson_num)
            logger.info(f"перед созданием клавиатуры {course_numeric_id=}")
            keyboard = get_main_menu_inline_keyboard(  # await убрали
                course_numeric_id = course_numeric_id,
                lesson_num=lesson_num,
                user_tariff=version_id,
                homework_pending=True if hw_status != 'approved' and hw_status != 'not_required' else False
            )

            welcome_message = (
                f"*С возвращением*, {escape_md(first_name)}\\!\n\n"
                f"🎓 Курс: {escape_md(course_name)}\n"
                f"🔑 Тариф: {escape_md(tariff_name)}\n"
                f"📚 Текущий урок: {lesson_num}"
            )
            logger.info(f"{welcome_message=}")
            await message.answer(welcome_message, reply_markup=keyboard, parse_mode="MarkdownV2")


    except Exception as e:
        logger.error(f"Error in cmd_start: {e}", exc_info=True)
        await message.answer("Произошла ошибка при обработке команды. Пожалуйста, попробуйте позже.", parse_mode=None)


async def send_course_description(user_id: int, course_id: str):
    """Отправляет описание курса пользователю."""
    logger.info(f"send_course_description {user_id=} {course_id=}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT text
                FROM group_messages
                WHERE course_id = ? AND lesson_num = 0
            """, (course_id,))
            description = await cursor.fetchone()
            logger.info(f"Описание курса description {course_id=} = {len(description)}")
            if description:
                await bot.send_message(user_id, description[0], parse_mode=None)
            else:
                await bot.send_message(user_id, "Описание курса не найдено.", parse_mode=None)

    except Exception as e:
        logger.error(f"Error sending course description: {e}")
        await bot.send_message(user_id, "Ошибка при получении описания курса. Или этот курс секретный и тогда вы знаете что делать!", parse_mode=None)



# help
@dp.message(Command("help"))
async def cmd_help(message: Message):
    logger.info(f"cmd_help  ")
    help_text = (
        "🤖 *Команды бота:*\n\n"
        "📚 *Основные команды:*\n"
        "/start - Начать работу с ботом\n"
        "/help - Показать это сообщение\n"
        "/activate - Активировать курс по коду\n"
        "/mycourses - Показать мои курсы\n"
        "/lesson - Получить текущий урок\n"
        "/progress - Показать мой прогресс\n"
        "/tokens - Показать баланс токенов\n\n"

        "📝 *Домашние задания:*\n"
        "/homework - Отправить домашнее задание\n"
        "/status - Статус проверки ДЗ\n\n"

        "🔔 *Другое:*\n"
        "/support - Связаться с поддержкой\n"
        "/profile - Мой профиль\n"
        "/referral - Реферальная программа"
    )

    await message.answer(escape_md(help_text), parse_mode="MarkdownV2")


# --- Вспомогательные функции ---

def escape_markdown_v2(text: str) -> str:
    """Экранирует специальные символы для MarkdownV2."""
    # Список символов для экранирования в MarkdownV2 согласно документации Telegram
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    # Создаем регулярное выражение для поиска любого из этих символов
    # и заменяем его на экранированную версию (с \ перед символом)
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', str(text))

def get_lesson_plural(n):
    """Возвращает правильную форму слова 'урок' для числа n."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return "урок"
    elif n % 10 in [2, 3, 4] and n % 100 not in [12, 13, 14]:
        return "урока"
    else:
        return "уроков"

def get_course_plural(n):
    """Возвращает правильную форму слова 'курс' для числа n."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return "курс"
    elif n % 10 in [2, 3, 4] and n % 100 not in [12, 13, 14]:
        return "курса"
    else:
        return "курсов"


# 17-04
@dp.callback_query(F.data == "menu_mycourses")  # Предоставляет кнопки для продолжения или повторного просмотра
@db_exception_handler  # Показывает список активных и завершенных курсов
async def cmd_mycourses_callback(query: types.CallbackQuery):
    """Показывает список активных и завершенных курсов."""
    user_id = query.from_user.id
    logger.info(f"12 cmd_mycourses_callback  user_id={user_id}  query={query}   ")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем активные курсы
            cursor = await conn.execute("""
                SELECT c.title, uc.course_id, uc.version_id, uc.current_lesson, c.id
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ? AND uc.status = 'active'
            """, (user_id,))
            active_courses = await cursor.fetchall()

            # Получаем завершенные курсы
            cursor = await conn.execute("""
                SELECT c.title, uc.course_id, uc.version_id, c.id
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ? AND uc.status = 'completed'
            """, (user_id,))
            completed_courses = await cursor.fetchall()

            # Получаем существующие вообще
            cursor = await conn.execute("""
                SELECT COUNT(*) AS total_courses FROM courses;
            """, )
            count_courses = (await cursor.fetchone())[0]

            # Получаем подробно про каждый курс
            cursor = await conn.execute("""
                SELECT c.title AS course_title, COUNT(DISTINCT gm.lesson_num) AS number_of_lessons
                FROM courses c
                LEFT JOIN group_messages gm ON c.course_id = gm.course_id AND gm.lesson_num > 0 -- Присоединяем только уроки с номером > 0
                GROUP BY c.course_id, c.title -- Группируем по ID и названию курса для получения уникальных курсов
                ORDER BY c.title; -- Опционально: сортируем по названию курса
            """, )
            detail_courses = await cursor.fetchall()

        logger.debug(f"cmd_mycourses: {count_courses=}, {detail_courses=}")

        # --- Формирование текста сообщения ---
        if not detail_courses:
            message_text = escape_markdown_v2("ℹ️ У вас пока нет доступных курсов или информация о них загружается.")
        else:
            header = "*📊 Информация о курсах:*"
            course_lines = []
            for title, lesson_count in detail_courses:
                escaped_title = escape_markdown_v2(title) # названий
                lesson_word = get_lesson_plural(lesson_count) # уроков
                escaped_lesson_word = escape_markdown_v2(lesson_word) # уроков после экрана маркдауна

                # Форматируем строку: пункт списка, _курсив_ для названия, количество и слово "урок"
                line = f"\\- _{escaped_title}_ \\- *{lesson_count}* {escaped_lesson_word}"
                course_lines.append(line)

            courses_list_str = "\n".join(course_lines)

            total_count_word = get_course_plural(count_courses) # уроков
            escaped_total_word = escape_markdown_v2(total_count_word)  #урокоа маркдаун
            # Используем \ для экранирования точки в конце
            total_line = escape_markdown_v2(
                f"🌍 Всего в системе: {count_courses} ") + escaped_total_word + escape_markdown_v2(".")

            message_text = f"{header}\n\n{courses_list_str}\n\n{total_line}"

        logger.debug(f"cmd_mycourses: {message_text=}")
        # Отправка сообщения
        await bot.send_message(
            user_id,
            message_text,  # Используем сформированный и экранированный текст
            parse_mode="MarkdownV2"  # Указываем режим парсинга
        )

        # Формируем текст ответа с кнопками
        response_text = ""
        if active_courses:
            response_text += "Активные курсы:\n"
            response_text += "\n".join([f"- {title}" for title, course_id, version_id, current_lesson, id in active_courses]) + "\n\n"
        if completed_courses:
            response_text += "Завершенные курсы:\n"
            response_text += "\n".join([f"- {title}" for title, course_id, version_id, id in completed_courses])

        if not active_courses and not completed_courses:
            response_text = "У вас нет активных или завершенных курсов."

        # Проверяем, есть ли активные курсы, чтобы взять данные для меню
        if active_courses:
            # Берем данные из первого активного курса для примера
            title, course_id, version_id, lesson_num, id = active_courses[0]
        else:
            # Если нет активных курсов, задаем значения по умолчанию или None
            id = None
            lesson_num = 0
            version_id = None

        # Создаем кнопки меню
        keyboard = get_main_menu_inline_keyboard(
            course_numeric_id=id,  # Определите course_id
            lesson_num=lesson_num,  # Определите lesson_num
            user_tariff=version_id,  # Определите version_id
            homework_pending=False  # disable_button=True
        )

        # Отправляем сообщение с прогрессом
        await bot.send_message(
            user_id,
            response_text,
            reply_markup=keyboard,
            parse_mode=None
        )
        await query.answer("✅ Курсы")
    except Exception as e:
        logger.error(f"Error in cmd_mycourses: {e}")
        await query.answer("Произошла ошибка при обработке запроса.", show_alert=True)


# 11-04
@dp.callback_query(CourseCallback.filter(F.action == "menu_cur"))
@db_exception_handler
async def show_lesson_content(callback_query: types.CallbackQuery, callback_data: CourseCallback):
    """Отображает текущий урок с динамическим меню"""
    user_id = callback_query.from_user.id
    course_numeric_id = callback_data.course_id
    lesson_num = callback_data.lesson_num

    logger.info(f"show_lesson_content: Callback получен! user_id={user_id}, course_numeric_id={course_numeric_id}, lesson_num={lesson_num}")

    try:
        course_id = await get_course_id_str(course_numeric_id)
        # Вызываем send_lesson_to_user для отправки контента
        await send_lesson_to_user(user_id, course_id, lesson_num, repeat=True)
        logger.info(f"✅ Lesson sent successfully to {user_id} повторно")
        await callback_query.answer("✅ повторная отправка текущего урока – OK")

    except Exception as e:
        logger.error(f"Error in show_lesson_content: {e}")
        await callback_query.message.answer("Произошла ошибка при отображении урока.")


# НОВЫЙ обработчик для кнопки "Оставить отзыв" ПОСЛЕ ЗАВЕРШЕНИЯ КУРСА
@dp.callback_query(F.data == "leave_feedback")
async def cb_leave_course_review_start(query: types.CallbackQuery, state: FSMContext): # Переименовал для ясности
    user_id = query.from_user.id
    last_completed_course_id = None
    # ... (ваш код получения last_completed_course_id из БД) ...
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute(
                "SELECT course_id FROM user_courses WHERE user_id = ? AND status = 'completed' ORDER BY activation_date DESC LIMIT 1",
                (user_id,)
            )
            row = await cursor.fetchone()
            if row:
                last_completed_course_id = row[0]
    except Exception as e:
        logger.error(f"Ошибка при получении последнего завершенного курса для отзыва: {e}")

    if last_completed_course_id:
        course_title = await get_course_title(last_completed_course_id)
        prompt_text = f"Пожалуйста, напишите ваш отзыв о пройденном курсе «{escape_md(course_title)}»:"
        await state.update_data(course_id_for_review=last_completed_course_id) # Сохраняем ID курса
    else:
        prompt_text = "Пожалуйста, напишите ваш отзыв о пройденном курсе:"
        await state.update_data(course_id_for_review="неизвестный (не найден)")

    await query.message.edit_text(escape_md(prompt_text), parse_mode=ParseMode.MARKDOWN_V2)
    await state.set_state(CourseReviewForm.waiting_for_review_text) # Используем новое состояние
    await query.answer()

# НОВЫЙ обработчик для кнопки "Оставить отзыв" - ответ, собственно, юзера
@dp.message(CourseReviewForm.waiting_for_review_text)  # Ловим сообщения в новом состоянии
async def process_course_review_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    review_text_raw = message.text
    current_data = await state.get_data()
    course_id_for_review = current_data.get("course_id_for_review", "неизвестный курс")

    logger.info(f"Получен отзыв о курсе '{course_id_for_review}' от пользователя {user_id}: {review_text_raw}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Убедитесь, что таблица course_reviews существует
            # CREATE TABLE IF NOT EXISTS course_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, course_id TEXT, review_text TEXT, created_at TIMESTAMP);
            await conn.execute(
                "INSERT INTO course_reviews (user_id, course_id, review_text, created_at) VALUES (?, ?, ?, ?)",
                (user_id, course_id_for_review, review_text_raw, datetime.now(pytz.utc))
            )
            await conn.commit()
        await message.reply(escape_md("Спасибо за ваш отзыв! Мы ценим ваше мнение. 🎉"),
                            parse_mode=ParseMode.MARKDOWN_V2)

        if ADMIN_GROUP_ID:
            user_info = await bot.get_chat(user_id)
            user_details = user_info.full_name
            if user_info.username:
                user_details += f" (@{user_info.username})"

            admin_message = (
                f"📝 Новый отзыв о курсе\\!\n"
                f"👤 Пользователь: {escape_md(user_details)} ID: {user_id}\n"
                f"📚 Курс: {escape_md(str(course_id_for_review))}\n"
                f"💬 Отзыв:\n{escape_md(review_text_raw)}"
            )
            await bot.send_message(ADMIN_GROUP_ID, admin_message, parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        logger.error(f"Ошибка при сохранении/отправке отзыва о курсе: {e}")
        await message.reply(escape_md("Произошла ошибка при обработке вашего отзыва. Пожалуйста, попробуйте позже."),
                            parse_mode=ParseMode.MARKDOWN_V2)
    finally:
        await state.clear()



@dp.callback_query(F.data == "select_other_course")
async def cb_select_other_course(query: types.CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    logger.info(f"Пользователь {user_id} нажал 'Выбрать другой курс'")
    await query.answer()

    # try: Не удаляю
    #     await query.message.delete()  # Удаляем предыдущее сообщение с кнопками о завершении
    # except TelegramBadRequest:
    #     pass

    async with aiosqlite.connect(DB_FILE) as conn:
        # 1. Получаем все доступные курсы в системе с их ценами (из course_versions)
        # Предполагаем, что у каждого course_id есть хотя бы одна версия (тариф)
        # И что для покупки мы предлагаем, например, базовый тариф (или самый дешевый)
        # Для простоты, пока возьмем все уникальные course_id и их названия
        # А цены/тарифы будем показывать при выборе конкретного курса

        cursor_all_courses = await conn.execute(
            "SELECT c.course_id, c.title, cv.price, cv.version_id FROM courses c JOIN course_versions cv ON c.course_id = cv.course_id GROUP BY c.course_id ORDER BY c.title"
            # Можно добавить WHERE cv.version_id = 'базовый_тариф_id' если хотите предлагать конкретный
        )
        all_system_courses = await cursor_all_courses.fetchall()  # [(course_id, title, price, version_id), ...]

        # 2. Получаем курсы пользователя (активные и завершенные)
        cursor_user_courses = await conn.execute(
            "SELECT course_id, status FROM user_courses WHERE user_id = ?", (user_id,)
        )
        user_courses_raw = await cursor_user_courses.fetchall()
        user_courses_dict = {course_data[0]: course_data[1] for course_data in
                             user_courses_raw}  # {'course_id': 'status'}

    if not all_system_courses:
        await query.message.edit_text(escape_md("К сожалению, сейчас нет доступных курсов для выбора."),
                                      parse_mode=ParseMode.MARKDOWN_V2, reply_markup=None)
        return

    builder = InlineKeyboardBuilder()
    message_text = "Выберите курс или действие:\n\n"

    for course_id_str, title, price, version_id in all_system_courses:
        course_title_safe = escape_md(title)
        status = user_courses_dict.get(course_id_str)

        if status == 'completed':
            message_text += f"🎓 _{course_title_safe}_ \\(пройден\\)\n"
            builder.button(
                text=f"🔁 Повторить: {course_title_safe}",
                callback_data=RestartCourseCallback(course_id_str=course_id_str).pack()
            )
        elif status == 'active':
            message_text += f"▶️ _{course_title_safe}_ \\(активен\\)\n"
            # Можно добавить кнопку "Продолжить", если есть такая логика (переход к текущему уроку)
            # builder.button(text=f"Продолжить: {course_title_safe}", callback_data=CourseCallback(action="menu_cur", course_id=await get_course_id_int(course_id_str), lesson_num=... ).pack())
        else:  # Курс не активирован у пользователя
            price_str = f"{price} руб." if price > 0 else "Бесплатно"  # или "За звезды"
            message_text += f"✨ _{course_title_safe}_ {escape_md(price_str)}\n"
            builder.button(
                text=f"Купить: {course_title_safe} {price_str}",
                callback_data=BuyCourseCallback(course_id_str=course_id_str).pack()
            )
        logger.info(f"Добавлена кнопка для курса {course_id_str}: {title} ({status})")
        builder.row()  # Каждая группа кнопок для курса на новой строке (или adjust)

    # Добавим кнопку "Вернуться в главное меню", если у пользователя есть активный курс
    async with aiosqlite.connect(DB_FILE) as conn:
        active_course_data = await (await conn.execute(
            """SELECT c.id, uc.current_lesson, uc.version_id 
               FROM user_courses uc JOIN courses c ON uc.course_id = c.course_id
               WHERE uc.user_id = ? AND uc.status = 'active' LIMIT 1""", (user_id,))).fetchone()

    if active_course_data:
        course_numeric_id, lesson_num, version_id = active_course_data
        builder.row(InlineKeyboardButton(
            text="⬅️ В меню активного курса",
            # Этот callback должен вести к отображению главного меню для этого курса
            # Например, можно создать новый callback или использовать существующий, если он подходит
            # Пока сделаем заглушку для callback_data
            callback_data=CourseCallback(action="show_main_menu_for_active", course_id=course_numeric_id,
                                         lesson_num=lesson_num).pack()
        ))

    builder.adjust(1)  # По одной кнопке в строке для наглядности

    try:
        await query.message.edit_text(  # Редактируем существующее сообщение
            text=escape_md(message_text),
            reply_markup=builder.as_markup(),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except TelegramBadRequest as e:  # Если сообщение не изменилось или другая ошибка
        logger.warning(f"Не удалось отредактировать сообщение для списка курсов: {e}. Отправляю новое.")
        await bot.send_message(
            chat_id=user_id,
            text=escape_md(message_text),
            reply_markup=builder.as_markup(),
            parse_mode=ParseMode.MARKDOWN_V2
        )

#  Обработчик для RestartCourseCallback:
@dp.callback_query(RestartCourseCallback.filter())
async def cb_restart_or_next_level_course(query: types.CallbackQuery, callback_data: RestartCourseCallback,
                                          state: FSMContext):
    user_id = query.from_user.id
    course_id_to_process = callback_data.course_id_str
    action = callback_data.action

    logger.info(f"Пользователь {user_id} выбрал действие '{action}' для курса {course_id_to_process}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor_current_info = await conn.execute(
                "SELECT version_id, level FROM user_courses WHERE user_id = ? AND course_id = ?",
                (user_id, course_id_to_process)
            )
            current_info = await cursor_current_info.fetchone()
            if not current_info:
                await query.answer("Не удалось найти информацию о вашем курсе.", show_alert=True)
                return

            version_id, current_user_level_db = current_info
            new_level_for_user = current_user_level_db

            if action == "next_level":
                new_level_for_user = current_user_level_db + 1
                # Дополнительно можно проверить, существует ли вообще контент для new_level_for_user,
                # хотя кнопка должна была появиться только если он есть.
                cursor_check_level = await conn.execute(
                    "SELECT 1 FROM group_messages WHERE course_id = ? AND level = ? LIMIT 1",
                    (course_id_to_process, new_level_for_user)
                )
                if not await cursor_check_level.fetchone():
                    await query.answer(f"Контент для {new_level_for_user}-го уровня пока не готов.", show_alert=True)
                    return
                log_details = f"Переход на уровень {new_level_for_user}"
                user_message_feedback = f"Вы перешли на {new_level_for_user}-й уровень курса '{escape_md(await get_course_title(course_id_to_process))}'. Уроки начнутся заново."
            elif action == "restart_current_level":
                # new_level_for_user остается current_user_level_db
                log_details = f"Повторное прохождение уровня {current_user_level_db}"
                user_message_feedback = f"Прогресс по текущему уровню ({current_user_level_db}) курса '{escape_md(await get_course_title(course_id_to_process))}' сброшен. Уроки начнутся заново."
            else:
                await query.answer("Неизвестное действие.", show_alert=True)
                return

            now_utc_str = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
            await conn.execute(
                """UPDATE user_courses 
                   SET current_lesson = 0, hw_status = 'none', hw_type = NULL, 
                       status = 'active', is_completed = 0, level = ?,
                       first_lesson_sent_time = ?, last_lesson_sent_time = ?,
                       activation_date = ? 
                   WHERE user_id = ? AND course_id = ?""",
                (new_level_for_user, now_utc_str, now_utc_str, now_utc_str, user_id, course_id_to_process)
            )
            await conn.commit()

        await log_action(user_id, action.upper(), course_id_to_process, new_value=str(new_level_for_user),
                         details=log_details)
        await query.answer(user_message_feedback, show_alert=True)

        await query.message.delete()
        await send_course_description(user_id, course_id_to_process)  # Отправляем описание (урок 0)
        # Меню для 0-го урока, но с новым уровнем пользователя
        await send_main_menu(user_id, course_id_to_process, 0, version_id,
                             user_course_level_for_menu=new_level_for_user)


    except Exception as e:
        logger.error(f"Ошибка при '{action}' для курса {course_id_to_process}, user {user_id}: {e}", exc_info=True)
        await query.answer("Произошла ошибка при обработке вашего запроса.", show_alert=True)

# Заглушка для ROBOKASSA_MERCHANT_LOGIN и ROBOKASSA_PASSWORD1
ROBOKASSA_MERCHANT_LOGIN = os.getenv("ROBOKASSA_MERCHANT_LOGIN", "your_robokassa_login")
ROBOKASSA_PASSWORD1 = os.getenv("ROBOKASSA_PASSWORD1", "your_robokassa_password1")




def calculate_robokassa_signature(*args) -> str:
    return hashlib.md5(":".join(str(a) for a in args).encode()).hexdigest()



@dp.callback_query(BuyCourseCallback.filter())
async def cb_buy_course_prompt(query: types.CallbackQuery, callback_data: BuyCourseCallback, state: FSMContext):
    user_id = query.from_user.id
    course_id_to_buy_str = callback_data.course_id_str  # Текстовый ID курса

    logger.info(f"Пользователь {user_id} инициировал 'покупку' курса {course_id_to_buy_str}")

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor_course_info = await conn.execute(
            "SELECT cv.title, cv.price, cv.version_id, c.title AS main_course_title FROM course_versions cv JOIN courses c ON cv.course_id = c.course_id WHERE cv.course_id = ? ORDER BY cv.price ASC LIMIT 1",
            (course_id_to_buy_str,)
        )
        course_info = await cursor_course_info.fetchone()

    if not course_info:
        await query.answer("Информация о курсе для покупки не найдена.", show_alert=True)
        return

    tariff_title, price, version_id_to_buy, main_course_title = course_info

    if price is None or price <= 0:
        await query.answer(
            "Этот курс не продается напрямую или является бесплатным. Возможно, для него нужен код активации.",
            show_alert=True)
        return

    # Форматируем инструкцию по оплате
    payment_instructions = PAYMENT_INSTRUCTIONS_TEMPLATE.format(
        user_id=user_id,
        course_id=escape_md(course_id_to_buy_str),  # Экранируем ID курса
        course_title=escape_md(main_course_title),
        tariff_title=escape_md(tariff_title),
        price=price
    )

    # Сохраняем информацию для состояния ожидания кода
    await state.update_data(
        pending_payment_course_id=course_id_to_buy_str,
        pending_payment_version_id=version_id_to_buy,
        pending_payment_price=price
    )

    builder = InlineKeyboardBuilder()
    # Кнопка для возврата к списку курсов, если пользователь передумал или уже оплатил и ждет код
    builder.button(text="⬅️ К списку курсов", callback_data="select_other_course")

    await query.message.edit_text(
        f"Для покупки курса «{escape_md(main_course_title)}» ({escape_md(tariff_title)}):\n\n"
        f"Сумма к оплате: {price} руб.\n\n"
        f"{escape_md(payment_instructions)}\n\n"  # Отображаем инструкцию
        f"После получения кода активации, отправьте его в этот чат.",
        reply_markup=builder.as_markup(),
        parse_mode=ParseMode.MARKDOWN_V2
    )
    await state.set_state(AwaitingPaymentConfirmation.waiting_for_activation_code_after_payment)
    await query.answer()


@dp.message(AwaitingPaymentConfirmation.waiting_for_activation_code_after_payment, F.text)
async def process_code_after_payment(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    activation_code = message.text.strip()
    current_data = await state.get_data()
    pending_course_id = current_data.get("pending_payment_course_id")

    logger.info(
        f"Пользователь {user_id} ввел код '{activation_code}' после инструкции по оплате для курса {pending_course_id}")

    # Попытка активации курса
    # Используем вашу существующую функцию activate_course
    is_activated, activation_message_text = await activate_course(user_id, activation_code, 1) # Предполагаем, что level=1

    await message.reply(escape_md(activation_message_text), parse_mode=ParseMode.MARKDOWN_V2)

    if is_activated:
        # Если успешно, выходим из состояния и показываем главное меню нового курса
        await state.clear()

        # Получаем данные активированного курса для отправки меню
        # (Это дублирование логики из handle_homework, можно вынести в функцию)
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute(
                "SELECT course_id, version_id FROM user_courses WHERE user_id = ? AND status='active' ORDER BY activation_date DESC LIMIT 1",
                (user_id,)  # Предполагаем, что activate_course установил правильный course_id
            )
            activated_course_data = await cursor.fetchone()

        if activated_course_data:
            actual_course_id, actual_version_id = activated_course_data
            await send_course_description(user_id, actual_course_id)
            numeric_id = await get_course_id_int(actual_course_id)
            # Отправляем меню для 0-го урока (описания)
            await send_main_menu(user_id, actual_course_id, 0, actual_version_id)
        else:
            logger.error(
                f"Не удалось получить данные об активированном курсе {activation_code} для пользователя {user_id}")
            # Можно отправить общее стартовое меню или сообщение об ошибке
            await cmd_start(message)  # Как вариант - просто /start
    else:
        # Код не подошел, пользователь остается в состоянии ожидания
        # Можно добавить кнопку "Попробовать другой код" или "Отмена"
        builder = InlineKeyboardBuilder()
        builder.button(text="⬅️ К списку курсов", callback_data="select_other_course_from_payment")  # Новый callback
        await message.reply(
            escape_md("Если у вас другой код, попробуйте ввести его. Или вернитесь к списку курсов."),
            reply_markup=builder.as_markup(),
            parse_mode=ParseMode.MARKDOWN_V2
        )


# Обработчик для кнопки "К списку курсов" из состояния ожидания оплаты
@dp.callback_query(F.data == "select_other_course_from_payment",
                   AwaitingPaymentConfirmation.waiting_for_activation_code_after_payment)
async def cb_back_to_courses_from_payment(query: types.CallbackQuery, state: FSMContext):
    await state.clear()  # Выходим из состояния ожидания кода
    # Вызываем тот же обработчик, что и для обычной кнопки "Выбрать другой курс"
    await cb_select_other_course(query, state)



# данные о курсе пользователя courses.id:int, user_courses.current_lesson, user_courses.version_id
async def get_user_course_data(user_id: int) -> tuple:
    """
    Получает данные о курсе пользователя (course_numeric_id, current_lesson, version_id).
    Возвращает None, если нет активного курса.
    """
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT courses.id, user_courses.current_lesson, user_courses.version_id 
            FROM user_courses 
            JOIN courses ON user_courses.course_id = courses.course_id
            WHERE user_courses.user_id = ? AND user_courses.status = 'active'
        """, (user_id,))
        user_course_data = await cursor.fetchone()
        logger.info(f"776 {user_course_data=}  ")
        if not user_course_data:
            logger.warning(f"Нет активного курса для пользователя {user_id}")
            return None
    return user_course_data


# 17-04
@dp.callback_query(F.data == "menu_progress")
@db_exception_handler # Обработчик для команды просмотра прогресса по всем курсам
async def cmd_progress_callback(query: types.CallbackQuery):
    """Показывает прогресс пользователя по курсам."""
    user_id = query.from_user.id
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем все активные курсы пользователя
            cursor = await conn.execute("""
                SELECT uc.course_id, c.title, uc.current_lesson, uc.activation_date, uc.version_id
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ? AND uc.status = 'active'
            """, (user_id,))
            courses = await cursor.fetchall()

            if not courses:
                await query.answer("Вы не записаны ни на один активный курс.", show_alert=True)
                return

            progress_text = ""
            now = datetime.now(pytz.utc)

            for course_id, course_title, current_lesson, activation_date_str, version_id in courses:
                # Получаем общее количество уроков курса
                cursor = await conn.execute("""
                    SELECT MAX(lesson_num) 
                    FROM group_messages 
                    WHERE course_id = ? AND lesson_num > 0
                """, (course_id,))
                total_lessons = (await cursor.fetchone())[0] or 0

                # Считаем сколько дней прошло с активации
                days_since_activation = "неизвестно"
                if activation_date_str:
                    try:
                        activation_date_naive = datetime.fromisoformat(activation_date_str)
                        # Делаем "aware" UTC. Предполагаем, что activation_date_str в БД хранится как UTC.
                        activation_date_aware_utc = pytz.utc.localize(activation_date_naive)
                        days_since_activation = (now - activation_date_aware_utc).days
                    except ValueError:
                        logger.warning(f"Некорректный формат даты активации: {activation_date_str}")

                # Вычисляем процент прохождения
                percent_complete = (current_lesson / total_lessons * 100) if total_lessons > 0 else 0

                # Формируем текст прогресса
                progress_text += (
                    f"📚 {course_title} \n"
                    f"  Пройдено уроков: {current_lesson} из {total_lessons} ({percent_complete:.1f}%)\n"
                    f"  Дней с начала курса: {days_since_activation}\n\n"
                )

            # Добавляем заголовок с общим количеством активных курсов
            total_active_courses = len(courses)
            progress_text = f"📊 Ваш прогресс по {total_active_courses} активным курсам:\n\n" + progress_text

            # Получаем клавиатуру для первого курса
            if courses:
                first_course_id, _, _, _, version_id = courses[0]
                course_numeric_id = await get_course_id_int(first_course_id)
                keyboard = get_main_menu_inline_keyboard(
                    course_numeric_id,
                    lesson_num=0,
                    user_tariff=version_id
                )
            else:
                keyboard = None

            # Отправляем сообщение с прогрессом
            await bot.send_message(
                user_id,
                progress_text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
            await query.answer("✅ Прогресс обновлен")

    except Exception as e:
        logger.error(f"Ошибка в cmd_progress_callback: {e}", exc_info=True)
        await query.answer("⚠️ Произошла ошибка при получении прогресса.", show_alert=True)


# 14-04 ночью - кнопка самоодобрения
@dp.callback_query(CourseCallback.filter(F.action == "self_approve_hw"))
@db_exception_handler
async def process_self_approve_hw(callback: types.CallbackQuery, callback_data: CourseCallback):
    """Обрабатывает нажатие на кнопку самоодобрения ДЗ."""
    user_id = callback.from_user.id
    course_numeric_id = callback_data.course_id  # Получаем course_id из callback_data
    course_id = await get_course_id_str(course_numeric_id)

    lesson_num = callback_data.lesson_num

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # 1.Обновляем статус ДЗ в базе данных
            await conn.execute("""
                UPDATE user_courses 
                SET hw_status = 'approved' 
                WHERE user_id = ? AND course_id = ? AND current_lesson = ?
            """, (user_id, course_id, lesson_num))

            # 2. Добавляем ДЗ в галерею (если еще не добавлено) и отмечаем как self-approved
            await conn.execute("""
                UPDATE homework_gallery
                SET approved_by = ?
                WHERE user_id = ? AND course_id = ? AND lesson_num = ?
            """, (user_id, user_id, course_id, lesson_num))
            await conn.commit()

            # Получаем version_id (можно убрать, если не используется)
            cursor = await conn.execute("""
                SELECT version_id FROM user_courses WHERE user_id = ? AND course_id = ? AND current_lesson = ?
            """, (user_id, course_id, lesson_num))
            version_id = (await cursor.fetchone())[0]

            keyboard = get_main_menu_inline_keyboard(
                course_numeric_id=course_numeric_id,
                lesson_num=lesson_num,
                user_tariff=version_id,
                homework_pending=False #disable_button=True
            )
            await callback.message.edit_text(  # TODO: Добавить текст
                text="🎉 ДЗ cамоодобрено! Так держать! 🔥",
                reply_markup=keyboard
            )
            # await callback.answer()  # Обязательно нужно ответить на callback
    except Exception as e:
        logger.error(f"Ошибка при самоодобрении ДЗ: {e}", exc_info=True)
        await callback.answer("⚠️ Произошла ошибка при самоодобрении ДЗ. Попробуйте позже.")


@dp.callback_query(ReplySupportCallback.filter())
async def reply_support_callback(callback: types.CallbackQuery, callback_data: ReplySupportCallback, state: FSMContext):
    user_id = callback_data.user_id
    message_id = callback_data.message_id
    logger.info(f"ReplySupportCallback: user_id={user_id}, message_id={message_id}")
    # Устанавливаем состояние ожидания ответа от админа
    await state.set_state(SupportRequest.waiting_for_response)

    # Сохраняем user_id и message_id
    await state.update_data(user_id=user_id, message_id=message_id)

    # Запрашиваем ответ от админа
    await callback.message.answer(
        "Пожалуйста, введите ваш ответ пользователю:",
        reply_markup=ForceReply(selective=True),
        parse_mode=None
    )

    # Подтверждаем получение callback
    await callback.answer()


#======================Конец обработчиков слов и хэндлеров кнопок=========================================

async def check_state(message: types.Message, state: FSMContext) -> bool:
    """Проверяет, находится ли пользователь в состоянии Form.feedback"""
    return state and await state.get_state() != Form.feedback


# НАДО 17-04
@db_exception_handler
async def update_homework_status(user_id: int, course_id: str, lesson_num: int, status: str):
    """Updates homework status in the database"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "UPDATE user_courses SET hw_status = ? WHERE user_id = ? AND course_id = ? AND current_lesson = ?",
                (status, user_id, course_id, lesson_num),
            )
            await db.commit()

        logger.info(f"Homework status updated for user {user_id}, course {course_id}, lesson {lesson_num} to {status}")
    except Exception as e:
        logger.error(f"Error updating homework status in database: {e}")

# 16-04 ночер сделаем клаву отдельно
def create_admin_keyboard(user_id: int, course_id: int, lesson_num: int, message_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру с кнопками принятия/отклонения ДЗ в две строки"""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=AdminHomeworkCallback(
                        action="approve_hw",
                        user_id=user_id,
                        course_id=course_id,
                        lesson_num=lesson_num,
                        message_id=message_id
                    ).pack()
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=AdminHomeworkCallback(
                        action="reject_hw",
                        user_id=user_id,
                        course_id=course_id,
                        lesson_num=lesson_num,
                        message_id=message_id
                    ).pack()
                )],
            [
                InlineKeyboardButton(
                    text="✅ Принять и отправить сообщение",
                    callback_data=AdminHomeworkCallback(
                        action="approve_reason",
                        user_id=user_id,
                        course_id=course_id,
                        lesson_num=lesson_num,
                        message_id=message_id
                    ).pack()
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить и обосновать",
                    callback_data=AdminHomeworkCallback(
                        action="reject_reason",
                        user_id=user_id,
                        course_id=course_id,
                        lesson_num=lesson_num,
                        message_id=message_id
                    ).pack()
                ) ]
        ])


async def send_message_to_user(user_id: int, text: str, reply_markup: InlineKeyboardMarkup = None):
    """Утилита для отправки сообщения пользователю."""
    try:
        await bot.send_message(user_id, text, reply_markup=reply_markup,parse_mode=None)
    except TelegramBadRequest as e:
        logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}", exc_info=True)


def get_tariff_name(version_id: str) -> str:
    """Возвращает человекочитаемое название тарифа."""
    TARIFF_NAMES = {
        "v1": "Соло",
        "v2": "Группа",
        "v3": "VIP"
    }
    return TARIFF_NAMES.get(version_id, f"Тариф {version_id}")


# НАДО 18-04
@dp.callback_query(AdminHomeworkCallback.filter(F.action.in_(["approve_hw", "reject_hw", "approve_reason", "reject_reason"])))
async def process_homework_action(callback_query: types.CallbackQuery, callback_data: AdminHomeworkCallback, state: FSMContext):
    """Handles approving, rejecting, or requesting feedback for homework."""
    logger.info(F"process_homework_action")
    try:
        user_id = callback_data.user_id
        course_numeric_id = callback_data.course_id
        course_id = await get_course_id_str(course_numeric_id)
        lesson_num = callback_data.lesson_num
        message_id = callback_data.message_id
        action = callback_data.action

        await callback_query.answer()

        if action == "approve_hw":
            await handle_homework_result(user_id, course_id, course_numeric_id, lesson_num, callback_query.from_user.id, "", True, callback_query)
        elif action == "reject_hw":
            await handle_homework_result(user_id, course_id, course_numeric_id, lesson_num, callback_query.from_user.id, "", False, callback_query)
        elif action in ["approve_reason", "reject_reason"]:
            # Store data and prompt for feedback
            await state.update_data(
                user_id=user_id,
                course_id=course_id,
                course_numeric_id=course_numeric_id,
                lesson_num=lesson_num,
                message_id=message_id,
                action=action.split("_")[0],  # "approve" or "reject"
                admin_id=callback_query.from_user.id,
                callback_query=callback_query # Add callback_query
            )
            text = "Ожидаю сообщение от администратора для одобрения/отклонения."
            await bot.edit_message_text(
                chat_id=ADMIN_GROUP_ID,
                message_id=callback_query.message.message_id,
                text=escape_md(text),  # Экранируем текст
                parse_mode=None  # Указываем parse_mode
            )
            await state.set_state(Form.feedback)
    except Exception as e:
        logger.error(f"❌ Error in process_homework_action: {e}", exc_info=True)


# Обработка callback-запроса для оставления отзыва
@dp.callback_query(F.data == "menu_feedback")
async def cmd_feedback(query: types.CallbackQuery, state: FSMContext):
    """Обработка callback-запроса для оставления отзыва."""
    await query.message.edit_text("Пожалуйста, напишите ваш отзыв:")
    await state.set_state(Form.feedback)
    await query.answer()

@dp.message(Form.feedback)
async def process_feedback(message: types.Message, state: FSMContext):
    """Process feedback from admin and finalize approval/rejection"""
    logger.info(F"===========================process_feedback")
    try:
        user_data = await state.get_data()
        user_id = user_data.get("user_id")
        course_id = user_data.get("course_id")
        course_numeric_id = user_data.get("course_numeric_id")
        lesson_num = user_data.get("lesson_num")
        admin_id = message.from_user.id
        feedback_text = message.text
        action = user_data.get("action")  # "approve" or "reject"

        is_approved = action == "approve"
        callback_query = user_data.get("callback_query")

        await handle_homework_result(user_id, course_id, course_numeric_id, lesson_num, admin_id, feedback_text, is_approved, callback_query)


    except Exception as e:
        logger.error(f"❌ Error in process_feedback: {e}", exc_info=True)
    finally:
        await state.clear()

# вызывается из process_feedback - вверху функция
async def handle_homework_result(
        user_id: int, course_id: str, course_numeric_id: int, lesson_num: int,
        admin_id: int, feedback_text: str, is_approved: bool,
        callback_query: types.CallbackQuery = None,
        original_admin_message_id_to_delete: int = None
):
    logger.info(
        f"handle_homework_result для user_id={user_id}, course_id={course_id}, lesson_num={lesson_num}, approved={is_approved}, admin_id={admin_id}")
    try:
        hw_status = "approved" if is_approved else "rejected"
        await update_homework_status(user_id, course_id, lesson_num, hw_status)  # Обновляем статус ДЗ

        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем информацию о курсе пользователя
            cursor_uc = await conn.execute(
                "SELECT version_id FROM user_courses WHERE user_id = ? AND course_id = ?",
                (user_id, course_id)
            )
            user_course_info = await cursor_uc.fetchone()
            if not user_course_info:
                logger.error(
                    f"Не найдены данные курса для user_id={user_id}, course_id={course_id} в handle_homework_result")
                if callback_query: await callback_query.answer("Ошибка: данные курса не найдены.", show_alert=True)
                return
            version_id = user_course_info[0]
            tariff_name = get_tariff_name(version_id)

            # Получаем общее количество уроков в курсе
            cursor_tl = await conn.execute(
                "SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ? AND lesson_num > 0", (course_id,)
            )
            total_lessons_data = await cursor_tl.fetchone()
            total_lessons = total_lessons_data[0] if total_lessons_data and total_lessons_data[0] is not None else 0
            logger.info(
                f"Для курса {course_id}: lesson_num={lesson_num} (текущий обработанный), total_lessons={total_lessons}")

            # ---- НОВАЯ ЛОГИКА ----
            if is_approved and lesson_num >= total_lessons and total_lessons > 0:
                # ДЗ для ПОСЛЕДНЕГО урока одобрено - курс завершен!
                logger.info(f"Последний урок {lesson_num} курса {course_id} завершен и ДЗ одобрено для user {user_id}.")
                course_title_safe = escape_md(await get_course_title(course_id))
                message_text_completion = (
                    f"🎉 Поздравляем с успешным завершением курса «{course_title_safe}»\\! 🎉\n\n"
                    "Вы прошли все уроки. Что вы хотите сделать дальше?"
                )
                builder_completion = InlineKeyboardBuilder()
                builder_completion.button(text=escape_md("Выбрать другой курс"), callback_data="select_other_course")
                builder_completion.button(text=escape_md("Оставить отзыв"), callback_data="leave_feedback")

                await bot.send_message(
                    chat_id=user_id,
                    text=message_text_completion,
                    reply_markup=builder_completion.as_markup(),
                    parse_mode=ParseMode.MARKDOWN_V2
                )
                # Обновляем статус курса на 'completed'
                await conn.execute(
                    "UPDATE user_courses SET status = 'completed', is_completed = 1 WHERE user_id = ? AND course_id = ?",
                    (user_id, course_id)
                )
                # await conn.commit() # Коммит будет ниже, после отправки уведомления админу
            else:
                # ДЗ одобрено/отклонено, но это не последний урок, или ДЗ отклонено
                message_to_user_main_part = ""
                if is_approved:
                    message_to_user_main_part = f"✅ Ваше домашнее задание по курсу {escape_md(course_id)}, урок {lesson_num} принято"
                    if feedback_text:
                        message_to_user_main_part += f"\n\nКомментарий:\n{escape_md(feedback_text)}"
                else:  # Отклонено
                    message_to_user_main_part = f"❌ Ваше домашнее задание по курсу {escape_md(course_id)}, урок {lesson_num} отклонено"
                    if feedback_text:
                        message_to_user_main_part += f"\n\nПричина:\n{escape_md(feedback_text)}"

                next_lesson_display_text = await get_next_lesson_time(user_id, course_id, lesson_num)

                menu_text_for_user = (
                    f"{message_to_user_main_part}\n\n"
                    f"⏳ Следующий урок: {escape_md(next_lesson_display_text)}\n\n"
                    f"🎓 Курс: {escape_md(await get_course_title(course_id))}\n"
                    f"🔑 Тариф: {escape_md(tariff_name)}\n"
                    f"📚 Текущий урок: {lesson_num}"
                )
                keyboard = get_main_menu_inline_keyboard(course_numeric_id, lesson_num, version_id, homework_pending=(
                    not is_approved))  # homework_pending если не одобрено
                await bot.send_message(
                    chat_id=user_id,
                    text=menu_text_for_user,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            # ---- КОНЕЦ НОВОЙ ЛОГИКИ ----

            # Уведомление администратора/ИИ, совершившего действие (остается как было)
            admin_actor_name = "Система "  # Дефолт, если нет информации
            if callback_query and callback_query.from_user:
                admin_actor_name = escape_md(
                    callback_query.from_user.full_name or f"ID:{callback_query.from_user.id}")
            elif admin_id:
                try:
                    actor_chat = await bot.get_chat(admin_id)
                    admin_actor_name = escape_md(actor_chat.full_name or f"ID:{admin_id}")
                except Exception:
                    admin_actor_name = f"Актор ID:{admin_id}"

            user_name_safe = escape_md(await get_user_name(user_id))
            course_id_safe = escape_md(course_id)
            action_str = "принято" if is_approved else "отклонено"

            notification_to_admin_group = (
                f"ДЗ от {user_name_safe} ID: {user_id} по курсу {course_id_safe}, урок {lesson_num} "
                f"было **{action_str}** актор: {admin_actor_name}"
            )
            if feedback_text:
                notification_to_admin_group += f"\nКомментарий/причина: {escape_md(feedback_text)}"

            if ADMIN_GROUP_ID:  # Проверяем, что ID админ группы есть
                await bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=notification_to_admin_group,
                    parse_mode=ParseMode.MARKDOWN_V2
                )

            await conn.commit()  # Один коммит в конце

            action_details = "одобрено" if is_approved else "отклонено"
            if feedback_text: action_details += f" с комментарием"
            await log_action(user_id, "HOMEWORK_REVIEWED", course_id, lesson_num,
                             new_value=hw_status,  # 'approved' или 'rejected'
                             details=f"Проверил: {admin_id}. Результат: {action_details}")

        # В handle_homework_result, после отправки уведомления пользователю и админу
        # РЕДАКТИРОВАНИЕ исходного сообщения с кнопками в админ-группе
        # Блок удаления/редактирования сообщения в админ-группе
        message_id_to_modify = None
        if callback_query and callback_query.message:
            message_id_to_modify = callback_query.message.message_id
        elif original_admin_message_id_to_delete:
            message_id_to_modify = original_admin_message_id_to_delete

        if message_id_to_modify and ADMIN_GROUP_ID:  # Убедитесь, что ADMIN_GROUP_ID используется правильно
            try:  # Вложенный try для операции с сообщением в админ-группе
                action_text_for_admin_msg = "✅ ОДОБРЕНО" if is_approved else "❌ ОТКЛОНЕНО"
                admin_actor_name_for_status = "Неизвестный"  # Получите имя актора, как делали выше
                if callback_query and callback_query.from_user:
                    admin_actor_name_for_status = escape_md(
                        callback_query.from_user.full_name or f"ID:{callback_query.from_user.id}")
                elif admin_id:
                    try:
                        actor_chat = await bot.get_chat(admin_id)
                        admin_actor_name_for_status = escape_md(actor_chat.full_name or f"ID:{admin_id}")
                    except Exception:
                        admin_actor_name_for_status = f"Актор ID:{admin_id}"

                await bot.edit_message_reply_markup(
                    chat_id=ADMIN_GROUP_ID,
                    message_id=message_id_to_modify,
                    reply_markup=None  # Убираем кнопки
                )
                logger.info(f"Убрана клавиатура с сообщения {message_id_to_modify} в админ-группе.")

                # Отправляем новое сообщение в ответ, указывая статус
                status_update_text = f"Статус ДЗ (сообщение выше): {action_text_for_admin_msg} (by {admin_actor_name_for_status})."
                if feedback_text:
                    status_update_text += f"\nКомментарий: {escape_md(feedback_text)}"

                await bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=status_update_text,
                    reply_to_message_id=message_id_to_modify,
                    parse_mode=ParseMode.MARKDOWN_V2
                )
            except TelegramBadRequest as e_tg_edit:  # Ловим ошибку конкретно для изменения сообщения
                logger.warning(
                    f"Не удалось изменить/ответить на сообщение {message_id_to_modify} в админ-группе: {e_tg_edit}")
            except Exception as e_inner:  # Другие ошибки во вложенном try
                logger.error(f"Неожиданная ошибка при модификации сообщения в админ-группе: {e_inner}", exc_info=True)

        if callback_query:
            await callback_query.answer()

        # Удаление исходного сообщения с кнопками в админ-группе
        # message_id_to_delete = None
        # if callback_query and callback_query.message:
        #     message_id_to_delete = callback_query.message.message_id
        # elif original_admin_message_id_to_delete:
        #     message_id_to_delete = original_admin_message_id_to_delete
        #
        # if message_id_to_delete and ADMIN_GROUP_ID:  # Проверяем, что ID админ группы есть
        #     try:
        #         await bot.delete_message(chat_id=ADMIN_GROUP_ID, message_id=message_id_to_delete)
        #     except TelegramBadRequest as e:
        #         logger.warning(f"Не удалось удалить сообщение {message_id_to_delete} в админ-группе: {e}")
        #
        # if callback_query:
        #     await callback_query.answer()

    except Exception as e:
        logger.error(f"❌ Ошибка в handle_homework_result: {e}", exc_info=True)
        if callback_query:
            await callback_query.answer("Произошла ошибка при обработке ДЗ.", show_alert=True)


async def get_user_name(user_id: int) -> str:
    """Получает имя пользователя по ID."""
    logger.info(F"get_user_name")
    try:
        user = await bot.get_chat(user_id)
        return user.first_name or user.username or str(user_id)
    except Exception as e:
        logger.error(f"Ошибка при получении имени пользователя: {e}")
        return str(user_id)



@dp.message(F.chat.id == ADMIN_GROUP_ID, SupportRequest.waiting_for_response)
@db_exception_handler
async def admin_response_handler(message: types.Message, state: FSMContext):
    """Обрабатывает ответы админов в группе поддержки (без reply_to_message)."""
    try:
        user_id = (await state.get_data()).get("user_id")  # Get user_id from FSM
        logger.info(f"admin_response_handler {user_id=}")
        if user_id:
            # Отправляем сообщение пользователю
            await bot.send_message(
                chat_id=user_id,
                text=f"Ответ поддержки:\n{message.text}",
                parse_mode = None
            )

            await message.answer(f"Ответ отправлен пользователю {user_id}.", parse_mode=None)

            logger.info(f"1111 {user_id=}")
            # Получаем данные для меню (course_id, lesson_num, version_id)
            async with aiosqlite.connect(DB_FILE) as conn:
                cursor = await conn.execute("""
                   SELECT course_id, current_lesson, version_id, hw_status, hw_type
                   FROM user_courses
                   WHERE user_id = ? AND status = 'active'
                """, (user_id,))
                user_course_data = await cursor.fetchone()
            logger.info(f"1111 {user_course_data=}")

            if user_course_data:
                course_id, lesson_num, version_id, hw_status,hw_type = user_course_data
                # Отправляем главное меню
                await send_main_menu(user_id, course_id, lesson_num, version_id,
                       homework_pending=False if hw_status in ('approved' ,'not_required', 'none') else True,
                       hw_type = hw_type)
            else:
                await bot.send_message(user_id, "Не удалось получить данные о курсе.")

            await state.clear()  # Clear the state

            logger.info(f"Ответ от админа для {user_id=} успешно переслан.")
        else:
            logger.warning("Не найден user_id в FSM.")
            await message.answer("Не могу определить, какому пользователю отправить это сообщение.  Убедитесь, что вы ответили на запрос поддержки, инициированный кнопкой.", parse_mode=None)

    except Exception as e:
        logger.error(f"Ошибка при обработке ответа админа: {e}")
        await message.answer("Ошибка при отправке сообщения пользователю.", parse_mode=None)



# ----------------------------- пользователь – последний -------------------------


# =========================== сначала жалуемся и просим поддержку =============




@dp.message(SupportRequest.waiting_for_message)
async def handle_support_message(message: types.Message, state: FSMContext):
    """Обработчик сообщений в состоянии ожидания запроса в поддержку"""
    user_id = message.from_user.id
    user_message = message.text  # Получаем текст сообщения от пользователя
    logger.info(f"handle_support_message {user_message=}")
    try:
        if ADMIN_GROUP_ID:
            # Пересылаем сообщение пользователя в группу админов
            try:
                forwarded_message = await bot.forward_message(
                    chat_id=ADMIN_GROUP_ID,
                    from_chat_id=user_id,
                    message_id=message.message_id
                )

                # Создаем кнопку "Ответить" прямо под пересланным сообщением
                reply_keyboard = InlineKeyboardBuilder()
                reply_keyboard.button(
                    text="Ответить",
                    callback_data=ReplySupportCallback(user_id=user_id, message_id=message.message_id)
                )
                logger.info(f"500 handle_support_message {user_message=}")
                await bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=f"Вопрос от {message.from_user.full_name} (ID: {user_id})",
                    reply_markup=reply_keyboard.as_markup(),
                    parse_mode=None
                )

                # Отправляем пользователю подтверждение
                await message.answer(
                    "✅ Ваш запрос отправлен в поддержку. Ожидайте ответа.",
                    reply_markup=types.ReplyKeyboardRemove(),  # Убираем клавиатуру
                    parse_mode=None
                )

                logger.info(f"handle_support_message всё отправили ")

            except TelegramBadRequest as e:
                logger.error(f"Ошибка отправки сообщения: {e}")
                await message.answer("❌ Не удалось отправить запрос. Попробуйте позже.",parse_mode=None)
        else:
            await message.answer("⚠️ Служба поддержки временно недоступна.",parse_mode=None)

    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения от пользователя: {e}")
        await message.answer("❌ Произошла ошибка при обработке запроса. Попробуйте позже.", parse_mode=None)

    finally:
        # Сбрасываем состояние
        await state.clear()



# =========================== теперь всё остальное

@dp.message(F.text, check_state)
@db_exception_handler
async def handle_text(message: types.Message, state: FSMContext):
    """
    Минималистичный обработчик текста. Проверяет курс и передаёт дальше.
    """
    user_id = message.from_user.id
    text = message.text.strip()
    logger.info(f"handle_text: {text=} {user_id=}")

    if text == "/cancel":
        await message.reply("Действие отменено.", parse_mode=None)
        return

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            "SELECT course_id FROM user_courses WHERE user_id = ? AND status = 'active'",
            (user_id,))
        active_course = await cursor.fetchone()
        logger.info(f"handle_text: active_course={active_course}")

    if active_course:
        logger.info("handle_text: отправляем в handle_homework")
    return await handle_homework(message)


# смайлики из "поддержки" кнопки пользователя
@dp.callback_query(F.data.startswith("support_eval:"))
async def process_support_evaluation(callback: types.CallbackQuery):
    """Обрабатывает оценку пользователя после обращения в поддержку."""
    try:
        user_id = callback.from_user.id
        evaluation = callback.data.split(":")[1]  # Извлекаем оценку (1-5)
        message_id = callback.message.message_id
        logger.info(f"Получена оценка {evaluation=} от {user_id=}")

        # Сохраняем оценку в базе данных (пример)
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                INSERT INTO support_evaluations (user_id, message_id, evaluation, timestamp)
                VALUES (?, ?, ?, ?)
            """, (user_id, message_id, evaluation, datetime.now(pytz.utc)))
            await conn.commit()

        # Подтверждение пользователю
        await callback.answer(f"Спасибо за вашу оценку ({evaluation})!", show_alert=True)

        # Отправляем оценку администраторам (опционально)
        if ADMIN_GROUP_ID:
            await bot.send_message(
                chat_id=ADMIN_GROUP_ID,
                text=f"Пользователь {callback.from_user.full_name} (ID: {user_id}) оценил поддержку на {evaluation}."
            )
    except Exception as e:
        logger.error(f"Ошибка при обработке оценки поддержки: {e}")
        await callback.answer("Произошла ошибка при обработке вашей оценки.", show_alert=True)


# --- Database Retry Utility ---
async def safe_db_execute(conn, query, params=None, retries=MAX_DB_RETRIES, delay=DB_RETRY_DELAY):
    """Executes a database query with retries."""
    for attempt in range(retries):
        try:
            if params:
                cursor = await conn.execute(query, params)
            else:
                cursor = await conn.execute(query)
            return cursor  # Return the cursor if successful
        except (sqlite3.OperationalError, aiosqlite.Error) as e:
            logger.warning(f"DB error on attempt {attempt + 1}: {e}. Retrying in {delay}s...")
            if attempt == retries - 1:
                logger.error(f"Max retries reached. Aborting query: {query}")
                raise  # Re-raise the exception if retries are exhausted
            await asyncio.sleep(delay)
        except Exception as e:
            logger.error(f"Unexpected error during DB execution: {e}")
            raise


# ----------------- новый обработчик и текстовой домашки и фото -------- от пользователя ------------
@dp.message(F.content_type.in_({'photo', 'document', 'text'}), F.chat.type == "private")
@db_exception_handler
async def handle_homework(message: types.Message):
    """Обрабатывает отправку домашних заданий (фото/документы/текст)"""
    user_id = message.from_user.id
    logger.info(f" новый обработчик и текстовой домашки и фото  17-04 {user_id=}")

    # Получаем данные о курсе
    user_course_data = await get_user_course_data(user_id)
    logger.info(f" строка 4162 {user_course_data=}")
    if not user_course_data:
        await message.answer("Проверяю код", parse_mode=None)
        activation_result = await activate_course(user_id, message.text) # Get status code
        is_activated = activation_result[0]
        activation_message = activation_result[1]

        await message.answer(activation_message, parse_mode=None) # answer

# ======================== вот тут активация ===================================
        if is_activated:
            logger.info(f"444 is_activated now")
            # Load course data to get course_id and version_id
            async with aiosqlite.connect(DB_FILE) as conn:
                try:
                    # cursor = await conn.execute("""
                    #     SELECT course_id, version_id FROM user_courses WHERE user_id = ?
                    # """, (user_id,))
                    cursor = await safe_db_execute(
                        conn,
                        "SELECT course_id, version_id FROM user_courses WHERE user_id = ?",
                        (user_id,)
                    )

                    new_course_data = await cursor.fetchone()
                    course_id, version_id = new_course_data

                    # Fetch additional info
                    course_title = await get_course_title(course_id)
                    course_numeric_id = await get_course_id_int(course_id)
                    tariff_name = get_tariff_name(version_id)
                    if course_numeric_id == 0:
                        logger.error(f"Не найден курс {course_id=}")
                    lesson_num = 0  # After activation the first lesson is shown

                    # Get the lesson interval information based on user_id and version
                    message_interval = settings.get("message_interval", 24) #message_interval = 0.05
                    logger.info(f" message_interval = {message_interval} ")

                except Exception as e:
                    logger.error(f" 😱 Ой-ой! Какая-то ошибка с базой после активации: {e}")
                    await message.answer(" 😥 Кажется, база данных уснула. Попробуйте чуть позже", parse_mode=None)
                    return

            await send_course_description(user_id, course_id) # show course description and new keyboards

            logger.info(f"3 перед созданием клавиатуры{course_numeric_id=}")
            keyboard = get_main_menu_inline_keyboard(  # await убрали
                course_numeric_id = course_numeric_id,
                lesson_num=lesson_num,
                user_tariff=version_id
            )

            # Формируем приветственное сообщение с информацией о курсе и тарифе
            first_name = message.from_user.first_name or message.from_user.username or "Пользователь"
            welcome_message = (
                f"*Добро пожаловать*, {escape_md(first_name)}\n\n"
                f"Вы успешно активировали *{escape_md(course_title)}*\n"
                f"Ваш тариф: *{escape_md(tariff_name)}*\n"
                f"Интервал между уроками: *{escape_md(str(message_interval))}* ч\n\n" #todo: interval
                f"Желаем удачи в прохождении курса"
            )
            logger.info(f"3332 {welcome_message=}")
            await message.answer(welcome_message, reply_markup=keyboard, parse_mode="MarkdownV2")


        return # break here

    course_numeric_id, current_lesson, version_id = user_course_data
    course_id = await get_course_id_str(course_numeric_id)

    # Если тариф v1 → самопроверка
    if version_id == 'v1':
        try:
            # Экранируем сообщение с помощью escape_md
            await message.answer(
                escape_md("✅ Домашка принята для самопроверки и будет одобрена автоматически!"),
                parse_mode=ParseMode.MARKDOWN_V2
            )
            logger.info(f"handle_homework: Отправлено сообщение об одобрении домашки для самопроверки")
            async with aiosqlite.connect(DB_FILE) as conn:
                await conn.execute("""
                    UPDATE user_courses 
                    SET hw_status = 'approved'
                    WHERE user_id = ? AND course_id = ?
                """, (user_id, course_id))
                await conn.commit()

                # ---- НОВАЯ ЛОГИКА ЗДЕСЬ для v1 ----
                cursor_total = await conn.execute(
                    "SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ? AND lesson_num > 0",
                    (course_id,))
                total_lessons_data = await cursor_total.fetchone()
                total_lessons = total_lessons_data[0] if total_lessons_data and total_lessons_data[0] is not None else 0

                if current_lesson >= total_lessons and total_lessons > 0:
                    logger.info(
                        f"Курс {course_id} (v1) завершен для {user_id} после самоодобрения ДЗ урока {current_lesson}.")
                    course_title_safe = escape_md(await get_course_title(course_id))
                    message_text_completion = (
                        f"🎉 Поздравляем с успешным завершением курса «{course_title_safe}»\\! 🎉\n\n"
                        "Вы прошли все уроки. Что вы хотите сделать дальше?"
                    )
                    builder_completion = InlineKeyboardBuilder()
                    builder_completion.button(text=escape_md("Выбрать другой курс"),
                                              callback_data="select_other_course")
                    builder_completion.button(text=escape_md("Оставить отзыв"), callback_data="leave_feedback")

                    await bot.send_message(
                        chat_id=user_id,
                        text=message_text_completion,
                        reply_markup=builder_completion.as_markup(),
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
                    await conn.execute(
                        "UPDATE user_courses SET status = 'completed', is_completed = 1 WHERE user_id = ? AND course_id = ?",
                        (user_id, course_id)
                    )
                    await conn.commit()
                else:
                    # Если курс не завершен, выводим обычное меню
                    await send_main_menu(user_id, course_id, current_lesson, version_id, homework_pending=False)
                    # ---- КОНЕЦ НОВОЙ ЛОГИКИ для v1 ----
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения об авто-аппруве: {e}", exc_info=True)
        return

    # Формируем сообщение для админа
    course_title_from_db = await get_course_title(course_numeric_id)  # Получаем название по числовому ID
    # Защита, если курс еще не имеет названия в БД или course_numeric_id=0
    display_course_title = course_title_from_db if course_title_from_db != "Неизвестный курс" else course_id  # course_id здесь строковый от user_course_data

    user_display_name = message.from_user.full_name  # Имя пользователя
    if message.from_user.username:
        user_display_name += f" @{message.from_user.username}"

    # Создаем клавиатуру для админа (ДО формирования сообщения)
    admin_keyboard = create_admin_keyboard(
        user_id=user_id,
        course_id=course_numeric_id,
        lesson_num=current_lesson,
        message_id=message.message_id
    )

    # Формируем сообщение для админа в зависимости от типа контента
    if message.text:
        homework_type = "Текстовая домашка"
        text = message.text.strip()
        file_id = None
        admin_message_content = f"✏️ Текст: {md.quote(text)}"
    elif message.photo:
        homework_type = "Домашка с фото"
        text = message.caption or ""  # Получаем подпись к фото (если есть)
        file_id = message.photo[-1].file_id  # Берем последнее (самое большое) фото
        admin_message_content = f"📸 Фото: {file_id}\n✏️ Описание: {md.quote(text)}"
    elif message.document:
        homework_type = "Домашка с документом"
        text = message.caption or ""  # Получаем подпись к документу (если есть)
        file_id = message.document.file_id
        admin_message_content = f"📎 Документ: {file_id}\n✏️ Описание: {md.quote(text)}"
    else:
        await message.answer("Неподдерживаемый тип контента.")
        return

    # Добавляем ID пользователя в сообщение админам
    admin_message_text = (
        f"📝 Новое ДЗ {homework_type}\n"
        f"👤 Пользователь: {escape_md(user_display_name)} ID: {user_id}\n"  # Добавили user_id
        f"📚 Курс: {escape_md(display_course_title)}\n"  # Используем display_course_title
        f"⚡ Тариф: {escape_md(version_id)}\n"
        f"📖 Урок: {current_lesson}\n"
    )

    try:
        # Сохраняем информацию о ДЗ в базе данных (для последующей обработки админами)
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                INSERT OR REPLACE INTO admin_context (user_id, course_id, lesson_num, text)
                VALUES (?, ?, ?, ?)
            """, (user_id, course_numeric_id, current_lesson, f"{user_id}:{course_numeric_id}:{current_lesson}"))
            await conn.commit()

        # Отправляем сообщение админам
        sent_admin_message = None  # Для отслеживания отправленного сообщения, если понадобится его ID

        # Формируем базовый caption (без описания из ДЗ, оно добавится ниже если есть)
        base_caption_for_media = admin_message_text

        # Добавляем описание из ДЗ к caption, если оно есть
        # text здесь - это message.caption из входящего сообщения с ДЗ
        description_from_homework = text if text else ""  # text = message.caption or ""
        if description_from_homework:
            caption_with_description = base_caption_for_media + f"\n✏️ Описание: {escape_md(description_from_homework)}"
        else:
            caption_with_description = base_caption_for_media

        if message.photo:
            sent_admin_message = await bot.send_photo(
                chat_id=ADMIN_GROUP_ID,
                photo=message.photo[-1].file_id,
                caption=caption_with_description,
                reply_markup=admin_keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        elif message.video:
            sent_admin_message = await bot.send_video(
                chat_id=ADMIN_GROUP_ID,
                video=message.video.file_id,
                caption=caption_with_description,
                reply_markup=admin_keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        elif message.document:
            sent_admin_message = await bot.send_document(
                chat_id=ADMIN_GROUP_ID,
                document=message.document.file_id,
                caption=caption_with_description,
                reply_markup=admin_keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        elif message.audio:  # Новый тип
            sent_admin_message = await bot.send_audio(
                chat_id=ADMIN_GROUP_ID,
                audio=message.audio.file_id,
                caption=caption_with_description,
                reply_markup=admin_keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        elif message.voice:  # Новый тип
            sent_admin_message = await bot.send_voice(
                chat_id=ADMIN_GROUP_ID,
                voice=message.voice.file_id,
                caption=caption_with_description,
                # Для voice caption обычно не отображается клиентами, но API его принимает
                reply_markup=admin_keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        elif message.animation:  # Новый тип (GIF)
            sent_admin_message = await bot.send_animation(
                chat_id=ADMIN_GROUP_ID,
                animation=message.animation.file_id,
                caption=caption_with_description,
                reply_markup=admin_keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        elif message.text:  # Если это текстовая домашка
            # text здесь - это message.text.strip()
            final_admin_text = admin_message_text + f"\n✏️ Текст ДЗ:\n{escape_md(text)}"  # text уже взят из message.text.strip()
            sent_admin_message = await bot.send_message(
                ADMIN_GROUP_ID,
                final_admin_text,
                reply_markup=admin_keyboard,
                parse_mode=ParseMode.MARKDOWN_V2
            )
        else:
            logger.warning(f"Получен неподдерживаемый тип контента для ДЗ от user {user_id}: {message.content_type}")
            await message.answer(escape_md("Неподдерживаемый тип файла для домашнего задания."),
                                 parse_mode=ParseMode.MARKDOWN_V2)
            return  # Выходим, если тип не поддерживается

        # Если нужно обновить callback_data кнопок с ID отправленного сообщения:
        # if sent_admin_message:
        #     new_keyboard = create_admin_keyboard(
        #         user_id=user_id,
        #         course_id=course_numeric_id,
        #         lesson_num=current_lesson,
        #         message_id=sent_admin_message.message_id # <--- Новый ID
        #     )
        #     await bot.edit_message_reply_markup(
        #         chat_id=ADMIN_GROUP_ID,
        #         message_id=sent_admin_message.message_id,
        #         reply_markup=new_keyboard
        #     )

        await message.answer(
            escape_md(f"✅ {homework_type} на проверке!"),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка отправки домашки админам: {e}", exc_info=True)
        await message.answer(escape_md("Произошла ошибка при отправке вашего ДЗ. Попробуйте позже."),
                             parse_mode=ParseMode.MARKDOWN_V2)

# единое главное меню для пользователя. Теперь с левелами
async def send_main_menu(user_id: int, course_id: str, lesson_num: int, version_id: str,
                         homework_pending: bool = False, hw_type: str = 'none', user_course_level_for_menu: int = 1):
    """Отправляет главное меню."""
    logger.info(f"send_main_menu: {course_id=}, {lesson_num=}, {version_id=}, {homework_pending=} {user_course_level_for_menu=}")
    try:
        course_title = await get_course_title(course_id)
        tariff_name = settings["tariff_names"].get(version_id, "Базовый")
        interval = settings.get("message_interval", 24) #message_interval = 0.05
        logger.info(f"222 send_main_menu: {course_title=}, {tariff_name=}, {interval=}")

        # Передаем lesson_num (номер текущего отображаемого урока в меню)
        next_lesson_display_text = await get_next_lesson_time(user_id, course_id, lesson_num)
        # Получаем время следующего урока по новому
        next_lesson_time = await get_next_lesson_time(user_id, course_id, lesson_num)
        logger.info(f"400223 send_main_menu: {next_lesson_time=} next_lesson_display_text {next_lesson_display_text=}")

        # Форматируем текст меню
        domashka_text = "не жду"  # По умолчанию (если homework_pending=False)
        if homework_pending:
            if hw_type and hw_type.lower() != 'none':  # Проверяем, что hw_type не None и не строка 'none'
                domashka_text = f"Ожидаю {escape_md(hw_type)}"
            else:
                domashka_text = "Ожидаю ДЗ"  # Более общее сообщение, если тип не указан
        #domashka_status_text = f"Ожидаю {escape_md(str(hw_type))}" if homework_pending and hw_type else "принята, урок придёт по расписанию"
        text = (f"🎓 *Курс:* {md.quote(course_title)}\n"
                f"🔑 *Тариф:* {md.quote(tariff_name)}\n"
                f"📚 *Урок:* {lesson_num}\n"
                f"⏳ *Интервал:* {escape_md(str(interval))} ч\n"
                f"⏳ *Домашка:* {domashka_text}\n"
        )

        text += f"🕒 *Следующий урок:* {escape_md(next_lesson_display_text)}\n"

        course_numeric_id = await get_course_id_int(course_id)
        keyboard = get_main_menu_inline_keyboard(
            course_numeric_id=course_numeric_id,
            lesson_num=lesson_num,
            user_tariff=version_id,
            homework_pending=homework_pending
        )
        await bot.send_message(
            user_id,
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке меню: {e}")


# Обработчик последний - чтобы не мешал другим обработчикам работать. Порядок имеет значение
@dp.message(F.text)  # Фильтр только для текстовых сообщений
async def handle_activation_code(message: types.Message): # handle_activation_code process_message
    """Проверяет код активации и выдаёт уроки, если всё окей"""
    user_id = message.from_user.id
    code = message.text.strip().lower()  # Приводим к нижнему регистру
    logger.info(f"стоп сюда не должны попадать никогда! 7 process_message Проверяем код: {code}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Ищем курс
            cursor = await conn.execute("""
                SELECT c.course_id, c.version_id, c.title
                FROM course_activation_codes cac
                JOIN course_versions c ON cac.course_id = c.course_id
                WHERE cac.code_word = ?
            """, (code,))
            course_data = await cursor.fetchone()
            logger.info(f"7 1318 course_data:Найдены данные курса: {course_data}")

        if not course_data:
            return await message.answer("Неверное кодовое слово. Попробуйте еще раз или свяжитесь с поддержкой.", parse_mode=None)

        course_id, version_id, course_name = course_data

        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверим, не активирован ли уже этот курс
            cursor = await conn.execute("""
                SELECT 1 FROM user_courses
                WHERE user_id = ? AND course_id = ?
            """, (user_id, course_id))
            existing_enrollment = await cursor.fetchone()
            logger.info(f"7 700 1318 existing_enrollment: {existing_enrollment} {course_id=}")
            if existing_enrollment:
                await message.answer("Этот курс уже активирован.", parse_mode=None)
                # Load 0 lesson
                logger.info(f"перед вызовом send_course_description: {user_id=} {course_id=}" )
                await send_course_description(user_id, course_id)

                # Generate keyboard
                course_numeric_id = await get_course_id_int(course_id)
                keyboard = get_main_menu_inline_keyboard(
                    course_numeric_id=course_numeric_id,
                    lesson_num=0,  # Для описания курса ставим урок 0
                    user_tariff=version_id,
                    homework_pending=False
                )
                await message.answer("Главное меню:", reply_markup=keyboard, parse_mode=None)
            else:
                # Активируем курс
                await conn.execute("""
                    INSERT OR REPLACE INTO user_courses (user_id, course_id, version_id, status, current_lesson, activation_date)
                    VALUES (?, ?, ?, 'active', 1, CURRENT_TIMESTAMP)
                """, (user_id, course_id, version_id))
                await conn.commit()

                await log_action(
                    user_id=user_id,
                    action_type="COURSE_ACTIVATION_BY_TEXT_CODE", # Более специфичный тип
                    course_id=course_id,
                    new_value=version_id, # version_id извлечен из course_data
                    details=f"Активирован кодом: {escape_md(message.text.strip())}"
                )

                # Load 0 lesson
                logger.info(f"перед вызовом send_course_description: {user_id=} {course_id=}")
                await send_course_description(user_id, course_id)

        async with aiosqlite.connect(DB_FILE) as conn:

            # Получаем общее количество курсов
            cursor = await conn.execute("""
                    SELECT COUNT(*) 
                    FROM user_courses 
                    WHERE user_id = ? AND status IN ('active', 'completed')
                """, (user_id,))
            total_courses = (await cursor.fetchone())[0]

            # Формируем текст кнопки с количеством
            courses_button_text = f"📚 Мои курсы ({total_courses})"
            course_numeric_id = await get_course_id_int(course_id)
            # Генерация клавиатуры
            keyboard = get_main_menu_inline_keyboard(
                course_numeric_id=course_numeric_id,
                lesson_num=0,  # Для описания курса ставим урок 0
                user_tariff=version_id,
                homework_pending=False
            )

            # Отправляем сообщение
            tariff_names = settings.get("tariff_names", {"v1": "Соло", "v2": "Группа", "v3": "VIP"})
            message_text = (
                f"Курс успешно активирован!\n"
                f"🎓 Курс: {course_name}\n"
                f"🔑 Тариф: {tariff_names.get(version_id, 'Базовый')}\n"
                f"📚 Текущий урок: 1"
            )
        await message.answer(message_text, reply_markup=keyboard, parse_mode=None)

    except Exception as e:
        logger.error(f"Общая ошибка в process_message: {e}", exc_info=True)
        await message.answer("Произошла общая ошибка. Пожалуйста, попробуйте позже.", parse_mode=None)



#  Обработчик входящего контента от пользователя
@dp.message(F.photo | F.video | F.document | F.text)
async def handle_user_content(message: types.Message):
    """Обработчик пользовательского контента для ДЗ"""
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT uc.course_id, uc.current_lesson, uc.version_id, uc.hw_status
                FROM user_courses uc
                WHERE uc.user_id = ? AND uc.status = 'active'
            """, (user_id,))
            user_data = await cursor.fetchone()

            if not user_data:
                # Нет активного курса - обрабатываем как код активации
                await handle_activation_code(message)
                return

            course_id, current_lesson, version_id, hw_status = user_data

            # Проверяем статус ДЗ
            if hw_status in ('required', 'rejected') and message.text:
                # Если ДЗ ожидается и это текст - игнорируем
                logger.info(f"Получен ненужный текст от {user_id}, игнорируем.")
                await message.answer("Текст не относится к текущему уроку, проигнорировано.", parse_mode=None)
            else:
                # Если статус ДЗ не 'required' или 'rejected', или это не текст - обрабатываем как обычно
                await handle_homework(message)

    except Exception as e:
        logger.error(f"Ошибка при обработке контента: {e}")
        await message.answer("Произошла ошибка при обработке вашего сообщения.", parse_mode=None)

#=======================Конец обработчиков текстовых сообщений=========================================

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    """Обработчик для фотографий."""
    logger.info(f"88 handle_photo  ")
    try:
        await message.answer("Фотография получена!", parse_mode=None)
    except Exception as e:
        logger.error(f"Ошибка при обработке фотографии: {e}")

@dp.message(F.video)
async def handle_video(message: types.Message):
    """Обработчик для видео."""
    logger.info(f"89 handle_video  ")
    try:
        await message.answer("Видео получено!", parse_mode=None)
    except Exception as e:
        logger.error(f"Ошибка при обработке видео: {e}")

@dp.message(F.document)
async def handle_document(message: types.Message):
    """Обработчик для документов."""
    logger.info(f"90 handle_document  ")
    try:
        await message.answer("Документ получен!", parse_mode=None)
    except Exception as e:
        logger.error(f"Ошибка при обработке документа: {e}")


@dp.message()
async def default_handler(message: types.Message):
    logger.warning(f"Получено необработанное сообщение: {message.text}")

@dp.callback_query()
async def default_callback_handler(query: types.CallbackQuery):
    logger.warning(f"Получен необработанный callback_query: {query.data}")

# ---- ФУНКЦИИ ДЛЯ УПРАВЛЕНИЯ ВЕБХУКОМ ----
async def on_startup():
    global bot, WEBHOOK_HOST_CONF, WEBHOOK_PATH_CONF, BOT_TOKEN_CONF
    # Явное указание global здесь не обязательно, если они уже определены на уровне модуля
    # и вы их только читаете

    final_webhook_path = f"{WEBHOOK_PATH_CONF.rstrip('/')}/{BOT_TOKEN_CONF}"
    webhook_url = f"{WEBHOOK_HOST_CONF.rstrip('/')}{final_webhook_path}"
    await bot.set_webhook(webhook_url, drop_pending_updates=True)
    logger.info(f"Webhook set to: {webhook_url}")



    logger.info("Запуск фоновых задач для пользователей (таймеры)...")
    async with aiosqlite.connect(DB_FILE) as conn: # DB_FILE должен быть определен
        cursor = await conn.execute("SELECT user_id FROM users")
        users_rows = await cursor.fetchall()
        for user_row in users_rows:
            user_id = user_row[0]
            # lesson_check_tasks должен быть определен глобально
            if user_id not in lesson_check_tasks or lesson_check_tasks[user_id].done():
                asyncio.create_task(start_lesson_schedule_task(user_id))
            else:
                logger.info(f"Task for user {user_id} already running or scheduled.")
    logger.info("Фоновые задачи запущены.")

    await send_startup_message(bot, ADMIN_GROUP_ID)  # <--- ВОТ ВЫЗОВ


async def on_shutdown():
    global bot
    logger.warning("Shutting down..")
    await bot.delete_webhook()
    logger.info("Webhook deleted.")

    logger.info("Cancelling background tasks...")
    if 'lesson_check_tasks' in globals() and lesson_check_tasks: # Проверка на существование
        active_tasks = [task for task in lesson_check_tasks.values() if task and not task.done()]
        if active_tasks:
            for task in active_tasks:
                task.cancel()
            # Ожидание завершения задач
            results = await asyncio.gather(*active_tasks, return_exceptions=True)
            # Логирование результатов отмены (опционально)
            for i, result in enumerate(results):
                task_id_for_log = "unknown" # Попытка найти ID задачи для лога
                try:
                    # Это сработает, если ключи - user_id, а значения - task
                    task_id_for_log = list(lesson_check_tasks.keys())[list(lesson_check_tasks.values()).index(active_tasks[i])]
                except (ValueError, IndexError):
                    pass # Не удалось найти, останется "unknown"

                if isinstance(result, asyncio.CancelledError):
                    logger.info(f"Task for ID {task_id_for_log} was cancelled successfully.")
                elif isinstance(result, Exception):
                    logger.error(f"Task for ID {task_id_for_log} raised an exception during shutdown: {result}")
    logger.info("All background tasks processed for shutdown.")
    await bot.session.close()
    logger.info("Bot session closed.")


async def main():
    # Делаем переменные модуля доступными для присваивания
    global settings, COURSE_GROUPS, dp, bot
    global BOT_TOKEN_CONF, ADMIN_IDS_CONF
    global WEBHOOK_HOST_CONF, WEBAPP_PORT_CONF, WEBAPP_HOST_CONF, WEBHOOK_PATH_CONF

    setup_logging()
    logger.info("Запуск main() в режиме вебхука...")

    load_dotenv()

    # Загрузка переменных с именами из вашего .env
    BOT_TOKEN_CONF = os.getenv("BOT_TOKEN")
    admin_ids_str = os.getenv("ADMIN_IDS")
    WEBHOOK_HOST_CONF = os.getenv("WEBHOOK_HOST")
    webapp_port_str = os.getenv("WEBAPP_PORT")
    WEBAPP_HOST_CONF = os.getenv("WEBAPP_HOST", "::") # '::' как дефолт, если не указано
    WEBHOOK_PATH_CONF = os.getenv("WEBHOOK_PATH", "/bot/") # '/bot/' как дефолт

    # Валидация обязательных переменных
    if not BOT_TOKEN_CONF:
        logger.critical("BOT_TOKEN не найден. Завершение.")
        raise ValueError("BOT_TOKEN не найден.")
    if not WEBHOOK_HOST_CONF:
        logger.critical("WEBHOOK_HOST не найден. Завершение.")
        raise ValueError("WEBHOOK_HOST не найден.")

    # Парсинг и установка значений
    if admin_ids_str:
        try:
            ADMIN_IDS_CONF = [int(admin_id.strip()) for admin_id in admin_ids_str.split(',')]
        except ValueError:
            logger.warning(f"Некорректный формат ADMIN_IDS: '{admin_ids_str}'. Оставляем пустым.")
            ADMIN_IDS_CONF = []
    else:
        ADMIN_IDS_CONF = []



    try:
        WEBAPP_PORT_CONF = int(webapp_port_str) if webapp_port_str else 8349 # Дефолт из вашего .env
    except ValueError:
        logger.warning(f"Некорректный формат WEBAPP_PORT: '{webapp_port_str}'. Устанавливаем 8349.")
        WEBAPP_PORT_CONF = 8349


    bot = Bot(
        token=BOT_TOKEN_CONF,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2)
    )
    # dp = Dispatcher() # <--- УБЕРИТЕ ЭТУ СТРОКУ

    # Регистрация хэндлеров (убедитесь, что они импортированы или определены)
    # from .handlers import register_all_my_handlers
    # register_all_my_handlers(dp)

    await init_db()
    settings = await load_settings()
    if settings and "groups" in settings: # Более безопасная проверка
        COURSE_GROUPS = list(map(int, settings.get("groups", {}).keys()))
    else:
        COURSE_GROUPS = []
        logger.warning("Настройки 'groups' не загружены или отсутствуют, COURSE_GROUPS пуст.")
    await import_settings_to_db()

    # Передаем актуальные значения в лямбду для on_startup
    # Имена аргументов в лямбде могут быть любыми, главное порядок и что они передаются в on_startup
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()

    # Формируем финальный путь для регистрации в aiohttp
    # Он должен быть таким же, как формируется в on_startup
    final_webhook_path_for_aiohttp = f"{WEBHOOK_PATH_CONF.rstrip('/')}/{BOT_TOKEN_CONF}"

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        # secret_token="YOUR_SECRET_TOKEN" # Если используется
    )
    webhook_requests_handler.register(app, path=final_webhook_path_for_aiohttp)

    logger.info(f"Зарегистрированные обработчики сообщений: {len(dp.message.handlers)}")
    logger.info(f"Зарегистрированные обработчики колбэков: {len(dp.callback_query.handlers)}")

    #Можно даже вывести их подробнее, если нужно глубоко копать:
    #for handler_obj in dp.message.handlers:
     #   logger.info(f"Message Handler: {handler_obj.callback.__name__ if hasattr(handler_obj.callback, '__name__') else handler_obj.callback}, filters: {handler_obj.filters}")

    setup_application(app, dp, bot=bot) # Передаем bot для доступа к нему через app['bot'] если нужно

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=WEBAPP_HOST_CONF, port=WEBAPP_PORT_CONF)

    try:
        await site.start()
        actual_host_log = "всех интерфейсах (IPv4/IPv6)" if WEBAPP_HOST_CONF in ('::', '0.0.0.0') else WEBAPP_HOST_CONF
        logger.info(
            f"Bot webhook server started on {actual_host_log}, port {WEBAPP_PORT_CONF}. Listening on path: {final_webhook_path_for_aiohttp}")
        await asyncio.Event().wait() # Поддерживает работу приложения
    except Exception as e:
        logger.critical(f"Не удалось запустить веб-сервер: {e}", exc_info=True)
    finally:
        logger.info("Остановка веб-сервера...")
        await runner.cleanup()
        logger.info("Веб-сервер остановлен.")

if __name__ == "__main__":
    # setup_logging() # Уже вызывается в начале main
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.warning("Bot stopped by user (KeyboardInterrupt/SystemExit)!")
    except ValueError as e: # Ловим ValueError от проверок переменных окружения
        logger.critical(f"Ошибка конфигурации: {e}")
    except Exception as e:
        # Настройка базового логирования, если setup_logging() в main не успел отработать или упал
        if not logging.getLogger().hasHandlers():
             logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')
        logging.critical(f"Критическая ошибка при запуске или работе бота: {e}", exc_info=True)


# Осознание обработчиков:
# @dp.message(Command(...)): Обработчики команд (начинаются с /).
# @dp.message(F.text): Обработчики текстовых сообщений (ловят любой текст).
# @dp.callback_query(lambda c: ...): Обработчики нажатий на кнопки (inline keyboard).
# @dp.message(lambda message: message.text.lower() in settings["activation_codes"]): Обработчик для активации курса по коду.
