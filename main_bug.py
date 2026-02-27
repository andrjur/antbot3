# -*- coding: utf-8 -*-
# AntBot v4 - Telegram bot for courses
import asyncio, logging, json, os, re, shutil, sys, locale, random
import functools, sqlite3, aiosqlite, pytz
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F, md
from aiogram.filters import Command, CommandStart, BaseFilter, CommandObject, StateFilter
from aiogram.filters.callback_data import CallbackData
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
import aiohttp
from aiohttp import web

# ---- ИМПОРТЫ ДЛЯ МОНИТОРИНГА ----
from services.metrics import (
    track_command, track_callback, track_db_operation,
    track_lesson_sent, track_homework_submission,
    init_bot_info, get_metrics_response
)
from services.monitoring import setup_metrics_endpoints
from services.health import health_check_endpoint, liveness_probe, readiness_probe


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

MAX_LOG_SIZE = 500 * 1024  # 500 kB
LOG_BACKUP_COUNT = 2


# Где-то в коде, можно после всех импортов
RACE_CONDITION_HUMOR = [
    "Опа! А я тоже хотел проверить, но ты оказался быстрее. Мое мнение совпадает с твоим 😉",
    "Эх, не успел... Но интуиция подсказывает, что ты принял верное решение.",
    "Пока я анализировал эту работу, ты уже всё сделал. Признаю, ты — молния!",
    "Я как раз закончил писать свой отзыв, а тут... Ну ладно, в этот раз твоя взяла. 👍",
    "Наши мнения разошлись, но раз ты уже нажал кнопку — последнее слово за тобой. В конце концов, кто здесь главный? 😉",
    "Согласен с твоим решением. Мыслю в том же направлении.",
    "Интересный случай. Я бы, возможно, ответил иначе, но уважаю твое решение. Будем наблюдать за студентом.",
    "О, это ДЗ уже в архиве. Переходим к следующему!",
]

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

# Установка локали для русского языка
try:
    locale.setlocale(locale.LC_ALL, 'ru_RU.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'ru_RU.utf8')
    except locale.Error:
        logger.warning("Russian locale not available, using default locale")
        pass

# == Константы и конфиг ==
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения.")
#logger.info(f"BOT_TOKEN: {BOT_TOKEN}") - дыра в безопасности

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

WEBHOOK_SECRET_PATH_CONF = os.getenv("WEBHOOK_SECRET_PATH") # Путь для секретного ключа вебхука 01-07
WEBHOOK_SECRET_TOKEN_CONF = os.getenv("WEBHOOK_SECRET_TOKEN")

# Валидация webhook переменных будет в main() после определения use_webhook
# Перенесем в main() после определения use_webhook

N8N_HOMEWORK_CHECK_WEBHOOK_URL = os.getenv("N8N_HOMEWORK_CHECK_URL")
N8N_ASK_EXPERT_WEBHOOK_URL = os.getenv("N8N_ASK_EXPERT_URL")
# Секретный ключ для аутентификации вебхуков n8n (если настроено в n8n)
N8N_WEBHOOK_SECRET = os.getenv("N8N_WEBHOOK_SECRET")
N8N_DOMAIN = os.getenv("N8N_DOMAIN")

HW_TIMEOUT_SECONDS = int(os.getenv("HW_TIMEOUT_SECONDS", "120"))

missing_lesson_warnings_sent = set()

# Базовый URL вашего бота для callback'ов от n8n
# Это WEBHOOK_HOST_CONF из вашего конфига + некий путь
BOT_CALLBACK_BASE_URL = f"{os.getenv('N8N_DOMAIN', 'https://n8n.indikov.ru/')}{os.getenv('WEBHOOK_PATH', '/bot/')}"

# В начале вашего файла main.py, после других os.getenv()
N8N_CALLBACK_SECRET = os.getenv("N8N_CALLBACK_SECRET")
if not N8N_CALLBACK_SECRET:
    logger.warning("N8N_CALLBACK_SECRET не установлен в переменных окружения! Callback-эндпоинты от n8n будут небезопасны или не будут работать, если проверка включена жестко.")
    # Можно установить значение по умолчанию для разработки, но это не рекомендуется для продакшена
    # N8N_CALLBACK_SECRET = "super_secret_callback_key_789_dev_only"
    # Или можно сделать так, чтобы без секрета бот не запускался или не регистрировал эти эндпоинты

CALLBACK_SECRET_HEADER_NAME = "X-CALLBACK-SIGNATURE" # Как вы и предложили

# Загрузка инструкции по оплате
PAYMENT_INSTRUCTIONS_TEMPLATE = os.getenv("PAYMENT_INSTRUCTIONS", "Инструкции по оплате у поддержки.")

# --- Constants ---
MAX_DB_RETRIES = 5
DB_RETRY_DELAY = 0.2  # seconds


# Initialize bot and dispatcher
bot = Bot(
    token=BOT_TOKEN
    #default=DefaultBotProperties(parse_mode=None)
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
    # course_id_str: str # ЗАМЕНЯЕМ
    course_numeric_id: int # Используем числовой ID

class RestartCourseCallback(CallbackData, prefix="restart_course"):
    # course_id_str: str # ЗАМЕНЯЕМ
    course_numeric_id: int # Используем числовой ID
    action: str

class AwaitingPaymentConfirmation(StatesGroup):
    waiting_for_activation_code_after_payment = State()

class MainMenuAction(CallbackData, prefix="main_menu"):
    action: str # "stop_course", "switch_course" (или "my_courses" как сейчас)
    course_id_numeric: int = 0 # Для действия stop_course, если нужно знать какой курс останавливаем

# Определим CallbackData для кнопок "Описание" и "Перейти к активному курсу"
class CourseDetailsCallback(CallbackData, prefix="course_details"):
    action: str
    # course_id_str: str # ЗАМЕНЯЕМ
    course_numeric_id: int # Используем числовой ID
    page: int = 1 # Для пагинации списка уроков, если нужно

class ShowActiveCourseMenuCallback(CallbackData, prefix="show_active_menu"):
    course_numeric_id: int
    lesson_num: int # Текущий урок пользователя на этом курсе


class SelectLessonForRepeatCallback(CallbackData, prefix="select_repeat"):
    course_numeric_id: int
    # current_lesson_user: int # Можно передать, чтобы подсветить текущий, но для списка всех уроков не обязательно

#27-05
class ChangeTariffCallback(CallbackData, prefix="ch_tariff"):
    course_id_str: str

#27-05
class SelectNewTariffToUpgradeCallback(CallbackData, prefix="sel_tariff_upg"):
    course_id_str: str
    new_version_id: str
    # price_difference: float # Сумма к доплате (может быть 0)
    # new_tariff_full_price: float # Полная цена нового тарифа, чтобы показать ее
    # Мы будем рассчитывать разницу на лету в обработчике,
    # а для отображения возьмем полную цену нового тарифа.
    # Для кнопки достаточно знать, на какой тариф переходим.


class UploadLessonAction(CallbackData, prefix="upload_lesson"):
    """Callback для действий при загрузке уроков"""
    action: str  # "more" - ещё контент, "next" - следующий урок, "done" - завершить
    course_id: str
    lesson_num: int


class ViewLessonCallback(CallbackData, prefix="view_lesson"):
    """Callback для просмотра содержимого урока"""
    course_id: str
    lesson_num: int


class BackToListCallback(CallbackData, prefix="back_list"):
    """Callback для возврата к списку уроков"""
    pass


class DeleteLessonPartCallback(CallbackData, prefix="delete_part"):
    """Callback для удаления части урока"""
    course_id: str
    lesson_num: int
    part_num: int
    action: str


class ConfirmCourseCreationCallback(CallbackData, prefix="confirm_course"):
    """Callback для подтверждения создания курса"""
    action: str


class LessonNavCallback(CallbackData, prefix="lesson_nav"):
    """Callback для навигации по урокам"""
    course_id: str
    lesson_num: int
    action: str  # prev, next, delete_part, add_content, manage_homework


class LessonAddContentCallback(CallbackData, prefix="lesson_add"):
    """Callback для добавления контента в урок"""
    course_id: str
    lesson_num: int
    content_type: str  # text, photo, video, video_note, document


class RepeatLessonForm(StatesGroup):
    waiting_for_lesson_number_to_repeat = State()


class AskExpertState(StatesGroup):
    waiting_for_expert_question = State()


class AddCourseFSM(StatesGroup):
    """FSM для добавления нового курса"""
    waiting_group_id = State()
    waiting_course_id = State()
    waiting_description = State()
    waiting_code1 = State()
    waiting_code2 = State()
    waiting_code3 = State()
    waiting_confirmation = State()


class AddContentFSM(StatesGroup):
    """FSM для добавления контента в урок"""
    waiting_content = State()
    course_id = State()
    lesson_num = State()
    content_type = State()
    is_homework = State()


class ManageHomeworkFSM(StatesGroup):
    """FSM для управления домашним заданием"""
    waiting_homework_content = State()
    course_id = State()
    lesson_num = State()
    action = State()  # add, edit, delete


# декоратор для обработки ошибок в БД
def db_exception_handler(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except sqlite3.OperationalError as e209:
            logger.error(f"Database is locked {func.__name__}: {e209}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("База данных заблокирована. Попробуйте позже.")
                    break
            return None
        except aiosqlite.Error as e217:
            logger.error(f"Database error in {func.__name__}: {e217}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла ошибка при работе с базой данных.")
                    break
            return None
        except Exception as e225:
            logger.error(f"Unexpected error in {func.__name__}: {e225}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла неизвестная ошибка.")
                    break
            return None
    return wrapper


# В вашем файле main.py, где-нибудь перед определением обработчиков вебхуков
from functools import wraps
from aiohttp import web


# logger уже должен быть определен

# N8N_CALLBACK_SECRET и CALLBACK_SECRET_HEADER_NAME определены выше

def require_n8n_secret(handler):
    @wraps(handler)
    async def wrapper(request: web.Request):
        # Проверяем, установлен ли секрет в конфигурации бота
        if not N8N_CALLBACK_SECRET:
            logger.error(
                f"N8N_CALLBACK_SECRET не сконфигурирован на стороне бота. Пропускаю проверку для эндпоинта {request.path}, но это НЕБЕЗОПАСНО.")
            # В продакшене здесь лучше возвращать 500 Internal Server Error или не регистрировать эндпоинт вообще
            # return web.Response(text="Server configuration error: Callback secret not set", status=500)
            # Пока для тестирования, если секрет не задан, можем пропустить проверку (но это плохо для безопасности)
            # Для большей безопасности, если секрет не задан, лучше отклонять запрос:
            # logger.critical("N8N_CALLBACK_SECRET не установлен! Невозможно проверить callback.")
            # return web.Response(text="Internal Server Error: Callback security not configured", status=500)

        secret_from_request = request.headers.get(CALLBACK_SECRET_HEADER_NAME)

        if not secret_from_request:
            logger.warning(f"Callback от n8n на {request.path} БЕЗ секрета. IP: {request.remote}. Запрос отклонен.")
            return web.Response(text="Forbidden: Missing secret header", status=403)  # 403 Forbidden

        if secret_from_request != N8N_CALLBACK_SECRET:
            logger.warning(
                f"Callback от n8n на {request.path} с НЕВЕРНЫМ секретом. IP: {request.remote}. Запрос отклонен. Получен: '{secret_from_request[:10]}...'")  # Логируем только часть секрета на всякий случай
            return web.Response(text="Forbidden: Invalid secret", status=403)  # 403 Forbidden

        logger.info(f"Callback от n8n на {request.path} с верным секретом. IP: {request.remote}. Обработка...")
        return await handler(request)

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
                    version_price = data.get("price", 0)

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
    except Exception as e265:
        logger.error(f"Ошибка при заполнении таблицы course_versions: {e265}")


async def create_default_settings():
    """Создает файл settings.json с настройками по умолчанию."""
    default_settings = {
        "message_interval": 12,
        "tariff_names": {
            "v1": "Соло",
            "v2": "с проверкой",
            "v3": "Премиум"
        },
        "groups": {
            "-1002549199868": "base"
        },
        "activation_codes": {
            "base1": {"course": "base", "version": "v1"},
            "base2": {"course": "base", "version": "v2"},
            "base3": {"course": "base", "version": "v3"}
        },
        "course_descriptions": {}
    }
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(default_settings, f, ensure_ascii=False, indent=4)
        logger.info(f"✅ Создан новый файл настроек: {SETTINGS_FILE}")
    except Exception as e:
        logger.error(f"❌ Ошибка при создании файла настроек: {e}")
    return default_settings


async def load_settings():
    """Загружает настройки из файла settings.json и заполняет таблицу course_versions."""
    logger.info(f"333444 load_settings ")
    
    # Если settings.json существует как директория - удаляем её и создаем файл
    if os.path.isdir(SETTINGS_FILE):
        logger.warning(f"⚠️ {SETTINGS_FILE} существует как директория, удаляю и создаю файл...")
        try:
            import shutil
            shutil.rmtree(SETTINGS_FILE)
            logger.info(f"✅ Директория {SETTINGS_FILE} удалена")
            # Сразу создаем дефолтный файл
            return await create_default_settings()
        except Exception as e:
            logger.error(f"❌ Ошибка при удалении директории {SETTINGS_FILE}: {e}")
            # Возвращаем дефолтные настройки даже если не удалось удалить
            return await create_default_settings()
    
    # Пробуем открыть файл
    if os.path.exists(SETTINGS_FILE) and os.path.isfile(SETTINGS_FILE):
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
            return await create_default_settings()
        except Exception as e:
            logger.error(f"❌ Ошибка при чтении файла {SETTINGS_FILE}: {e}")
            return await create_default_settings()
    else:
        logger.warning(f"Файл настроек {SETTINGS_FILE} не найден, создаю новый с настройками по умолчанию.")
        return await create_default_settings()


async def cleanup_orphaned_courses(settings_data: dict) -> tuple[int, list[str]]:
    """
    Удаляет активные записи из user_courses для курсов, которых нет в settings.json.
    Возвращает (количество удаленных записей, список курсов).
    """
    valid_courses = set(settings_data.get("groups", {}).values())
    deleted_count = 0
    removed_courses = []
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем все активные записи
            cursor = await conn.execute(
                "SELECT DISTINCT user_id, course_id FROM user_courses WHERE status = 'active'"
            )
            active_courses = await cursor.fetchall()
            
            for user_id, course_id in active_courses:
                if course_id not in valid_courses:
                    # Удаляем или деактивируем запись
                    await conn.execute(
                        "DELETE FROM user_courses WHERE user_id = ? AND course_id = ? AND status = 'active'",
                        (user_id, course_id)
                    )
                    deleted_count += 1
                    if course_id not in removed_courses:
                        removed_courses.append(course_id)
                    logger.info(f"🧹 Удалена активная запись: user_id={user_id}, course_id={course_id} (курс недоступен)")
            
            await conn.commit()
            
        if deleted_count > 0:
            logger.info(f"🧹 Очищено {deleted_count} записей для недоступных курсов: {removed_courses}")
        
        return deleted_count, removed_courses
        
    except Exception as e:
        logger.error(f"❌ Ошибка при очистке осиротевших курсов: {e}")
        return 0, []


settings=dict() # делаем глобальный пустой словарь

COURSE_GROUPS = []

# Глобальный словарь для блокировки ДЗ, которые редактируются админами
HOMEWORK_BEING_PROCESSED = {}

# Глобальная переменная для хранения стека уроков
lesson_stack = {}

# Глобальная переменная для хранения информации о последнем сообщении в канале
last_message_info = {}

user_support_state = {}

# Переменные для хранения задач и времени последней отправки статистики
lesson_check_tasks = {}
last_stats_sent = None # 14-04 todo нафига

# Set для отслеживания ДЗ, отправленных на n8n (для приоритета админа)
homework_sent_to_n8n = set()

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
    except Exception as e320:
        logger.error(f"Ошибка при проверке активности курса: {e320}")
        return False

# 14-04 todo нафига. use get_user_active_courses. get_user_active_courses and is_course_active
async def get_user_courses(user_id: int) -> list:
    """Получает список всех курсов пользователя."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT course_id, status FROM user_courses WHERE user_id = ?", (user_id,))
            rows = await cursor.fetchall()
            return rows
    except Exception as e332:
        logger.error(f"Ошибка при получении курсов пользователя: {e332}")
        return []

# course_numeric_id = await get_course_id_int(course_id)
async def get_course_id_int(course_id: str) -> int:
    """Получает название курса по ID."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT id FROM courses WHERE course_id = ?", (course_id,))
            result = await cursor.fetchone()
            if result:
                return result[0]
            else:
                logger.warning(f"Курс {course_id} не найден в базе")
                return 0
    except Exception as e349:
        logger.error(f"Ошибка при получении course_id курса: {e349}")
        return 0

async def get_course_id_str(course_numeric_id: int) -> str:
    """Получает название курса по ID."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT course_id FROM courses WHERE id = ?", (course_numeric_id,))
            result = await cursor.fetchone()
            if result:
                return result[0]
            else:
                logger.warning(f"Курс с ID {course_numeric_id} не найден в базе")
                return "Неизвестный курс"
    except Exception as e366:
        logger.error(f"Ошибка при получении course_id курса: {e366}")
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
    except Exception as e381:
        logger.error(f"Ошибка при получении названия курса: {e381}")
        return "Неизвестный курс"

# 14-04
async def is_valid_activation_code(code: str) -> bool:
    """Проверяет, существует ли код активации в базе данных."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT 1 FROM course_activation_codes WHERE code_word = ?", (code,))
            result = await cursor.fetchone()
            return result is not None
    except Exception as e393:
        logger.error(f"Ошибка при проверке кода активации: {e393}")
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
            
            # 0. ПРОВЕРКА: есть ли уроки в курсе
            cursor_lessons = await conn.execute(
                "SELECT COUNT(*) FROM group_messages WHERE course_id = ? AND lesson_num > 0",
                (new_course_id,)
            )
            lessons_count = (await cursor_lessons.fetchone())[0]
            
            if lessons_count == 0:
                logger.error(
                    f"⛔ БЛОКИРОВКА активации: курс '{new_course_id}' пустой (0 уроков). "
                    f"Сначала загрузите уроки через репост в админ-группу."
                )
                return False, (
                    f"❌ Курс «{escape_md(await get_course_title(new_course_id))}» ещё не готов к запуску.\n"
                    f"В нём пока нет уроков. Обратитесь к администратору."
                )
            
            course_title = await get_course_title(new_course_id)  # Получаем название курса
            new_tariff_name = settings.get("tariff_names", {}).get(new_version_id, f"Тариф {new_version_id}")

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

            activation_log_details = user_message = ""

            if existing_user_course_records:
                # У пользователя уже есть записи для этого курса.
                # Найдем активную запись, если она есть.
                active_record = next((r for r in existing_user_course_records if r[1] == 'active'), None)

                if active_record:
                    current_active_version_id, _, current_active_lesson = active_record
                    current_active_tariff_name = settings.get("tariff_names", {}).get(current_active_version_id,
                                                                                      f"Тариф {current_active_version_id}")

                    if current_active_version_id == new_version_id:
                        # СБРОС hw_status даже при активации того же тарифа!
                        await conn.execute(
                            "UPDATE user_courses SET hw_status = 'none', hw_type = NULL WHERE user_id = ? AND course_id = ?",
                            (user_id, new_course_id)
                        )
                        user_message = f"✅ Курс «{escape_md(course_title)}» с тарифом «{escape_md(new_tariff_name)}» у вас уже активен.\n\n🔄 Прогресс ДЗ сброшен."
                        activation_log_details = f"Повторная активация того же тарифа '{new_version_id}' для курса '{new_course_id}'. Курс уже активен, hw_status сброшен."
                        logger.info(activation_log_details)
                        await conn.commit()
                        # Запускаем шедулер на всякий случай, если он был остановлен
                        await start_lesson_schedule_task(user_id)
                        return True, user_message  # Считаем успешной, т.к. курс уже активен

                    else:
                        # Активен другой тариф! Обновляем.
                        logger.info(
                            f"Смена тарифа для user_id={user_id}, course_id='{new_course_id}' с '{current_active_version_id}' на '{new_version_id}'.")
                        # СБРОС hw_status для ВСЕХ записей этого курса перед сменой тарифа
                        await conn.execute(
                            "UPDATE user_courses SET hw_status = 'none', hw_type = NULL WHERE user_id = ? AND course_id = ?",
                            (user_id, new_course_id)
                        )
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

                        user_message = (f"✅ Тариф для курса «{escape_md(course_title)}» успешно изменен!\n"
                                        f"Раньше был: «{escape_md(current_active_tariff_name)}».\n"
                                        f"Теперь активен: «{escape_md(new_tariff_name)}».\n"
                                        "Прогресс по курсу начнется заново.")
                        activation_log_details = f"Смена тарифа для курса '{new_course_id}' с '{current_active_version_id}' на '{new_version_id}'. Прогресс сброшен."
                else:
                    # Есть записи, но ни одна не активна (все inactive или completed)
                    logger.info(
                        f"Повторная активация курса '{new_course_id}' с тарифом '{new_version_id}' для user_id={user_id}. Предыдущие статусы были неактивны.")
                    # СБРОС hw_status для ВСЕХ записей этого курса
                    await conn.execute(
                        "UPDATE user_courses SET hw_status = 'none', hw_type = NULL WHERE user_id = ? AND course_id = ?",
                        (user_id, new_course_id)
                    )
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
                    user_message = f"✅ Курс «{escape_md(course_title)}» с тарифом «{escape_md(new_tariff_name)}» успешно активирован (или возобновлен)! Прогресс начнется заново."
                    activation_log_details = f"Активирован/возобновлен курс '{new_course_id}' с тарифом '{new_version_id}'. Прогресс сброшен."
            else:
                # Это первая активация этого курса для пользователя
                logger.info(
                    f"Первая активация курса '{new_course_id}' с тарифом '{new_version_id}' для user_id={user_id}.")
                await conn.execute("""
                    INSERT INTO user_courses (user_id, course_id, version_id, status, current_lesson, activation_date, first_lesson_sent_time, last_lesson_sent_time, level, hw_status, hw_type)
                    VALUES (?, ?, ?, 'active', 0, ?, ?, ?, 1, 'none', NULL)
                """, (user_id, new_course_id, new_version_id, now_utc_str, now_utc_str, now_utc_str))
                user_message = f"✅ Курс «{escape_md(course_title)}» с тарифом «{escape_md(new_tariff_name)}» успешно активирован!"
                activation_log_details = f"Курс '{new_course_id}' (тариф '{new_version_id}') успешно активирован."
            logger.info(f"{activation_log_details=}  {user_message=}" )
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
                    await bot.send_message(ADMIN_GROUP_ID, admin_notification, parse_mode=None)
                except Exception as e_admin_notify:
                    logger.error(f"Не удалось отправить уведомление админам об активации: {e_admin_notify}")

            await start_lesson_schedule_task(user_id)
            return True, user_message

    except Exception as e566:
        logger.error(f"Ошибка при активации курса (код {activation_code}) для user_id={user_id}: {e566}", exc_info=True)
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
    except Exception as e586:
        logger.error(f"Ошибка при деактивации курса: {e586}")
        return False, "⚠️ Произошла ошибка при деактивации курса. Попробуйте позже."


@db_exception_handler
async def check_lesson_schedule(user_id: int, hours=24, minutes=0):
    """Проверяет расписание уроков и отправляет урок, если пришло время."""
    logger.debug(f"Проверка расписания для user_id={user_id}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor_user_data = await conn.execute("""
                SELECT course_id, current_lesson, version_id, 
                       first_lesson_sent_time, last_lesson_sent_time, 
                       hw_status, last_menu_message_id, status, level
                FROM user_courses 
                WHERE user_id = ? AND status = 'active' 
            """, (user_id,))
            user_data = await cursor_user_data.fetchone()

            if not user_data:
                logger.debug(f"У пользователя {user_id} нет активных курсов")
                cursor_active_count = await conn.execute(
                    "SELECT COUNT(*) FROM user_courses WHERE user_id = ? AND status = 'active'", (user_id,))
                active_count_data = await cursor_active_count.fetchone()
                if active_count_data and active_count_data[0] == 0:
                    await stop_lesson_schedule_task(user_id)
                return

            (course_id, current_lesson_db, version_id,
             first_sent_time_str, last_sent_time_str,
             hw_status, menu_message_id, course_status_db, user_course_level) = user_data

            if hw_status not in ('approved', 'not_required', 'none'):
                return

            # ПРОВЕРКА ТЕСТ-РЕЖИМА: если админ в тест-режиме - интервал 5 минут
            if user_id in ADMIN_TEST_MODE:
                message_interval_hours = TEST_MODE_INTERVAL_MINUTES / 60.0  # 5 минут в часах
                logger.debug(f"Тест-режим для {user_id}: интервал {message_interval_hours} ч ({TEST_MODE_INTERVAL_MINUTES} минут)")
            else:
                message_interval_hours = float(settings.get("message_interval", 24.0))

            if last_sent_time_str:

                if not first_sent_time_str:
                    logger.error(f"Отсутствует first_lesson_sent_time для user_id={user_id}")
                    return

                try:
                    first_sent_naive_utc = datetime.strptime(first_sent_time_str, '%Y-%m-%d %H:%M:%S')
                    first_sent_aware_utc = pytz.utc.localize(first_sent_naive_utc)

                    next_lesson_event_time_utc = first_sent_aware_utc + timedelta(
                        hours=message_interval_hours) * current_lesson_db
                    current_time_aware_utc = datetime.now(pytz.utc)
                    time_left = next_lesson_event_time_utc - current_time_aware_utc

                    if time_left.total_seconds() > 10 and not (hours == 0 and minutes == 0):
                        display_next_lesson_time = await get_next_lesson_time(user_id, course_id, current_lesson_db)
                        status_time_message = f"⏳ Следующий урок: {display_next_lesson_time}\n"

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
                                    parse_mode=None
                                )
                                logger.debug(f"Меню обновлено для user_id={user_id}")
                            except TelegramBadRequest as e_edit:
                                if "message to edit not found" in str(e_edit).lower() or \
                                        "message is not modified" in str(e_edit).lower():
                                    await conn.execute(
                                        "UPDATE user_courses SET last_menu_message_id = NULL WHERE user_id = ? AND course_id = ?",
                                        (user_id, course_id)
                                    )
                                    await conn.commit()
                            except Exception as e_update_menu:
                                logger.error(f"Ошибка при обновлении меню для user {user_id}: {e_update_menu}")
                        else:
                            logger.debug(f"Нет menu_message_id для user_id={user_id}")

                    else:  # Время пришло отправлять следующий урок
                        next_lesson_to_send = current_lesson_db + 1
                        logger.info(f"Отправка урока {next_lesson_to_send} для user_id={user_id}")
                        await send_lesson_to_user(user_id, course_id, next_lesson_to_send)

                except ValueError as e_parse:
                    logger.error(f"Ошибка преобразования времени: {e_parse}")
                    await bot.send_message(user_id, "📛 Ошибка времени урока! Свяжитесь с поддержкой.", parse_mode=None)
                    return
                except Exception as e_time_calc:
                    logger.error(f"Ошибка в расчете времени для user_id={user_id}: {e_time_calc}")
                    await bot.send_message(user_id, "📛 Ошибка при расчете времени урока!", parse_mode=None)
                    return

            else:  # last_sent_time_str отсутствует
                if current_lesson_db == 0 and first_sent_time_str:
                    logger.info(f"Отправка первого урока для user_id={user_id}")
                    await send_lesson_to_user(user_id, course_id, 1)
                elif current_lesson_db == 0 and not first_sent_time_str:
                    logger.error(f"Нет first_sent_time_str для user_id={user_id}")
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
        logger.debug(f"🏁 Функция check_lesson_schedule для user_id={user_id} полностью завершена.")




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
            except Exception as e787:
                logger.error(f"❌ Не удалось отправить статистику админам: {e787}")

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
    except Exception as e817:
        logger.error(f"Ошибка при сборе статистики: {e817}")

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


async def check_pending_homework_timeout():
    """
    Периодически проверяет ДЗ, которые ожидают проверки более HW_TIMEOUT_SECONDS секунд,
    и отправляет их на n8n webhook если админ не ответил.
    """
    global HW_TIMEOUT_SECONDS
    logger.info("=" * 80)
    logger.info(f"check_pending_homework_timeout START")
    logger.info(f"HW_TIMEOUT_SECONDS={HW_TIMEOUT_SECONDS}")
    logger.info(f"N8N_HOMEWORK_CHECK_WEBHOOK_URL={N8N_HOMEWORK_CHECK_WEBHOOK_URL}")
    logger.info("=" * 80)
    
    while True:
        try:
            await asyncio.sleep(60)
            
            logger.info(f"check_pending_homework_timeout: проверка через 60 сек")

            if not N8N_HOMEWORK_CHECK_WEBHOOK_URL:
                logger.info(f"check_pending_homework_timeout: N8N_WEBHOOK не настроен, пропускаем")
                continue

            async with aiosqlite.connect(DB_FILE) as conn:
                cutoff_time = datetime.now(pytz.utc) - timedelta(seconds=HW_TIMEOUT_SECONDS)
                cutoff_time_str = cutoff_time.strftime('%Y-%m-%d %H:%M:%S')
                
                logger.info(f"check_pending_homework_timeout: cutoff_time_str={cutoff_time_str}")

                cursor = await conn.execute('''
                    SELECT admin_message_id, admin_chat_id, student_user_id,
                           course_numeric_id, lesson_num, student_message_id, created_at
                    FROM pending_admin_homework
                    WHERE created_at < ?
                ''', (cutoff_time_str,))

                pending_rows = await cursor.fetchall()
                
                logger.info(f"check_pending_homework_timeout: найдено {len(pending_rows)} pending ДЗ")

                for row in pending_rows:
                    admin_msg_id, admin_chat_id, student_user_id, course_numeric_id, lesson_num, student_msg_id, created_at = row
                    
                    logger.info(f"check_pending_homework_timeout: pending ДЗ admin_msg_id={admin_msg_id}, student_user_id={student_user_id}, course_numeric_id={course_numeric_id}, lesson_num={lesson_num}, created_at={created_at}")

                    if admin_msg_id in homework_sent_to_n8n:
                        logger.info(f"check_pending_homework_timeout: ДЗ #{admin_msg_id} уже отправлено на n8n, пропускаем")
                        continue
                    
                    cursor_student = await conn.execute(
                        "SELECT username, first_name FROM users WHERE user_id = ?",
                        (student_user_id,)
                    )
                    student_info = await cursor_student.fetchone()
                    student_name = f"{student_info[1]} (@{student_info[0]})" if student_info else f"User {student_user_id}"
                    
                    course_id_str = await get_course_id_str(course_numeric_id)
                    course_title = await get_course_title(course_id_str)
                    
                    # Получаем текст задания и ДЗ
                    async with aiosqlite.connect(DB_FILE) as conn2:
                        cursor_lesson = await conn2.execute(
                            """SELECT text FROM group_messages 
                               WHERE course_id = ? AND lesson_num = ? AND is_homework = 0 AND content_type = 'text'
                               ORDER BY id ASC""",
                            (course_id_str, lesson_num)
                        )
                        lesson_parts = await cursor_lesson.fetchall()
                        lesson_description = "\n".join([row[0] for row in lesson_parts if row[0]])
                        
                        cursor_hw_type = await conn2.execute(
                            """SELECT hw_type FROM group_messages 
                               WHERE course_id = ? AND lesson_num = ? AND is_homework = 1 LIMIT 1""",
                            (course_id_str, lesson_num)
                        )
                        hw_type_row = await cursor_hw_type.fetchone()
                        expected_hw_type = hw_type_row[0] if hw_type_row else "any"
                    
                    callback_base = f"{WEBHOOK_HOST_CONF.rstrip('/')}{WEBHOOK_PATH_CONF.rstrip('/')}"
                    
                    payload = {
                        "action": "check_homework_timeout",
                        "student_user_id": student_user_id,
                        "student_name": student_name,
                        "course_numeric_id": course_numeric_id,
                        "course_id": course_id_str,
                        "course_title": course_title,
                        "lesson_num": lesson_num,
                        "lesson_assignment_description": lesson_description,
                        "expected_homework_type": expected_hw_type,
                        "admin_message_id": admin_msg_id,
                        "admin_group_id": ADMIN_GROUP_ID,
                        "student_message_id": student_msg_id,
                        "callback_webhook_url_result": f"{callback_base}/n8n_hw_result",
                        "telegram_bot_token": BOT_TOKEN,
                        "timeout_seconds": HW_TIMEOUT_SECONDS
                    }
                    
                    success, response = await send_data_to_n8n(N8N_HOMEWORK_CHECK_WEBHOOK_URL, payload)
                    logger.info(f"n8n response: success={success}, response={response[:200] if response else 'None'}")
                    
                    homework_sent_to_n8n.add(admin_msg_id)
                    
                    if success:
                        logger.info(f"ДЗ #{admin_msg_id} отправлено на n8n")
                        try:
                            await bot.edit_message_caption(
                                chat_id=admin_chat_id,
                                message_id=admin_msg_id,
                                caption=f"🤖 ИИ-ассистент проверяет ДЗ...\n\n{student_name}\n\n⏳ Ожидайте результат",
                                reply_markup=None
                            )
                        except Exception as e_edit:
                            logger.debug(f"Не удалось обновить caption: {e_edit}")
                    else:
                        logger.error(f"Ошибка отправки ДЗ #{admin_msg_id} на n8n: {response}")
                
        except Exception as e:
            logger.error(f"Ошибка в check_pending_homework_timeout: {e}")


def save_settings(settings_s):
    """Сохраняет настройки в файл settings.json."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings_s, f, ensure_ascii=False, indent=4)
        logger.info("Настройки успешно сохранены.")
    except Exception as e849:
        logger.error(f"Ошибка при сохранении настроек: {e849}")

@db_exception_handler
async def process_add_course_to_db(course_id, group_id, code1, code2, code3, description=""):
    """Добавляет информацию о курсе и кодах активации в базу данных."""
    logger.info(f"3338883333 process_add_course_to_db: course_id={course_id}, description={description[:50] if description else 'None'}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем максимальный id из таблицы, если таблица пуста — ставим 1000
            cursor = await conn.execute("SELECT MAX(id) FROM courses")
            row = await cursor.fetchone()
            max_id = row[0] if row[0] is not None else 999  # если таблица пуста, начнем с 1000
            new_id = max_id + 1
            
            course_title = f"{course_id} basic"
            course_description = description if description else f"Описание для {course_id}"

            await conn.execute("""
                INSERT OR REPLACE INTO courses (id, course_id, group_id, title, description)
                VALUES (?, ?, ?, ?, ?)
            """, (new_id, course_id, group_id, course_title, course_description))
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
                        code_info.get("price", 0)
                    ))

            await conn.commit()
            logger.info(f"Курс {course_id} успешно добавлен в базу данных.")

            await update_settings_file()  # Обновляем файл settings.json
            await backup_settings_file()  # Создаем бэкап файла settings.json

    except Exception as e892:
        logger.error(f"Ошибка при добавлении курса {course_id} в базу данных: {e892}")


async def backup_settings_file():
    """Создает бэкап файла settings.json."""
    try:
        if not os.path.isfile("settings.json"):
            logger.warning("⚠️ settings.json не существует или является директорией, пропускаю бэкап")
            return

        timestamp = datetime.now(pytz.utc).strftime("%Y-%m-%d_%H-%M-%S")
        backup_file = f"settings_{timestamp}.json"
        shutil.copy("settings.json", backup_file)
        logger.info(f"Создан бэкап файла settings.json: {backup_file}")

    except Exception as e904:
        logger.error(f"Ошибка при создании бэкапа файла settings.json: {e904}")


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
            await conn.execute("PRAGMA busy_timeout = 5000")  #
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
                CREATE TABLE IF NOT EXISTS task_pool (
                    id INTEGER PRIMARY KEY, -- Уникальный ID задания (например, 201, 202...)
                    task_category TEXT NOT NULL, -- Категория: 'Управление вниманием', 'Работа с эмоциями' и т.д.
                    task_text TEXT NOT NULL, -- Полный текст задания
                    report_format TEXT, -- Примерный формат отчета: "Сделано. Мой уровень тревоги [...]"
                    karma_points INTEGER DEFAULT 1, -- Количество "Карма Баллов" за выполнение
                    is_repeatable BOOLEAN DEFAULT FALSE, -- <-- НОВОЕ ПОЛЕ
                    is_active BOOLEAN DEFAULT TRUE -- Флаг, чтобы временно отключать задания
                )
            ''')
            await conn.commit()

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS task_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- Уникальный номер, который вы дали в списке (1-214)
                    task_num INTEGER UNIQUE,                -- Категория, как в вашем списке
                    category TEXT NOT NULL,                 -- Полное описание задания
                    title TEXT NOT NULL ,                 -- 
                    description TEXT NOT NULL,              -- Стоимость в Карма-Баллах
                    karma_points INTEGER NOT NULL,          -- Пример отчета, чтобы ИИ знал, чего ждать
                    report_example TEXT 
                )
            ''')

            await conn.commit()

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_completed_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    task_id INTEGER NOT NULL,
                    completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    user_report TEXT, -- Можно сохранять отчет пользователя
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (task_id) REFERENCES task_pool(id)
                )
            ''')
            await conn.commit()

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_karma (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            await conn.commit()

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS user_karma_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    points_changed INTEGER NOT NULL, -- Может быть и отрицательным
                    reason TEXT, -- 'Урок 5 курса "база"', 'Задание #34', 'Подарок от админа'
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await conn.commit()

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS marathon_flow (
                    step_number INTEGER PRIMARY KEY, -- 1, 2, 3... Порядок шагов в марафоне
                    step_type TEXT NOT NULL, -- 'TASK' или 'INFO_MESSAGE'
                    task_category TEXT, -- 'Управление вниманием', 'Восстановление энергии' и т.д.
                    is_ai_choice BOOLEAN DEFAULT TRUE, -- ИИ выбирает задание из категории?
                    info_text TEXT -- Текст для шагов типа 'INFO_MESSAGE'
                )
            ''')
            await conn.commit()



            await conn.execute('''
                CREATE TABLE IF NOT EXISTS courses (
                    course_id TEXT PRIMARY KEY,
                    id INTEGER,
                    group_id TEXT,
                    title TEXT NOT NULL COLLATE NOCASE,
                    course_type TEXT DEFAULT 'LESSON_BASED', -- или  бывает (новые) `TASK_BASED`.
                    message_interval REAL NOT NULL DEFAULT 24,
                    description TEXT COLLATE NOCASE,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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

            # сообщения с ДЗ, отправленных в админ-группу и ожидающих проверки.
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS pending_admin_homework (
                    admin_message_id INTEGER PRIMARY KEY, -- ID сообщения В АДМИН-ГРУППЕ
                    admin_chat_id INTEGER NOT NULL,       -- ID админ-группы (на всякий случай, если их несколько)
                    student_user_id INTEGER NOT NULL,
                    course_numeric_id INTEGER NOT NULL,
                    lesson_num INTEGER NOT NULL,
                    student_message_id INTEGER,           -- ID исходного сообщения студента с ДЗ (опционально, но полезно)
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (student_user_id) REFERENCES users(user_id),
                    FOREIGN KEY (course_numeric_id) REFERENCES courses(id) -- или courses(course_id) если id числовой
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

            # Получаем список созданных таблиц
            cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = await cursor.fetchall()
            table_names = [t[0] for t in tables]
            logger.info(f"✅ База данных инициализирована: {len(table_names)} таблиц")
            logger.debug(f"   Таблицы: {', '.join(table_names)}")
    except Exception as e1095:
        logger.error(f"❌ Ошибка инициализации базы данных: {e1095}")
        raise  # Allows bot to exit on startup if database cannot be initialized


# ================== ФУНКЦИИ АДМИНИСТРИРОВАНИЯ ==================

async def is_super_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь суперадмином (из .env ADMIN_IDS)"""
    return user_id in ADMIN_IDS_CONF


async def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом.
    - Суперадмин из ADMIN_IDS (.env)
    - Участник группы ADMIN_GROUP_ID
    """
    if user_id in ADMIN_IDS_CONF:
        return True
    
    # Проверяем участие в админ-группе
    if ADMIN_GROUP_ID:
        try:
            member = await bot.get_chat_member(ADMIN_GROUP_ID, user_id)
            if member.status in ['member', 'administrator', 'creator']:
                return True
        except Exception as e:
            logger.debug(f"Не удалось проверить членство в группе: {e}")
    
    return False


async def get_admin_type(user_id: int) -> str:
    """Возвращает тип админа: 'super', 'group', или None"""
    if user_id in ADMIN_IDS_CONF:
        return "super"
    
    if ADMIN_GROUP_ID:
        try:
            member = await bot.get_chat_member(ADMIN_GROUP_ID, user_id)
            if member.status in ['member', 'administrator', 'creator']:
                return "group"
        except:
            pass
    
    return None


# ================== КОНЕЦ ФУНКЦИЙ АДМИНИСТРИРОВАНИЯ ==================


async def send_data_to_n8n(n8n_webhook_url: str, payload: dict):
    async with aiohttp.ClientSession() as session:
        headers = {'Content-Type': 'application/json'}
        if N8N_WEBHOOK_SECRET:
            headers['X-N8N-Signature'] = N8N_WEBHOOK_SECRET

        logger.info(f"Отправка данных в n8n: URL={n8n_webhook_url}, action={payload.get('action', 'unknown')}, callback_url={payload.get('callback_webhook_url_result', 'N/A')}")
        try:
            async with session.post(n8n_webhook_url, json=payload, headers=headers, timeout=30) as response:
                response_text = await response.text()
                if response.status == 200 or response.status == 202:
                    logger.info(f"n8n OK. Статус: {response.status}")
                    return True, response_text
                else:
                    if '<html' in response_text.lower():
                        logger.error(f"n8n вернул HTML (возможно Cloudflare). Статус: {response.status}")
                    else:
                        logger.error(f"Ошибка n8n. Статус: {response.status}. Ответ: {response_text[:200]}")
                    return False, f"HTTP {response.status}"
        except aiohttp.ClientConnectorError as e_conn:
            logger.error(f"Ошибка соединения с n8n: {e_conn}")
            return False, str(e_conn)
        except asyncio.TimeoutError:
            logger.error(f"Тайм-аут при отправке в n8n на URL: {n8n_webhook_url}")
            return False, "Timeout error"
        except Exception as e_general:
            logger.error(f"Непредвиденная ошибка при отправке в n8n: {e_general}")
            return False, str(e_general)


@dp.callback_query(F.data == "ask_expert_question")  # Или ваша CallbackData
async def cb_ask_expert_start(query: types.CallbackQuery, state: FSMContext):
    await query.message.answer(escape_md("Напишите ваш вопрос эксперту или ИИ:"))
    await state.set_state(AskExpertState.waiting_for_expert_question)
    await query.answer()


@dp.message(AskExpertState.waiting_for_expert_question, F.text)
async def process_expert_question(message: types.Message, state: FSMContext):
    await state.clear()
    user_full_name = message.from_user.full_name
    user_username = message.from_user.username

    payload_for_n8n_expert = {
        "action": "ask_expert",
        "user_id": message.from_user.id,
        "user_fullname": user_full_name,
        "username": user_username,
        "question_text": message.text,
        "admin_group_id": ADMIN_GROUP_ID,  # Куда переслать вопрос, если ИИ не справится
        "original_user_message_id": message.message_id,  # ID вопроса от пользователя
        "callback_webhook_url_answer": f"{BOT_CALLBACK_BASE_URL}/n8n_expert_answer/{message.from_user.id}/{message.message_id}"
    }

    if N8N_ASK_EXPERT_WEBHOOK_URL:
        # Неблокирующий вызов
        asyncio.create_task(send_data_to_n8n(N8N_ASK_EXPERT_WEBHOOK_URL, payload_for_n8n_expert))
        await message.reply(escape_md("Ваш вопрос отправлен. Пожалуйста, ожидайте ответа."))
    else:
        await message.reply(escape_md("Сервис ответов на вопросы временно недоступен."))


#============ зона n8n ========================
@require_n8n_secret
async def handle_n8n_hw_approval(request: web.Request) -> web.Response:
    """Получает от ИИ результат проверки ДЗ. да/нет и подробная причина."""
    # Достаем bot из request.app.
    # Указываем тип для подсказок IDE.
    #bot: Bot = request.app['bot']
    logger.info('Зашли в handle_n8n_hw_approval')  # <-- НОВЫЙ ЛОГ
    try:
        data = await request.json()
        logger.info(f"Получен callback от n8n (HW Approval): {data}")

        # --- НАДЕЖНАЯ ОЧИСТКА И ПРЕОБРАЗОВАНИЕ ДАННЫХ ---
        student_user_id = int(str(data.get("student_user_id", "0")).strip())
        course_numeric_id = int(str(data.get("course_numeric_id", "0")).strip())
        lesson_num = int(str(data.get("lesson_num", "0")).strip())
        original_admin_message_id = int(str(data.get("original_admin_message_id", "0")).strip())
        feedback_text = str(data.get("feedback_text", "")).strip()
        is_approved_raw = data.get("is_approved", "false")  # Получаем как есть
        is_approved = str(is_approved_raw).strip().lower() == 'true'

        logger.info(
            f"Данные после очистки: user={student_user_id}, course={course_numeric_id}, lesson={lesson_num}, approved={is_approved}")

        # ===== НОВАЯ ЛОГИКА: ПРОВЕРКА ЗАМКА И ОТПРАВКА СОВЕТА ===== todo 29-06
        # ===== ПРОВЕРКА БЛОКИРОВКИ =====
        if original_admin_message_id in HOMEWORK_BEING_PROCESSED:
            admin_id_who_locked = HOMEWORK_BEING_PROCESSED[original_admin_message_id]
            logger.warning(
                f"ДЗ (admin_msg_id: {original_admin_message_id}) уже обрабатывается админом {admin_id_who_locked}. Отправляем ответ ИИ как подсказку.")

            feedback_from_ai = data.get("feedback_text", "ИИ не предоставил комментария.")
            is_approved_by_ai = str(data.get("is_approved", "false")).lower() == 'true'
            ai_verdict = "✅ Одобрить" if is_approved_by_ai else "❌ Отклонить"

            # Формируем красивое сообщение-подсказку
            # ВАЖНО: Мы экранируем фидбек от ИИ и используем parse_mode=MarkdownV2
            suggestion_text = (
                f"🤫 *Подсказка от ИИ (для ДЗ выше):*\n\n"
                f"**Вердикт ИИ:** {escape_md(ai_verdict)}\n"
                f"**Комментарий:**\n{escape_md(feedback_from_ai)}\n\n"
                f"_(Это сообщение видите только вы. Студент ждет вашего финального ответа.)_"
            )

            # Отправляем подсказку в админ-чат
            try:
                await bot.send_message(
                    chat_id=ADMIN_GROUP_ID,
                    text=suggestion_text,
                    reply_to_message_id=original_admin_message_id,
                    parse_mode=None  # Используем MarkdownV2
                )
            except Exception as e_suggestion:
                logger.error(f"Не удалось отправить подсказку от ИИ админу: {e_suggestion}")

            # Важно: выходим, не обрабатывая результат
            return web.Response(text="OK: Ignored, homework is locked by admin.", status=200)
        # ===== КОНЕЦ НОВОЙ ЛОГИКИ =====

        logger.info(f"нет замка")

        if not all([student_user_id, course_numeric_id, lesson_num, original_admin_message_id]):
             logger.error(f"Некорректные или нулевые ID в колбэке от n8n: {data}")
             return web.Response(text="Error: Invalid IDs provided", status=400)

        logger.info(f'******course_numeric_id = {course_numeric_id}')
        course_id_str = await get_course_id_str(course_numeric_id)
        logger.info(f'******course_id_str = {course_id_str}')

        await handle_homework_result(
            user_id=student_user_id,
            course_id=course_id_str,
            course_numeric_id=course_numeric_id,
            lesson_num=lesson_num,
            admin_id=0,  # 0 для ИИ
            feedback_text=feedback_text,
            is_approved=is_approved,
            #bot=bot, # Передаем bot
            callback_query=None,
            original_admin_message_id_to_delete=original_admin_message_id
        )
        return web.Response(text="OK", status=200)


    except  (ValueError, TypeError) as e95:
        logger.error(f"Ошибка преобразования данных из n8n callback: {e95}. Полученные данные: {await request.text()}", exc_info=True)
        return web.Response(text="Error: Invalid data format", status=400)


@require_n8n_secret
async def handle_n8n_hw_error(request: web.Request) -> web.Response:
    bot: Bot = request.app['bot']
    logger.info('handle_n8n_hw_error Бот получен из request.app.')  # Добавил лог, чтобы убедиться
    try:
        data = await request.json()
        logger.info(f"Получен callback от n8n (HW Error): {data}")
        original_admin_message_id = data.get("original_admin_message_id")
        error_message = data.get("error_message", "Неизвестная ошибка в n8n.")

        if ADMIN_GROUP_ID and original_admin_message_id:
            await bot.send_message(
                ADMIN_GROUP_ID,
                text=f"⚠️ Ошибка при автоматической обработке ДЗ (ID сообщения: {original_admin_message_id}):\n`{escape_md(error_message)}`\nПожалуйста, проверьте вручную.",
                reply_to_message_id=original_admin_message_id,
                parse_mode=None
            )
        return web.Response(text="Error noted", status=200)
    except Exception as e66:
        logger.error(f"Ошибка обработки n8n_hw_error callback: {e66}", exc_info=True)
        return web.Response(text="Error processing request", status=500)


@require_n8n_secret
async def handle_n8n_expert_answer(request: web.Request) -> web.Response:
    bot: Bot = request.app['bot']
    logger.info('handle_n8n_expert_answer ')  # Добавил лог, чтобы убедиться
    try:
        data = await request.json()
        logger.info(f"Получен callback от n8n (Expert Answer): {data}")

        user_id_to_answer = data.get("user_id")
        answer_text = data.get("answer_text")
        source = data.get("source", "ai")

        if user_id_to_answer and answer_text:
            prefix = "🤖 Ответ ИИ-помощника:\n" if source == "ai_generated" else "👩‍🏫 Ответ эксперта:\n"
            await bot.send_message(
                user_id_to_answer,
                text=prefix + escape_md(answer_text),
                parse_mode=None
            )
        return web.Response(text="OK", status=200)
    except Exception as e3333:
        logger.error(f"Ошибка обработки n8n_expert_answer callback: {e3333}", exc_info=True)
        return web.Response(text="Error processing request", status=500)

# ... остальной код ...

# В функции main(), где настраивается веб-сервер aiohttp:
# ...
# app = web.Application()
# webhook_requests_handler.register(app, path=final_webhook_path_for_aiohttp) # Ваш основной вебхук для Telegram
# setup_application(app, dp, bot=bot) # Это должно передавать bot и dp в app['bot'] и app['dp']

# Регистрируем новые пути для callback'ов от n8n


# Функция для экранирования спецсимволов (отключено - используем plain text)
def escape_md(text):
    """Возвращает текст без изменений (MarkdownV2 отключен)."""
    return str(text) if text is not None else ""


def get_unique_course_id(course_id: str, existing_courses: set) -> str:
    """Генерирует уникальный ID курса, добавляя цифры если нужно."""
    if course_id not in existing_courses:
        return course_id
    
    counter = 1
    new_course_id = f"{course_id}{counter}"
    while new_course_id in existing_courses:
        counter += 1
        new_course_id = f"{course_id}{counter}"
    return new_course_id


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
    except Exception as e1120:
        logger.error(f"Ошибка логирования действия {action_type} для user_id={user_id}: {e1120}")

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
    except Exception as e1147:
        logger.error(f"Error resolving user ID: {e1147}")
        return None


@db_exception_handler
async def send_lesson_to_user(user_id: int, course_id: str, lesson_num: int, repeat: bool = False, level: int = 1):
    """Отправляет урок или сообщение о завершении курса, обновляет статус пользователя."""
    logger.info(
        f"🚀 send_lesson_to_user: user_id={user_id}, course_id={course_id}, lesson_num={lesson_num}, "
        f"repeat={repeat}, user_course_level={level}"
    )

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # 0. Защита от lesson_num=0 - это описание курса, не урок
            if lesson_num <= 0:
                logger.warning(f"send_lesson_to_user: lesson_num={lesson_num} <= 0, показываем главное меню")
                cursor_v = await conn.execute(
                    "SELECT version_id, hw_status, hw_type, level FROM user_courses WHERE user_id = ? AND course_id = ?",
                    (user_id, course_id)
                )
                v_row = await cursor_v.fetchone()
                if v_row:
                    version_id, hw_status, hw_type, user_level = v_row
                    await send_main_menu(
                        user_id, course_id, 0, version_id,
                        homework_pending=(hw_status == 'pending' or hw_status == 'rejected'),
                        hw_type=hw_type or 'none',
                        user_course_level_for_menu=user_level or 1
                    )
                return

            # 1. Получаем общее количество уроков в курсе (с lesson_num > 0)
            cursor_total = await conn.execute(
                "SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ? AND lesson_num > 0", (course_id,)
            )
            total_lessons_data = await cursor_total.fetchone()
            total_lessons = total_lessons_data[0] if total_lessons_data and total_lessons_data[0] is not None else 0
            logger.info(f"Для курса '{course_id}' найдено {total_lessons} уроков. Запрошен урок {lesson_num}.")

            # 2. Проверяем, не является ли запрошенный урок выходящим за рамки курса
            if  0 < total_lessons < lesson_num:
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
            if not repeat and not is_homework_local and 0 < total_lessons < lesson_num:
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

    except TelegramBadRequest as e1221:
        logger.error(
            f"💥 Ошибка Telegram API в send_lesson_to_user для user {user_id}, курс {course_id}, урок {lesson_num}: {e1221}",
            exc_info=True)
        await bot.send_message(user_id,
                               escape_md("📛 Произошла ошибка при отправке урока (Telegram API). Мы уже разбираемся!"),
                               parse_mode=None)
    except Exception as e1456:
        logger.error(
            f"💥 Общая ошибка в send_lesson_to_user для user {user_id}, курс {course_id}, урок {lesson_num}: {e1456}",
            exc_info=True)
        await bot.send_message(user_id, escape_md(
            "📛 Что-то пошло не так при подготовке урока. Робот уже вызвал ремонтную бригаду!"),
                               parse_mode=None)


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
                await bot.send_message(user_id, safe_caption, parse_mode=None)
            elif file_id:
                # Динамический вызов метода отправки
                send_method_name = f"send_{content_type}"
                if hasattr(bot, send_method_name):
                    send_method = getattr(bot, send_method_name)
                    await send_method(user_id, file_id, caption=safe_caption, parse_mode=None)
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
        
        logger.info(f"UPDATE user_courses: hw_status='{new_hw_status_for_db}', hw_type={new_hw_type_for_db}, lesson_num={lesson_num}")

        await conn.execute(
            """UPDATE user_courses
               SET hw_status = ?, hw_type = ?, current_lesson = ?, last_lesson_sent_time = ?
               WHERE user_id = ? AND course_id = ? AND status = 'active'""",
            (new_hw_status_for_db, new_hw_type_for_db, lesson_num, now_utc_str, user_id, course_id)
        )
        await log_action(user_id, "LESSON_SENT", course_id, lesson_num, new_value=str(user_course_level))
        
        # ПРОВЕРКА ЧТО ЗАПИСАЛОСЬ
        cursor_verify = await conn.execute(
            "SELECT hw_status FROM user_courses WHERE user_id = ? AND course_id = ? AND status = 'active'",
            (user_id, course_id)
        )
        verify_row = await cursor_verify.fetchone()
        logger.info(f"VERIFY после UPDATE: hw_status={verify_row[0] if verify_row else 'NULL'}")
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
    course_numeric_id=await get_course_id_int(course_id)
    message_text = (
        f"🎉 Поздравляем с успешным завершением курса «{course_title_safe}»! 🎉\n\n"
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
            callback_data=RestartCourseCallback(course_numeric_id = course_numeric_id, action="next_level").pack()  # Добавим action
        )

    # Кнопка повторить ТЕКУЩИЙ уровень (если он не первый, или если хотим всегда давать возможность)
    # Можно добавить условие, if current_user_level > 0 или просто всегда показывать
    builder.button(
        text=escape_md(f"🔁 Повторить {current_user_level}-й уровень"),
        callback_data=RestartCourseCallback(course_numeric_id = course_numeric_id, action="restart_current_level").pack()
    )

    builder.button(text=escape_md("Выбрать другой курс"), callback_data="select_other_course")
    builder.button(text=escape_md("Оставить отзыв"), callback_data="leave_feedback")
    builder.adjust(1)  # Все кнопки в один столбец

    await bot.send_message(
        chat_id=user_id,
        text=message_text,
        reply_markup=builder.as_markup(),
        parse_mode=None
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
    # Если уроков в курсе вообще нет (total_lessons=0) - не спамим, это известная проблема
    if total_lessons == 0:
        logger.warning(
            f"⚠️ Курс '{course_id}' пустой (0 уроков). Пропускаю предупреждение для user_id={user_id}. "
            f"Нужно загрузить уроки через репост в админ-группу."
        )
        return

    warning_key = f"{user_id}:{course_id}:{lesson_num}"

    if warning_key in missing_lesson_warnings_sent:
        logger.debug(f"Skipping duplicate missing lesson warning for {warning_key}")
        return

    missing_lesson_warnings_sent.add(warning_key)

    logger.warning(
        f"⚠️ Контент для урока {lesson_num} не найден в курсе {course_id}, "
        f"хотя такой номер урока допустим (всего {total_lessons} уроков)."
    )
    course_title_safe = await get_course_title(course_id)
    await bot.send_message(
        user_id,
        f"Извините, урок №{lesson_num} для курса «{course_title_safe}» временно недоступен или еще не был добавлен. "
        f"Пожалуйста, попробуйте позже или свяжитесь с поддержкой.",
        parse_mode=None
    )

async def scheduled_lesson_check(user_id: int):
    """Запускает проверку расписания уроков для пользователя каждую минуту."""
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
    except Exception as e1458:
        logger.error(f"Error getting course status for user {user_id}: {e1458}")
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
                                     (то есть, ��оследний отправленный пользователю).
    """
    logger.debug(
        f"get_next_lesson_time: user_id={user_id}, course_id={course_id}, current_lesson_for_display={current_lesson_for_display}")
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
                    f"Урок {current_lesson_for_display} является последним для курса {course_id} (��сего {total_lessons}).")
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
                return "о������������������ибка расчета времени (н������т базовой даты)"

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
            logger.debug(f"get_next_lesson_time: user_id={user_id}, db_current_lesson={db_current_lesson}")


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

            logger.debug(f"Урок {next_lesson_to_send_number}: {time_of_lesson_event_utc}")


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

            logger.debug(
                f"Для user_id={user_id} следующий урок ({next_lesson_to_send_number}) в {user_timezone_str}: {formatted_time}")
            return formatted_time

    except Exception as e1606:
        logger.error(
            f"Ошибка при получении времени следующего урока для user_id={user_id}, course_id={course_id}: {e1606}",
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
            
            # Проверяем, есть ли уже сообщения для этого курса
            cursor_existing = await conn.execute(
                "SELECT COUNT(*) FROM group_messages WHERE course_id = ?",
                (course_id,)
            )
            existing_messages_count = (await cursor_existing.fetchone())[0]
            
            if existing_messages_count == 0:
                # Это ПЕРВОЕ сообщение для курса - считаем его описанием (урок 0)
                if message.content_type == "text":
                    course_snippet = extract_course_snippet(cleaned_text)
                    course_title = extract_course_title(cleaned_text)
                    lesson_num = 0  # Первое сообщение = урок 0 (описание курса)
                    
                    # Обновляем название и описание курса
                    await conn.execute("""
                        UPDATE courses
                        SET title = ?, description = ?
                        WHERE course_id = ?
                    """, (course_title, course_snippet, course_id))
                    logger.info(f"✅ Первое сообщение курса {course_id} сохранено как описание (урок 0)")
                    await conn.commit()
                else:
                    # Первое сообщение не текстовое - всё равно урок 0, но без описания
                    lesson_num = 0
                    logger.warning(f"⚠️ Первое сообщение курса {course_id} не текстовое ({message.content_type}), но сохранено как урок 0")
            elif group_id_str in lesson_stack and lesson_stack[group_id_str]:
                # Есть активный урок из стека
                lesson_num = lesson_stack[group_id_str][-1]
            else:
                # Берем последний известный номер урока и увеличиваем на 1
                last_info = last_message_info.get(group_id_str, {})
                last_lesson = last_info.get("lesson_num", 0)
                
                # Если это новое сообщение (не часть текущего урока), увеличиваем номер
                if not hw_start_match and not start_lesson_match:
                    # Проверяем, не дублируется ли сообщение
                    cursor_check = await conn.execute(
                        "SELECT lesson_num FROM group_messages WHERE message_id = ? AND group_id = ?",
                        (mes_id, group_id_str)
                    )
                    if await cursor_check.fetchone():
                        logger.info(f"Сообщение {mes_id} уже сохранено, пропускаем")
                        return
                    
                    lesson_num = last_lesson + 1
                    logger.info(f"Новое сообщение: увеличиваем номер урока с {last_lesson} до {lesson_num}")
                else:
                    lesson_num = last_lesson

            logger.info(f"{course_snippet=} {course_title=} {lesson_num=} {is_homework=} {hw_type=}")

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
    except Exception as e2109:(
        logger.error(f"❌ Ошибка в функции save_message_to_db: {e2109}", exc_info=True))


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

    except Exception as e2254:
        logger.error(f"Ошибка при обработке завершения курса: {e2254}")


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
                    code_info.get("price", 0)
                ))
                #logger.info(f"Added code: {code}")

            await conn.commit()
            logger.info("Settings imported successfully")

    except Exception as e2093:
        logger.error(f"Import error: {str(e2093)}")
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

    except TelegramBadRequest as e2344:
        logger.warning(f"Ошибка: {gr_name} | ID: {raw_id}\n Подробнее: {str(e2344)}")
        return f"Ошибка: {gr_name} | ID: {raw_id}\n Подробнее: {str(e2344)}"



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
    
    # Проверяем, был ли только что создан settings.json
    settings_status = ""
    if not os.path.exists(SETTINGS_FILE):
        settings_status = "\n⚠️ Файл settings.json не найден и будет создан при первом добавлении курса\n"
    elif len(settings.get("groups", {})) == 0:
        settings_status = "\n💡 Settings.json загружен, но курсы ещё не добавлены\n"
    
    # Формирование текста сообщения для администраторов
    message_text = "🚀 Бот запущен\n\n📊 Статус групп курсов:\n" + "\n".join(channel_reports) + \
                   settings_status + \
                   "\n🔧 Команды:\n/add_course - создать новый курс (пошагово)\n" + \
                   "/add_course <group_id> <course_id> <code1> <code2> <code3> - быстрое создание\n" + \
                   "/upload_lesson - загрузить уроки"

    # Отправка сообщения в группу администраторов
    try:
        await bot.send_message(admin_group_id, message_text, parse_mode=None)
    except Exception as e2371:
        logger.error(f"Ошибка при отправке стартового сообщения в группу администраторов: {e2371}")
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

@dp.message(Command("export_db"))
@db_exception_handler
async def export_db(message: types.Message):
    """Экспорт данных из базы данных в JSON-файл. Для суперадминов или в админ-группе."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Разрешаем суперадминам из лички или любому в админ-группе
    if not (user_id in ADMIN_IDS_CONF or chat_id == ADMIN_GROUP_ID):
        await message.answer("❌ Только для суперадминов или в админ-группе.")
        return
    
    logger.info(f"/export_db от user_id={user_id}")

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
                caption="📦 База данных успешно экспортирована в JSON",
                parse_mode=None
            )

        logger.info("База данных успешно экспортирована.")
    except Exception as e2218:
        logger.error(f"Ошибка при экспорте базы данных: {e2218}")
        await message.answer("❌ Произошла ошибка при экспорте базы данных", parse_mode=None)

@dp.message(Command("import_db"))
@db_exception_handler
async def import_db(message: types.Message):
    """Импорт данных из JSON-файла в базу данных. Для суперадминов или в админ-группе."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Разрешаем суперадминам из лички или любому в админ-группе
    if not (user_id in ADMIN_IDS_CONF or chat_id == ADMIN_GROUP_ID):
        await message.answer("❌ Только для суперадминов или в админ-группе.")
        return
    
    logger.info(f"/import_db от user_id={user_id}")

    if not message.document:
        await message.answer(
            "📥 Импорт базы данных\n\n"
            "Отправьте JSON-файл с данными.\n\n"
            "⚠️ Внимание: существующие данные будут заменены!",
            parse_mode=None
        )
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


@dp.message(Command("add_course"))
async def cmd_add_course(message: types.Message, state: FSMContext, command: CommandObject):
    """
    Добавление нового курса.
    Поддерживает два режима:
    - Старый формат: /add_course <group_id> <course_id> <code1> <code2> <code3>
    - Новый FSM: /add_course (без аргументов) - пошаговое создание
    """
    # Проверка админа
    if message.from_user.id not in ADMIN_IDS_CONF:
        return
    
    # Проверяем старый формат (с аргументами)
    if command.args:
        args = command.args.split()
        if len(args) >= 5:
            # Старый формат - быстрое создание
            return await create_course_old_format(message, args)
    
    # Новый FSM формат
    await state.set_state(AddCourseFSM.waiting_group_id)
    await message.answer(
        "🆕 Создание нового курса (7 шагов)\n\n"
        "Шаг 1/7: Введите ID группы Telegram\n"
        "Пример: `-1001234567890`\n\n"
        "💡 Чтобы узнать ID группы:\n"
        "1. Добавьте бота @getidsbot в группу\n"
        "2. Он покажет ID (начинается с -100)\n\n"
        "💡 Для отмены отправьте /cancel"
    )


async def create_course_old_format(message: types.Message, args: list):
    """Быстрое создание курса в старом формате"""
    # Очистка ID группы
    raw_group_id = args[0].strip()
    if raw_group_id.startswith("--"):
        raw_group_id = "-" + raw_group_id.lstrip("-")
    
    group_id_str = raw_group_id
    course_id = args[1]
    code1, code2, code3 = args[2], args[3], args[4]
    
    # Проверка дубликатов - если курс существует, добавляем цифру
    existing_courses = set(settings.get("groups", {}).values())
    original_course_id = course_id
    course_id = get_unique_course_id(course_id, existing_courses)
    
    if course_id != original_course_id:
        await message.answer(
            f"⚠️ Курс `{original_course_id}` уже существует. Используем `{course_id}`.",
            parse_mode=None
        )
    
    for code in [code1, code2, code3]:
        if code in settings.get("activation_codes", {}):
            await message.answer(f"❌ Код активации `{code}` уже используется!", parse_mode=None)
            return
    
    # Сохранение
    settings["groups"][group_id_str] = course_id
    settings["activation_codes"][code1] = {"course": course_id, "version": "v1", "price": 0}
    settings["activation_codes"][code2] = {"course": course_id, "version": "v2", "price": 0}
    settings["activation_codes"][code3] = {"course": course_id, "version": "v3", "price": 0}
    
    await process_add_course_to_db(course_id, group_id_str, code1, code2, code3)
    
    try:
        group_id_int = int(group_id_str)
        if group_id_int not in COURSE_GROUPS:
            COURSE_GROUPS.append(group_id_int)
    except ValueError:
        pass
    
    # Сохраняем settings.json явно
    await update_settings_file()
    
    await message.answer(
        f"✅ Курс *{escape_md(course_id)}* создан (быстрый формат)!\n\n"
        f"📍 Группа: `{escape_md(group_id_str)}`\n"
        f"🔑 Коды: `{escape_md(code1)}`, `{escape_md(code2)}`, `{escape_md(code3)}`\n\n"
        f"💾 Настройки сохранены в settings.json",
        parse_mode=None
    )


@dp.message(Command("cancel"), StateFilter(AddCourseFSM))
async def cancel_add_course(message: types.Message, state: FSMContext):
    """Отмена создания курса"""
    await state.clear()
    await message.answer("❌ Создание курса отменено. Данные не сохранены.")


@dp.message(AddCourseFSM.waiting_group_id)
async def process_course_group_id(message: types.Message, state: FSMContext):
    """Обработка ID группы"""
    raw_group_id = message.text.strip()
    
    # Очистка ID группы
    if raw_group_id.startswith("--"):
        raw_group_id = "-" + raw_group_id.lstrip("-")
    
    await state.update_data(group_id=raw_group_id)
    await state.set_state(AddCourseFSM.waiting_course_id)
    
    await message.answer(
        "Шаг 2/7: Введите ID курса\n"
        "Примеры: `python_base`, `design_pro`, `sprint2`\n\n"
        "💡 Используйте только латинские буквы, цифры и _"
    )


@dp.message(AddCourseFSM.waiting_course_id)
async def process_course_id(message: types.Message, state: FSMContext):
    """Обработка ID курса"""
    course_id = message.text.strip().lower()
    
    await state.update_data(course_id=course_id)
    await state.set_state(AddCourseFSM.waiting_description)
    
    await message.answer(
        "Шаг 3/7: Введите описание курса\n\n"
        "Это описание будет показываться студентам при активации.\n"
        "Можно использовать Markdown (*жирный*, _курсив_)\n\n"
        "💡 Чтобы пропустить, отправьте \"-\""
    )


@dp.message(AddCourseFSM.waiting_description)
async def process_course_description(message: types.Message, state: FSMContext):
    """Обработка описания курса"""
    description = message.text.strip()
    
    if description == "-":
        description = ""
    
    await state.update_data(description=description)
    await state.set_state(AddCourseFSM.waiting_code1)
    
    await message.answer(
        "Шаг 4/7: Введите код активации для тарифа v1 (Соло)\n"
        "Пример: `solo2024`, `base_v1`\n\n"
        "💡 Этот код даст доступ к базовому тарифу без проверки ДЗ"
    )


@dp.message(AddCourseFSM.waiting_code1)
async def process_course_code1(message: types.Message, state: FSMContext):
    """Обработка кода 1 с проверкой уникальности"""
    code1 = message.text.strip()
    
    # Проверка на дубликат
    if code1 in settings.get("activation_codes", {}):
        await message.answer(
            f"❌ Код `{code1}` уже используется!\n"
            f"Введите другой код для тарифа v1:"
        )
        return
    
    await state.update_data(code1=code1)
    await state.set_state(AddCourseFSM.waiting_code2)
    
    await message.answer(
        "Шаг 5/7: Введите код активации для тарифа v2 (с проверкой)\n"
        "Пример: `pro2024`, `base_v2`\n\n"
        "💡 Этот код даст доступ с проверкой домашних заданий"
    )


@dp.message(AddCourseFSM.waiting_code2)
async def process_course_code2(message: types.Message, state: FSMContext):
    """Обработка кода 2 с проверкой уникальности"""
    code2 = message.text.strip()
    data = await state.get_data()
    code1 = data.get('code1', '')
    
    # Проверка на дубликат
    if code2 in settings.get("activation_codes", {}):
        await message.answer(
            f"❌ Код `{code2}` уже используется!\n"
            f"Введите другой код для тарифа v2:"
        )
        return
    
    # Проверка что code2 != code1
    if code2 == code1:
        await message.answer(
            f"❌ Код `{code2}` уже используется для тарифа v1!\n"
            f"Введите уникальный код для тарифа v2:"
        )
        return
    
    await state.update_data(code2=code2)
    await state.set_state(AddCourseFSM.waiting_code3)
    
    await message.answer(
        "Шаг 6/7: Введите код активации для тарифа v3 (Премиум)\n"
        "Пример: `vip2024`, `base_v3`\n\n"
        "💡 Этот код даст доступ к премиум тарифу"
    )


@dp.message(AddCourseFSM.waiting_code3)
async def process_course_code3(message: types.Message, state: FSMContext):
    """Обработка кода 3 и показ сводки для подтверждения"""
    code3 = message.text.strip()
    data = await state.get_data()
    code1 = data.get('code1', '')
    code2 = data.get('code2', '')
    
    # Проверка на дубликаты и уникальность
    if code3 in settings.get("activation_codes", {}):
        await message.answer(
            f"❌ Код `{code3}` уже используется!\n"
            f"Введите другой код для тарифа v3:"
        )
        return
    
    if code3 == code1 or code3 == code2:
        await message.answer(
            f"❌ Код `{code3}` уже используется для другого тарифа!\n"
            f"Введите уникальный код для тарифа v3:"
        )
        return
    
    await state.update_data(code3=code3)
    
    # Получаем все данные для сводки
    group_id = data['group_id']
    course_id = data['course_id']
    description = data.get('description', '')
    
    # Формируем сводку
    summary = (
        f"📋 *Сводка создаваемого курса*\n\n"
        f"📚 Курс: `{escape_md(course_id)}`\n"
        f"📍 Группа: `{escape_md(group_id)}`\n"
    )
    
    if description:
        safe_desc = escape_md(description[:50] + ('...' if len(description) > 50 else ''))
        summary += f"📝 Описание: {safe_desc}\n"
    
    summary += (
        f"\n🔑 Коды активации:\n"
        f"  • v1 (Соло): `{escape_md(code1)}`\n"
        f"  • v2 (Проверка): `{escape_md(code2)}`\n"
        f"  • v3 (Премиум): `{escape_md(code3)}`\n\n"
        f"💾 Будет сохранено в settings.json\n\n"
        f"*Создать курс?*"
    )

    # Создаем инлайн кнопки для подтверждения
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, создать", callback_data=ConfirmCourseCreationCallback(action="yes").pack()),
            InlineKeyboardButton(text="❌ Нет, отменить", callback_data=ConfirmCourseCreationCallback(action="no").pack())
        ]
    ])

    await state.set_state(AddCourseFSM.waiting_confirmation)
    await message.answer(summary, reply_markup=keyboard, parse_mode=None)


@dp.callback_query(AddCourseFSM.waiting_confirmation, ConfirmCourseCreationCallback.filter())
async def process_course_confirmation(callback: CallbackQuery, callback_data: ConfirmCourseCreationCallback, state: FSMContext):
    """Обработка подтверждения создания курса через инлайн кнопки"""
    global settings

    await callback.answer()

    if callback_data.action == "no":
        await callback.message.edit_text("❌ Создание курса отменено. Данные не сохранены.")
        await state.clear()
        return

    # Получаем все данные
    data = await state.get_data()
    group_id = data['group_id']
    course_id = data['course_id']
    description = data.get('description', '')
    code1 = data['code1']
    code2 = data['code2']
    code3 = data['code3']

    # Проверка дубликатов - если курс существует, добавляем цифру
    existing_courses = set(settings.get("groups", {}).values())
    original_course_id = course_id
    course_id = get_unique_course_id(course_id, existing_courses)
    
    if course_id != original_course_id:
        await callback.message.edit_text(
            f"⚠️ Курс `{original_course_id}` уже существует. Используем `{course_id}`.",
            parse_mode=None
        )

    # Обновляем глобальные настройки
    settings["groups"][group_id] = course_id
    settings["activation_codes"][code1] = {"course": course_id, "version": "v1", "price": 0}
    settings["activation_codes"][code2] = {"course": course_id, "version": "v2", "price": 0}
    settings["activation_codes"][code3] = {"course": course_id, "version": "v3", "price": 0}

    # Сохраняем информацию о курсе (включая описание)
    if "courses" not in settings:
        settings["courses"] = {}
    settings["courses"][course_id] = {
        "title": f"{course_id} basic",
        "description": description or ""
    }

    # Сохраняем в БД
    try:
        await process_add_course_to_db(course_id, group_id, code1, code2, code3, description)

        # Явно сохраняем settings.json
        await update_settings_file()

        # Добавляем в список разрешенных групп
        try:
            group_id_int = int(group_id)
            if group_id_int not in COURSE_GROUPS:
                COURSE_GROUPS.append(group_id_int)
        except ValueError:
            pass

        await callback.message.edit_text(
            f"✅ Курс *{escape_md(course_id)}* успешно создан и сохранён!\n\n"
            f"💾 Настройки записаны в settings.json",
            parse_mode=None
        )
        logger.info(f"Админ создал курс {course_id} через FSM с подтверждением")

    except Exception as e:
        logger.error(f"Ошибка при создании курса: {e}", exc_info=True)
        await callback.message.edit_text(f"❌ Ошибка при создании курса: {e}")

    finally:
        await state.clear()


@dp.message(Command("edit_course_description"))
async def cmd_edit_course_description(message: types.Message, command: CommandObject):
    """
    Редактирование описания существующего курса.
    Формат: /edit_course_description <course_id> <новое_описание>
    Пример: /edit_course_description sprint2 Новое описание курса
    """
    # Проверка админа
    if message.from_user.id not in ADMIN_IDS_CONF:
        return
    
    if not command.args:
        await message.answer(
            "⚠️ Укажите ID курса и новое описание\n"
            "Пример: `/edit_course_description sprint2 Новое описание`",
            parse_mode=None
        )
        return
    
    args = command.args.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("⚠️ Нужно указать ID курса и новое описание", parse_mode=None)
        return
    
    course_id = args[0].strip().lower()
    new_description = args[1].strip()
    
    # Проверяем существование курса
    if course_id not in settings.get("groups", {}).values():
        await message.answer(
            f"❌ Курс `{escape_md(course_id)}` не найден!",
            parse_mode=None
        )
        return
    
    # Сохраняем описание
    if "course_descriptions" not in settings:
        settings["course_descriptions"] = {}
    
    settings["course_descriptions"][course_id] = new_description
    
    # Явно сохраняем settings.json
    await update_settings_file()
    
    await message.answer(
        f"✅ Описание курса `{escape_md(course_id)}` обновлено!\n\n"
        f"📝 Новое описание: {new_description[:100]}{'...' if len(new_description) > 100 else ''}\n\n"
        f"💾 Настройки сохранены в settings.json",
        parse_mode=None
    )
    logger.info(f"Админ обновил описание курса {course_id}")


@dp.message(Command("admin_reset"))
async def cmd_admin_reset(message: types.Message, state: FSMContext):
    """
    Полностью удаляет прогресс пользователя из базы.
    Используйте это, чтобы бот перестал слать уроки админу.
    """
    user_id = message.from_user.id
    
    # Сначала жестко сбрасываем любые диалоги
    await state.clear()
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Удаляем записи о прохождении курсов
            await conn.execute("DELETE FROM user_courses WHERE user_id = ?", (user_id,))
            # Удаляем задачи планировщика (если есть в памяти)
            await stop_lesson_schedule_task(user_id)
            await conn.commit()
            
        logger.info(f"Админ {user_id} сбросил свой статус студента.")
        await message.answer("✅ Я забыл, что вы студент. Больше никаких уведомлений и уроков. Только админка.", parse_mode=None)
        
    except Exception as e:
        logger.error(f"Ошибка admin_reset: {e}")
        await message.answer(f"Ошибка сброса: {e}")


# =========================== ЗАГРУЗКА УРОКОВ (UPLOAD LESSON) ===========================

class UploadLesson(StatesGroup):
    """FSM для загрузки урока"""
    waiting_course = State()
    waiting_lesson_num = State()
    waiting_content = State()


# StateFilter("*") означает "Ловить эту команду в ЛЮБОМ состоянии"
@dp.message(Command("upload_lesson"), StateFilter("*"))
async def cmd_upload_lesson(message: types.Message, state: FSMContext):
    """
    Начало загрузки урока.
    Принудительно прерывает любые другие процессы.
    """
    # 1. Сразу сбрасываем всё, что было до этого
    await state.clear()

    # Проверка на админа
    admin_ids_str = os.getenv("ADMIN_IDS", "")
    # Делаем список админов надежно
    try:
        admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip()]
    except:
        admin_ids = []

    if message.from_user.id not in admin_ids:
        # Игнорируем не-админов молча или шлем лесом
        return

    # Получаем сп��со�� курсов
    courses_list_str = "Нет доступных ку��с��в."
    if settings.get("groups"):
        courses_list_str = "\n".join([f"{i+1}. {c_id}" for i, c_id in enumerate(settings["groups"].values())])

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Админ-меню", callback_data="admin_menu")]
    ])

    await message.answer(
        f"🛠 РЕЖИМ ЗАГРУЗКИ\n\n"
        f"Доступные курсы:\n{courses_list_str}\n\n"
        f"👇 Введите ID курса или его номер из списка:",
        reply_markup=keyboard,
        parse_mode=None
    )

    # Переводим в состояние ожидания курса
    await state.set_state(UploadLesson.waiting_course)

# Обработчик кнопки отмены для всех состояний UploadLesson
@dp.callback_query(F.data == "admin_menu", StateFilter("UploadLesson:*"))
async def cancel_upload_lesson(callback: CallbackQuery, state: FSMContext):
    """Отмена загрузки урока"""
    await state.clear()
    await callback.message.edit_text("❌ Загрузка урока отменена.\n\n👑 АДМИН-МЕНЮ", reply_markup=None)
    # Показываем админ меню через небольшую задержку
    await asyncio.sleep(0.5)
    await cmd_start(callback.message, state)

@dp.message(UploadLesson.waiting_course)
async def process_course(message: types.Message, state: FSMContext):
    """Обработка выбора курса"""
    text = message.text.strip()
    
    # Получаем курсы из settings
    available_courses = list(settings.get("groups", {}).values())
    
    # Проверяем если ввели номер
    try:
        idx = int(text) - 1
        if 0 <= idx < len(available_courses):
            course_id = available_courses[idx]
        else:
            await message.answer(f"❌ Номер курса должен быть от 1 до {len(available_courses)}:")
            return
    except ValueError:
        # Проверяем если ввели ID курса напрямую
        if text in available_courses:
            course_id = text
        else:
            await message.answer(f"❌ Неизвестный курс. Доступные: {', '.join(available_courses)}")
            return
    
    await state.update_data(course_id=course_id)

    # Показываем существующие уроки этого курса
    existing_lessons_info = ""
    has_description = False
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверяем есть ли описание (урок 0)
            cursor_desc = await conn.execute('''
                SELECT COUNT(*) FROM group_messages
                WHERE course_id = ? AND lesson_num = 0
            ''', (course_id,))
            desc_count = (await cursor_desc.fetchone())[0]
            has_description = desc_count > 0
            
            # Получаем уроки (lesson_num > 0)
            cursor = await conn.execute('''
                SELECT lesson_num, COUNT(*) as parts_count
                FROM group_messages
                WHERE course_id = ? AND lesson_num > 0
                GROUP BY lesson_num
                ORDER BY lesson_num
            ''', (course_id,))
            lessons = await cursor.fetchall()

            if has_description:
                existing_lessons_info = "\n📚 Существующие уроки:\n"
                existing_lessons_info += "   • 📘 Урок 0 (описание курса) ✅\n"
                if lessons:
                    for lesson_num, parts_count in lessons:
                        existing_lessons_info += f"   • Урок {lesson_num} ({parts_count} частей)\n"
            elif lessons:
                existing_lessons_info = "\n📚 Существующие уроки:\n"
                for lesson_num, parts_count in lessons:
                    existing_lessons_info += f"   • Урок {lesson_num} ({parts_count} частей)\n"
                existing_lessons_info += "\n⚠️ Нет описания курса (урок 0)!\n"
                existing_lessons_info += "💡 Рекомендуется добавить: введите 0\n"
            else:
                existing_lessons_info = "\n📭 У этого курса пока нет уроков.\n"
                existing_lessons_info += "💡 Начните с описания (урок 0) или добавьте урок 1\n"
    except Exception as e:
        logger.error(f"Ошибка получения списка уроков: {e}")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_menu")]
    ])

    await message.answer(
        f"📚 Выбран курс: {course_id}\n"
        f"{existing_lessons_info}\n"
        f"🔢 Введите номер урока:\n\n"
        f"• 0 = описание курса (только один!)\n"
        f"• 1, 2, 3... = уроки\n"
        f"{'⚠️ Описание уже есть!' if has_description else '💡 Рекомендуется начать с урока 0'}",
        reply_markup=keyboard,
        parse_mode=None
    )
    await state.set_state(UploadLesson.waiting_lesson_num)

@dp.message(UploadLesson.waiting_lesson_num)
async def process_lesson_num(message: types.Message, state: FSMContext):
    """Обработка номера урока"""
    try:
        lesson_num = int(message.text.strip())
        if lesson_num < 0:
            await message.answer("❌ Номер урока должен быть 0 или больше.\n\nУрок 0 = описание курса (НУЛЕВОЕ сообщение).")
            return
    except ValueError:
        await message.answer("❌ Введите число.")
        return

    # Подсказка для урока 0
    if lesson_num == 0:
        await state.update_data(lesson_num=lesson_num)
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_menu")]
        ])
        
        await message.answer(
            "📝 Урок 0 - это описание курса.\n\n"
            "⚠️ Описание может быть ТОЛЬКО ОДНО!\n\n"
            "Отправьте ТЕКСТОВОЕ сообщение с описанием курса.\n"
            "Это сообщение будет показано студентам при активации курса.\n\n"
            "💡 Формат:\n"
            "*Курс Название*\n"
            "Описание курса...\n\n"
            "Или просто отправьте текст - бот автоматически сохранит как описание.",
            reply_markup=keyboard,
            parse_mode=None
        )
        await state.set_state(UploadLesson.waiting_content)
        return

    await state.update_data(lesson_num=lesson_num)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data="admin_menu")]
    ])

    await message.answer(
        "📝 Отправьте контент урока:\n\n"
        "Можно отправить:\n"
        "• Текст\n"
        "• Фото (с подписью)\n"
        "• Видео (с подписью)\n"
        "• Документ\n\n"
        "Для домашнего задания добавьте #hw или *hw в подписи к файлу.\n\n"
        "💡 Теги для настройки (работают # и *):\n"
        "• #LEVEL 2 / *LEVEL 2 - уровень сложности\n"
        "• #HW_TYPE photo / *HW_TYPE photo - тип ответа на ДЗ",
        reply_markup=keyboard,
        parse_mode=None
    )
    await state.set_state(UploadLesson.waiting_content)

@dp.message(UploadLesson.waiting_content, F.content_type.in_({'text', 'photo', 'video', 'document'}))
async def process_content(message: types.Message, state: FSMContext):
    """Обработка контента урока"""
    import re
    
    data = await state.get_data()
    course_id = data['course_id']
    lesson_num = data['lesson_num']
    
    content_type = message.content_type
    text = message.caption or message.text or ""
    file_id = None
    
    # Парсим уровень из текста (*LEVEL X или #LEVEL X), по умолчанию 1
    level = 1
    level_match = re.search(r"[*#]LEVEL\s*(\d+)", text, re.IGNORECASE)
    if level_match:
        level = int(level_match.group(1))
        # Удаляем тег из текста перед сохранением
        text = re.sub(r"[*#]LEVEL\s*\d+", "", text, flags=re.IGNORECASE).strip()
    
    # Парсим тип домашнего задания (*HW_TYPE или #HW_TYPE)
    hw_type = None
    hw_type_match = re.search(r"[*#]HW_TYPE\s*(\w+)", text, re.IGNORECASE)
    if hw_type_match:
        hw_type = hw_type_match.group(1).lower()
        # Удаляем тег из текста перед сохранением
        text = re.sub(r"[*#]HW_TYPE\s*\w+", "", text, flags=re.IGNORECASE).strip()
    
    # Парсим маркер домашнего задания (#hw или *hw)
    is_homework = re.search(r"[*#]hw", text, re.IGNORECASE) is not None
    
    if is_homework:
        if not hw_type:  # Если не задан через *HW_TYPE или #HW_TYPE
            type_match = re.search(r"[*#]type_(\w+)", text, re.IGNORECASE)
            if type_match:
                hw_type = type_match.group(1).lower()
            else:
                hw_type = 'text'
        
        # Удаляем все теги ДЗ из текста
        text = re.sub(r"[*#]hw|[*#]type_\w+", "", text, flags=re.IGNORECASE).strip()
    
    if content_type == 'photo':
        file_id = message.photo[-1].file_id
    elif content_type == 'video':
        file_id = message.video.file_id
    elif content_type == 'document':
        file_id = message.document.file_id
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute('''
                INSERT INTO group_messages 
                (group_id, lesson_num, course_id, content_type, is_homework, hw_type, text, file_id, level, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                f'direct_upload_{message.from_user.id}',
                lesson_num,
                course_id,
                content_type,
                is_homework,
                hw_type,
                text,
                file_id,
                level,
                message.message_id
            ))
            await conn.commit()
        
        hw_status = "✅ Да" if is_homework else "❌ Нет"
        level_info = f"🎯 Уровень: {level}\n" if level > 1 else ""
        
        # Создаем inline-клавиатуру для следующих действий
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📎 Добавить ещё",
                    callback_data=UploadLessonAction(action="more", course_id=course_id, lesson_num=lesson_num).pack()
                ),
                InlineKeyboardButton(
                    text="📚 Следующий урок",
                    callback_data=UploadLessonAction(action="next", course_id=course_id, lesson_num=lesson_num).pack()
                )
            ],
            [
                InlineKeyboardButton(
                    text="🗑️ Удалить последний",
                    callback_data=UploadLessonAction(action="delete_last", course_id=course_id, lesson_num=lesson_num).pack()
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Завершить",
                    callback_data=UploadLessonAction(action="done", course_id=course_id, lesson_num=lesson_num).pack()
                )
            ]
        ])
        
        await message.answer(
            f"✅ Урок успешно загружен!\n\n"
            f"📚 Курс: {course_id}\n"
            f"🔢 Урок: {lesson_num}\n"
            f"{level_info}"
            f"📝 Тип: {content_type}\n"
            f"🏠 ДЗ: {hw_status}\n\n"
            f"Выберите действие:",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка загрузки урока: {e}")
        await message.answer(f"❌ Ошибка при сохранении: {e}")

@dp.callback_query(UploadLessonAction.filter())
async def handle_upload_lesson_action(callback: CallbackQuery, callback_data: UploadLessonAction, state: FSMContext):
    """Обработка действий после загрузки урока"""
    action = callback_data.action
    course_id = callback_data.course_id
    lesson_num = callback_data.lesson_num
    
    if action == "more":
        # Остаемся в том же состоянии, можно добавлять ещё контент
        await callback.message.edit_text(
            f"📎 Добавьте ещё контент к уроку {lesson_num} курса {course_id}\n\n"
            f"Можно отправить: текст, фото, видео, документ"
        )
        await state.set_state(UploadLesson.waiting_content)
        
    elif action == "next":
        # Переходим к следующему уроку
        next_lesson = lesson_num + 1
        await state.update_data(course_id=course_id, lesson_num=next_lesson)
        await callback.message.edit_text(
            f"📚 Курс: {course_id}\n"
            f"🔢 Урок: {next_lesson}\n\n"
            f"📝 Отправьте контент урока:\n"
            f"• Текст\n"
            f"• Фото (с подписью)\n"
            f"• Видео (с подписью)\n"
            f"• Документ\n\n"
            f"Для домашнего задания добавьте #hw или *hw в подписи к файлу.\n\n"
            f"💡 Теги для настройки (работают # и *):\n"
            f"• #LEVEL 2 / *LEVEL 2 - уровень сложности\n"
            f"• #HW_TYPE photo / *HW_TYPE photo - тип ответа на ДЗ"
        )
        await state.set_state(UploadLesson.waiting_content)
        
    elif action == "done":
        # Завершаем загрузку
        await state.clear()
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📚 Список уроков", callback_data="list_lessons_menu")],
            [InlineKeyboardButton(text="🔙 Админ-меню", callback_data="admin_menu")]
        ])
        
        await callback.message.edit_text(
            "✅ Загрузка уроков завершена!\n\n"
            "Для загрузки новых уроков используйте /upload_lesson",
            reply_markup=keyboard
        )
    
    elif action == "delete_last":
        # Удаляем последнюю загруженную часть урока
        try:
            async with aiosqlite.connect(DB_FILE) as conn:
                # Находим последнюю часть
                cursor = await conn.execute('''
                    SELECT id FROM group_messages 
                    WHERE course_id = ? AND lesson_num = ?
                    ORDER BY id DESC LIMIT 1
                ''', (course_id, lesson_num))
                row = await cursor.fetchone()
                
                if row:
                    await conn.execute("DELETE FROM group_messages WHERE id = ?", (row[0],))
                    await conn.commit()
                    
                    # Проверяем, остались ли ещё части
                    cursor = await conn.execute('''
                        SELECT COUNT(*) FROM group_messages 
                        WHERE course_id = ? AND lesson_num = ?
                    ''', (course_id, lesson_num))
                    count = (await cursor.fetchone())[0]
                    
                    # Формируем то же меню что было после загрузки
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📎 Добавить ещё",
                                callback_data=UploadLessonAction(action="more", course_id=course_id, lesson_num=lesson_num).pack()
                            ),
                            InlineKeyboardButton(
                                text="📚 Следующий урок",
                                callback_data=UploadLessonAction(action="next", course_id=course_id, lesson_num=lesson_num).pack()
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="🗑️ Удалить последний",
                                callback_data=UploadLessonAction(action="delete_last", course_id=course_id, lesson_num=lesson_num).pack()
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="✅ Завершить",
                                callback_data=UploadLessonAction(action="done", course_id=course_id, lesson_num=lesson_num).pack()
                            )
                        ]
                    ])
                    
                    if count > 0:
                        await callback.message.edit_text(
                            f"🗑️ Часть удалена! Осталось частей: {count}\n\n"
                            f"📚 Курс: {course_id}\n"
                            f"🔢 Урок: {lesson_num}\n\n"
                            f"Выберите действие:",
                            reply_markup=keyboard
                        )
                    else:
                        await callback.message.edit_text(
                            f"🗑️ Весь урок {lesson_num} удалён!\n\n"
                            f"📚 Курс: {course_id}\n"
                            f"🔢 Урок: {lesson_num}\n\n"
                            f"Выберите действие:",
                            reply_markup=keyboard
                        )
                    
                    await state.set_state(UploadLesson.waiting_content)
                else:
                    await callback.answer("❌ Нечего удалять", show_alert=True)
        except Exception as e:
            logger.error(f"Ошибка при удалении: {e}")
            await callback.answer(f"❌ Ошибка: {e}", show_alert=True)
    
    await callback.answer()

@dp.message(Command("list_lessons"))
async def cmd_list_lessons(message: types.Message):
    """Показать список загруженных уроков"""
    logger.info(f"cmd_list_lessons START: user_id={message.from_user.id}")
    await show_lessons_list(message.from_user.id, message.chat.id)
    logger.info("✅ Обработчики загрузки контента зарегистрированы")


@dp.message(Command("show_codes"))
async def cmd_show_codes(message: types.Message):
    """Показать курсы и коды активации (только для админов)"""
    logger.info(f"cmd_show_codes START: user_id={message.from_user.id}")
    
    if message.from_user.id not in ADMIN_IDS_CONF:
        await message.answer("❌ Только для администраторов.")
        return
    
    try:
        settings = await load_settings()
        
        # Часть 1: Список курсов из базы данных
        result = "📚 Список курсов в базе:\n\n"
        
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute('''
                SELECT DISTINCT c.course_id, c.title, c.description, c.group_id,
                       (SELECT COUNT(*) FROM group_messages gm WHERE gm.course_id = c.course_id) as lessons_count
                FROM courses c
                ORDER BY c.course_id
            ''')
            courses_db = await cursor.fetchall()
        
        if courses_db:
            for course_id, title, description, group_id, lessons_count in courses_db:
                result += f"📌 {course_id}\n"
                result += f"   Название: {title or 'не указано'}\n"
                result += f"   Группа: {group_id or 'не указана'}\n"
                result += f"   Уроков: {lessons_count or 0}\n"
                if description:
                    desc_short = description[:100] + "..." if len(description) > 100 else description
                    result += f"   Описание: {desc_short}\n"
                result += "\n"
        else:
            result += "📭 Курсы не найдены в базе данных.\n\n"
        
        # Часть 2: Коды активации
        result += "🔐 Коды активации:\n\n"
        
        if not settings or "activation_codes" not in settings:
            result += "📭 Коды активации не найдены.\n"
        else:
            codes = settings["activation_codes"]
            if not codes:
                result += "📭 Нет зарегистрированных кодов.\n"
            else:
                # Группируем коды по курсам
                courses = {}
                for code, data in codes.items():
                    course = data.get("course", "unknown")
                    version = data.get("version", "v1")
                    price = data.get("price", 0)
                    
                    if course not in courses:
                        courses[course] = []
                    courses[course].append({
                        "code": code,
                        "version": version,
                        "price": price
                    })
                
                for course_name, course_codes in sorted(courses.items()):
                    result += f"📚 {course_name}:\n"
                    for item in course_codes:
                        code = item["code"]
                        version = item["version"]
                        price = item["price"]
                        result += f"   • {code} — {version}"
                        if price:
                            result += f" ({price}₽)"
                        result += "\n"
                    result += "\n"
        
        await message.answer(result, parse_mode=None)
        logger.info(f"cmd_show_codes: показано курсов и кодов")
        
    except Exception as e:
        logger.error(f"Ошибка при показе кодов: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("list_admins"))
async def cmd_list_admins(message: types.Message):
    """Показать список всех админов с именами"""
    logger.info(f"cmd_list_admins START: user_id={message.from_user.id}")
    
    if not await is_admin(message.from_user.id):
        await message.answer("❌ Только для администраторов.")
        return
    
    try:
        result = "👥 Список администраторов:\n\n"
        
        # Суперадмины из .env
        result += "👑 Суперадмины (из .env):\n"
        for uid in ADMIN_IDS_CONF:
            try:
                user = await bot.get_chat(uid)
                name = user.first_name or ""
                if user.last_name:
                    name += f" {user.last_name}"
                username = f"@{user.username}" if user.username else ""
                result += f"   • {name} {username} (ID: {uid})\n"
            except:
                result += f"   • ID: {uid}\n"
        
        # Участники админ-группы
        if ADMIN_GROUP_ID:
            result += f"\n🔧 Админы группы ({ADMIN_GROUP_ID}):\n"
            try:
                # Получаем список участников группы
                admins = await bot.get_chat_administrators(ADMIN_GROUP_ID)
                admins_info = []
                for member in admins:
                    name = member.user.first_name or ""
                    if member.user.last_name:
                        name += f" {member.user.last_name}"
                    username = f"@{member.user.username}" if member.user.username else ""
                    admins_info.append(f"   • {name} {username} (ID: {member.user.id})")

                if admins_info:
                    result += "\n".join(admins_info) + "\n"
                else:
                    result += "   Не удалось получить список.\n"
            except Exception as e:
                result += f"   Ошибка: {e}\n"
        
        result += f"\n💡 Админы могут: управлять курсами, уроками\n"
        result += f"💡 Суперадмины могут: экспорт/импорт БД из лички"
        
        await message.answer(result, parse_mode=None)
        
    except Exception as e:
        logger.error(f"Ошибка при показе админов: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("add_admin"))
async def cmd_add_admin(message: types.Message):
    """Добавить пользователя в админ-группу (любой админ может)"""
    logger.info(f"cmd_add_admin START: user_id={message.from_user.id}")
    
    if not await is_admin(message.from_user.id):
        await message.answer("❌ Только для администраторов.")
        return
    
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Использование: /add_admin <user_id или @username>\n"
                "Пример: /add_admin 123456789 или /add_admin @username"
            )
            return
        
        user_identifier = args[1].strip()
        if user_identifier.startswith('@'):
            username = user_identifier[1:]
        else:
            username = None
            try:
                target_user_id = int(user_identifier)
            except ValueError:
                await message.answer("❌ Неверный формат. Укажите ID (число) или @username")
                return
        
        if username:
            try:
                chat = await bot.get_chat(f"@{username}")
                target_user_id = chat.id
            except Exception as e:
                await message.answer(f"❌ Не найден пользователь @{username}")
                return
        
        if not target_user_id:
            await message.answer("❌ Не удалось определить пользователя")
            return
        
        if await is_admin(target_user_id):
            await message.answer("✅ Этот пользователь уже является администратором.")
            return
        
        try:
            chat_member = await bot.get_chat_member(ADMIN_GROUP_ID, target_user_id)
            await message.answer(
                f"✅ Пользователь {target_user_id} уже участвует в админ-группе."
            )
            return
        except:
            pass
        
        invite_link = await bot.export_chat_invite_link(ADMIN_GROUP_ID)
        
        try:
            await bot.send_message(
                target_user_id,
                f"👤 Вас пригласили в админ-группу бота AntBot.\n"
                f"Ссылка для вступления: {invite_link}"
            )
            
            actor_name = message.from_user.full_name
            await bot.send_message(
                ADMIN_GROUP_ID,
                f"👤 {actor_name} добавил нового админа: ID {target_user_id}"
            )
            
            await message.answer(
                f"✅ Приглашение отправлено пользователю {target_user_id}\n"
                f"После вступления в группу он станет администратором."
            )
            
        except Exception as e:
            await message.answer(f"❌ Не удалось отправить приглашение: {e}")
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении админа: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("remove_admin"))
async def cmd_remove_admin(message: types.Message):
    """Удалить пользователя из админ-группы (любой админ может)"""
    logger.info(f"cmd_remove_admin START: user_id={message.from_user.id}")
    
    if not await is_admin(message.from_user.id):
        await message.answer("❌ Только для администраторов.")
        return
    
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Использование: /remove_admin <user_id>\n"
                "Пример: /remove_admin 123456789"
            )
            return
        
        user_identifier = args[1].strip()
        
        try:
            target_user_id = int(user_identifier)
        except ValueError:
            await message.answer("❌ Укажите числовой ID пользователя")
            return
        
        if target_user_id in ADMIN_IDS_CONF:
            await message.answer(
                "❌ Нельзя удалить суперадмина (из .env ADMIN_IDS)"
            )
            return
        
        if not await is_admin(target_user_id):
            await message.answer("❌ Этот пользователь не является администратором.")
            return
        
        try:
            await bot.ban_chat_member(ADMIN_GROUP_ID, target_user_id)
            await bot.unban_chat_member(ADMIN_GROUP_ID, target_user_id)
            
            actor_name = message.from_user.full_name
            await bot.send_message(
                ADMIN_GROUP_ID,
                f"👤 {actor_name} удалил админа: ID {target_user_id}"
            )
            
            await message.answer(f"✅ Пользователь {target_user_id} удалён из админ-группы.")
            
            try:
                await bot.send_message(
                    target_user_id,
                    "👤 Вас удалили из админ-группы бота AntBot."
                )
            except:
                pass
                
        except Exception as e:
            await message.answer(f"❌ Не удалось удалить из группы: {e}")
            
    except Exception as e:
        logger.error(f"Ошибка при удалении админа: {e}")
        await message.answer(f"❌ Ошибка: {e}")


# ====================== ТЕСТ-РЕЖИМ ДЛЯ АДМИНОВ ======================
# Глобальное хранилище: user_id -> True (если включен тест-режим)
ADMIN_TEST_MODE = {}
TEST_MODE_INTERVAL_MINUTES = 5  # Интервал в тест-режиме (минут)

@dp.message(Command("test_mode"))
async def cmd_test_mode(message: types.Message):
    """
    Переключает тест-режим для админа: 5 минут между уроками вместо 12 часов.
    ТОЛЬКО ДЛЯ АДМИНОВ!
    """
    user_id = message.from_user.id
    
    # ПРОВЕРКА: только суперадмины
    if user_id not in ADMIN_IDS_CONF:
        await message.answer(
            "❌ Эта команда доступна только суперадминам",
            parse_mode=None
        )
        logger.warning(f"Пользователь {user_id} пытался включить тест-режим (НЕ админ)")
        return
    
    if user_id in ADMIN_TEST_MODE:
        # Выключаем
        del ADMIN_TEST_MODE[user_id]
        await message.answer(
            "❌ Тест-режим ВЫКЛЮЧЕН\n\n"
            f"🕐 Теперь интервал между уроками: 12 часов (как обычно)",
            parse_mode=None
        )
        logger.info(f"Тест-режим ВЫКЛЮЧЕН для админа {user_id}")
    else:
        # Включаем
        ADMIN_TEST_MODE[user_id] = True
        await message.answer(
            "✅ Тест-режим ВКЛЮЧЕН\n\n"
            f"⚡ Интервал между уроками: {TEST_MODE_INTERVAL_MINUTES} минут\n"
            "Идеально для быстрой проверки курса!\n\n"
            "Чтобы выключить: /test_mode",
            parse_mode=None
        )
        logger.info(f"Тест-режим ВКЛЮЧЕН для админа {user_id}")

# ====================== КОНЕЦ ТЕСТ-РЕЖИМА ======================


@dp.message(Command("set_hw_timeout"))
async def cmd_set_hw_timeout(message: types.Message):
    """Установить таймаут AI-проверки ДЗ (только админы)"""
    global HW_TIMEOUT_SECONDS
    
    if not await is_admin(message.from_user.id):
        await message.answer("❌ Только для администраторов.")
        return
    
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                f"Текущий таймаут: {HW_TIMEOUT_SECONDS} сек\n\n"
                f"Использование: /set_hw_timeout <секунды>\n"
                f"Пример: /set_hw_timeout 60"
            )
            return
        
        try:
            new_timeout = int(args[1])
            if new_timeout < 10 or new_timeout > 600:
                await message.answer("❌ Таймаут должен быть от 10 до 600 секунд")
                return
            
            old_timeout = HW_TIMEOUT_SECONDS
            HW_TIMEOUT_SECONDS = new_timeout
            
            await message.answer(f"✅ Таймаут AI-проверки изменён: {old_timeout} → {new_timeout} сек")
            logger.info(f"HW_TIMEOUT изменён: {old_timeout} -> {new_timeout} сек (by {message.from_user.id})")
            
        except ValueError:
            await message.answer("❌ Укажите число секунд")
            
    except Exception as e:
        logger.error(f"Ошибка при установке таймаута: {e}")
        await message.answer(f"❌ Ошибка: {e}")


async def show_lessons_list(user_id: int, chat_id: int, message_id: int = None):
    """Универсальная функция показа списка уроков с группировкой"""
    logger.info(f"show_lessons_list: user_id={user_id}, chat_id={chat_id}")
    
    if not await is_admin(user_id):
        if message_id:
            await bot.edit_message_text("❌ Только для администраторов.", chat_id=chat_id, message_id=message_id)
        else:
            await bot.send_message(chat_id, "❌ Только для администраторов.")
        return
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем все записи и группируем в Python для гибкости
            cursor = await conn.execute('''
                SELECT course_id, lesson_num, content_type, is_homework
                FROM group_messages 
                WHERE lesson_num IS NOT NULL
                ORDER BY course_id, lesson_num
            ''')
            rows = await cursor.fetchall()
            
            logger.info(f"show_lessons_list: найдено {len(rows)} частей")
            if not rows:
                text = "📭 Пока нет загруженных уроков."
                if message_id:
                    await bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)
                else:
                    await bot.send_message(chat_id, text)
                return
            
            # Группируем по урокам
            lessons = {}  # (course_id, lesson_num) -> {types: set(), has_homework: bool, count: int}
            for row in rows:
                course_id, lesson_num, content_type, is_homework = row
                key = (course_id, lesson_num)
                if key not in lessons:
                    lessons[key] = {'types': set(), 'has_homework': False, 'count': 0}
                lessons[key]['types'].add(content_type)
                lessons[key]['has_homework'] = lessons[key]['has_homework'] or is_homework
                lessons[key]['count'] += 1
            
            # Эмодзи для типов контента
            type_emojis = {
                'text': '📝',
                'photo': '📷',
                'video': '🎬',
                'video_note': '🎯',
                'document': '📄'
            }
            
            result = f"📚 Загруженные уроки (всего уроков: {len(lessons)}):\n\n"
            MAX_BUTTONS = 40
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            
            # Сортируем уроки
            sorted_lessons = sorted(lessons.items(), key=lambda x: (x[0][0], x[0][1]))
            
            for i, (key, data) in enumerate(sorted_lessons):
                course_id, lesson_num = key
                types_str = ''.join(type_emojis.get(t, '❓') for t in sorted(data['types']))
                hw_marker = " 🏠" if data['has_homework'] else ""
                count_marker = f" ({data['count']} ч.)" if data['count'] > 1 else ""
                
                result += f"• {course_id} - Урок {lesson_num}{count_marker} {types_str}{hw_marker}\n"
                
                if i < MAX_BUTTONS:
                    keyboard.inline_keyboard.append([
                        InlineKeyboardButton(
                            text=f"👁️ {course_id[:8]}-{lesson_num}",
                            callback_data=ViewLessonCallback(course_id=course_id, lesson_num=lesson_num).pack()
                        ),
                        InlineKeyboardButton(
                            text=f"🗑️",
                            callback_data=LessonNavCallback(course_id=course_id, lesson_num=lesson_num, action="delete_part").pack()
                        )
                    ])
            
            if len(sorted_lessons) > MAX_BUTTONS:
                result += f"\n⚠️ Показаны кнопки только для первых {MAX_BUTTONS} уроков."
            
            # Добавляем кнопки управления
            keyboard.inline_keyboard.append([])  # Пустая строка
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(text="🗑️ Удалить курс", callback_data="delete_course_menu"),
                InlineKeyboardButton(text="🗑️ Очистить всё", callback_data="delete_all_lessons_confirm")
            ])
            
            if message_id:
                await bot.edit_message_text(result, chat_id=chat_id, message_id=message_id, reply_markup=keyboard)
            else:
                await bot.send_message(chat_id, result, reply_markup=keyboard)
                
    except Exception as e:
        logger.error(f"Ошибка show_lessons_list: {e}")
        if message_id:
            await bot.edit_message_text(f"❌ Ошибка: {e}", chat_id=chat_id, message_id=message_id)
        else:
            await bot.send_message(chat_id, f"❌ Ошибка: {e}")


@dp.callback_query(BackToListCallback.filter())
async def callback_back_to_list(callback: CallbackQuery):
    """Возврат к списку уроков"""
    logger.info(f"callback_back_to_list START: user_id={callback.from_user.id}")
    await callback.answer()
    logger.info(f"callback_back_to_list SUCCESS: возвращаем к списку уроков")
    await show_lessons_list(callback.from_user.id, callback.message.chat.id, callback.message.message_id)


@dp.callback_query(lambda c: c.data == "delete_all_lessons_confirm")
async def callback_delete_all_confirm(callback: CallbackQuery):
    """Подтверждение удаления всех уроков"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить ВСЁ", callback_data="delete_all_lessons_execute"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="list_lessons_menu")
        ]
    ])
    
    await callback.message.edit_text(
        "⚠️ ВНИМАНИЕ!\n\n"
        "Вы уверены, что хотите удалить ВСЕ уроки из базы данных?\n\n"
        "Это действие НЕОБРАТИМО!",
        reply_markup=keyboard
    )


@dp.callback_query(lambda c: c.data == "delete_all_lessons_execute")
async def callback_delete_all_execute(callback: CallbackQuery):
    """Выполнение удаления всех уроков"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM group_messages")
            count_before = (await cursor.fetchone())[0]
            
            await conn.execute("DELETE FROM group_messages")
            await conn.commit()
            
            logger.info(f"callback_delete_all_execute: удалено {count_before} уроков")
        
        await callback.message.edit_text(f"✅ Удалено {count_before} записей уроков из базы данных.")
        
    except Exception as e:
        logger.error(f"Ошибка при удалении всех уроков: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "delete_course_menu")
async def callback_delete_course_menu(callback: CallbackQuery):
    """Меню выбора курса для удаления"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT DISTINCT course_id FROM group_messages ORDER BY course_id")
            courses = await cursor.fetchall()
        
        if not courses:
            await callback.message.edit_text("📭 Нет курсов с уроками.")
            return
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[])
        for (course_id,) in courses:
            keyboard.inline_keyboard.append([
                InlineKeyboardButton(
                    text=f"🗑️ {course_id}",
                    callback_data=f"delete_course_confirm:{course_id}"
                )
            ])
        keyboard.inline_keyboard.append([
            InlineKeyboardButton(text="🔙 Назад", callback_data="list_lessons_menu")
        ])
        
        await callback.message.edit_text(
            "🗑️ Выберите курс для удаления всех его уроков:",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка при показе меню удаления курса: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data.startswith("delete_course_confirm:"))
async def callback_delete_course_confirm(callback: CallbackQuery):
    """Подтверждение удаления курса"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    course_id = callback.data.split(":")[1]
    await callback.answer()
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM group_messages WHERE course_id = ?",
                (course_id,)
            )
            count = (await cursor.fetchone())[0]
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить",
                    callback_data=f"delete_course_execute:{course_id}"
                ),
                InlineKeyboardButton(text="❌ Отмена", callback_data="delete_course_menu")
            ]
        ])
        
        await callback.message.edit_text(
            f"⚠️ Удалить все уроки курса '{course_id}'?\n\n"
            f"Количество записей: {count}\n\n"
            f"Это действие НЕОБРАТИМО!",
            reply_markup=keyboard
        )
        
    except Exception as e:
        logger.error(f"Ошибка при подтверждении удаления курса: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data.startswith("delete_course_execute:"))
async def callback_delete_course_execute(callback: CallbackQuery):
    """Выполнение удаления курса"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    course_id = callback.data.split(":")[1]
    await callback.answer()
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM group_messages WHERE course_id = ?",
                (course_id,)
            )
            count = (await cursor.fetchone())[0]
            
            await conn.execute("DELETE FROM group_messages WHERE course_id = ?", (course_id,))
            await conn.commit()
            
            logger.info(f"callback_delete_course_execute: удалено {count} уроков курса {course_id}")
        
        await callback.message.edit_text(f"✅ Удалено {count} записей курса '{course_id}'.")
        
    except Exception as e:
        logger.error(f"Ошибка при удалении курса: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")


@dp.callback_query(ViewLessonCallback.filter())
async def callback_view_lesson(callback: CallbackQuery, callback_data: ViewLessonCallback):
    """Показать содержимое урока с улучшенным интерфейсом управления"""
    logger.info(f"callback_view_lesson START: user_id={callback.from_user.id}, course_id={callback_data.course_id}, lesson_num={callback_data.lesson_num}")
    
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем части урока
            cursor = await conn.execute('''
                SELECT course_id, lesson_num, content_type, text, file_id, level, is_homework
                FROM group_messages 
                WHERE course_id = ? AND lesson_num = ?
                ORDER BY level
            ''', (callback_data.course_id, callback_data.lesson_num))
            
            rows = await cursor.fetchall()
            
            # Проверяем наличие домашки
            cursor_hw = await conn.execute('''
                SELECT 1 FROM group_messages 
                WHERE course_id = ? AND lesson_num = ? AND is_homework = 1
                LIMIT 1
            ''', (callback_data.course_id, callback_data.lesson_num))
            has_homework = await cursor_hw.fetchone() is not None
            
            # Проверяем общее количество уроков в курсе
            cursor_count = await conn.execute('''
                SELECT MAX(lesson_num) FROM group_messages 
                WHERE course_id = ?
            ''', (callback_data.course_id,))
            max_lesson = await cursor_count.fetchone()
            max_lesson_num = max_lesson[0] if max_lesson and max_lesson[0] else callback_data.lesson_num
        
        logger.info(f"callback_view_lesson: получено {len(rows)} частей урока, has_homework={has_homework}")
        
        if not rows:
            await callback.message.edit_text("❌ Урок не найден.")
            return
        
        course_id = rows[0][0]
        lesson_num = rows[0][1]
        parts_count = len(rows)
        
        # Формируем заголовок
        hw_status = "✅ ДЗ есть" if has_homework else "❌ Нет ДЗ"
        result = f"📚 **Урок {lesson_num}** курса *{course_id}*\n"
        result += f"📊 Частей: {parts_count} | {hw_status}\n\n"
        
        # Показываем содержимое частей (компактно)
        for i, row in enumerate(rows, 1):
            content_type, text, file_id, level, is_hw = row[2], row[3], row[4], row[5], row[6]
            hw_marker = " 🏠" if is_hw else ""
            
            if content_type == 'text' and text:
                char_count = len(text)
                word_count = len(text.split())
                # Показываем первые 100 символов с индикатором
                preview = text[:100] if len(text) > 100 else text
                truncated = "…" if len(text) > 100 else ""
                result += f"📝 Часть {i}{hw_marker}: Текст ({char_count} симв., {word_count} сл.){truncated}\n"
                result += f"_{preview}_\n\n"
            elif content_type == 'photo' and file_id:
                result += f"📷 Часть {i}{hw_marker}: [Фото]\n"
            elif content_type == 'video' and file_id:
                result += f"🎬 Часть {i}{hw_marker}: [Видео]\n"
            elif content_type == 'video_note' and file_id:
                result += f"🎯 Часть {i}{hw_marker}: [Кружок]\n"
            elif content_type == 'document' and file_id:
                result += f"📄 Часть {i}{hw_marker}: [Документ]\n"
        
        # Формируем клавиатуру управления
        keyboard_rows = []
        
        # Навигация между уроками
        nav_buttons = []
        if lesson_num > 1:
            nav_buttons.append(InlineKeyboardButton(
                text="⬅️ Предыдущий",
                callback_data=LessonNavCallback(course_id=course_id, lesson_num=lesson_num-1, action="nav").pack()
            ))
        nav_buttons.append(InlineKeyboardButton(
            text=f"📚 {lesson_num}/{max_lesson_num}",
            callback_data="lesson_num_info"
        ))
        if lesson_num < max_lesson_num:
            nav_buttons.append(InlineKeyboardButton(
                text="Следующий ➡️",
                callback_data=LessonNavCallback(course_id=course_id, lesson_num=lesson_num+1, action="nav").pack()
            ))
        if nav_buttons:
            keyboard_rows.append(nav_buttons)
        
        # Управление частями
        if parts_count > 0:
            keyboard_rows.append([InlineKeyboardButton(
                text=f"🗑️ Удалить часть ({parts_count})",
                callback_data=LessonNavCallback(course_id=course_id, lesson_num=lesson_num, action="delete_part").pack()
            )])
        
        # Добавление контента
        keyboard_rows.append([
            InlineKeyboardButton(text="📝 Текст", callback_data=LessonAddContentCallback(course_id=course_id, lesson_num=lesson_num, content_type="text").pack()),
            InlineKeyboardButton(text="📷 Фото", callback_data=LessonAddContentCallback(course_id=course_id, lesson_num=lesson_num, content_type="photo").pack()),
            InlineKeyboardButton(text="🎬 Видео", callback_data=LessonAddContentCallback(course_id=course_id, lesson_num=lesson_num, content_type="video").pack()),
        ])
        keyboard_rows.append([
            InlineKeyboardButton(text="🎯 Кружок", callback_data=LessonAddContentCallback(course_id=course_id, lesson_num=lesson_num, content_type="video_note").pack()),
            InlineKeyboardButton(text="📄 Документ", callback_data=LessonAddContentCallback(course_id=course_id, lesson_num=lesson_num, content_type="document").pack()),
        ])
        
        # Управление домашкой
        hw_text = "✏️ Редактировать ДЗ" if has_homework else "➕ Добавить ДЗ"
        keyboard_rows.append([InlineKeyboardButton(
            text=hw_text,
            callback_data=LessonNavCallback(course_id=course_id, lesson_num=lesson_num, action="manage_homework").pack()
        )])
        
        # Возврат к списку
        keyboard_rows.append([InlineKeyboardButton(text="🔙 К списку уроков", callback_data=BackToListCallback().pack())])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
        
        await callback.message.edit_text(result, reply_markup=keyboard, parse_mode=None)
        
    except Exception as e:
        logger.error(f"Ошибка просмотра урока: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")


@dp.callback_query(lambda c: c.data == "lesson_num_info")
async def callback_lesson_num_info(callback: CallbackQuery):
    """Обработка клика на информационную кнопку с номером урока"""
    await callback.answer("Текущий урок", show_alert=False)


@dp.callback_query(LessonNavCallback.filter())
async def callback_lesson_nav(callback: CallbackQuery, callback_data: LessonNavCallback, state: FSMContext):
    """Обработка навигации по урокам"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    
    action = callback_data.action
    course_id = callback_data.course_id
    lesson_num = callback_data.lesson_num
    
    if action == "nav":
        # Переход к другому уроку
        logger.info(f"lesson_nav: переход к уроку {lesson_num} курса {course_id}")
        # Создаем фейковый ViewLessonCallback и вызываем callback_view_lesson
        view_callback = ViewLessonCallback(course_id=course_id, lesson_num=lesson_num)
        await callback_view_lesson(callback, view_callback)
    
    elif action == "delete_part":
        # Показываем список частей для удаления
        logger.info(f"lesson_nav: удаление части урока {lesson_num} курса {course_id}")
        try:
            async with aiosqlite.connect(DB_FILE) as conn:
                cursor = await conn.execute('''
                    SELECT level, content_type, text, file_id 
                    FROM group_messages 
                    WHERE course_id = ? AND lesson_num = ?
                    ORDER BY level
                ''', (course_id, lesson_num))
                rows = await cursor.fetchall()
            
            if not rows:
                await callback.message.edit_text("❌ Урок пуст.")
                return
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[])
            for i, row in enumerate(rows, 1):
                level, content_type, text, file_id = row
                content_emoji = {'text': '📝', 'photo': '📷', 'video': '🎬', 'video_note': '🎯', 'document': '📄'}.get(content_type, '❓')
                keyboard.inline_keyboard.append([
                    InlineKeyboardButton(
                        text=f"{content_emoji} Часть {i} (уровень {level})",
                        callback_data=DeleteLessonPartCallback(course_id=course_id, lesson_num=lesson_num, part_num=level, action="confirm").pack()
                    )
                ])
            
            keyboard.inline_keyboard.append([InlineKeyboardButton(
                text="🔙 Назад к уроку",
                callback_data=ViewLessonCallback(course_id=course_id, lesson_num=lesson_num).pack()
            )])
            
            await callback.message.edit_text(
                f"🗑️ Выберите часть для удаления:\n\nУрок {lesson_num} курса {course_id}",
                reply_markup=keyboard
            )
            
        except Exception as e:
            logger.error(f"Ошибка при показе частей для удаления: {e}")
            await callback.message.edit_text(f"❌ Ошибка: {e}")
    
    elif action == "manage_homework":
        # Переходим к управлению домашкой
        logger.info(f"lesson_nav: управление домашкой урока {lesson_num} курса {course_id}")
        await state.set_state(ManageHomeworkFSM.waiting_homework_content)
        await state.update_data(course_id=course_id, lesson_num=lesson_num)
        
        # Показываем текущую домашку если есть
        try:
            async with aiosqlite.connect(DB_FILE) as conn:
                cursor = await conn.execute('''
                    SELECT level, content_type, text, file_id 
                    FROM group_messages 
                    WHERE course_id = ? AND lesson_num = ? AND is_homework = 1
                    ORDER BY level
                ''', (course_id, lesson_num))
                hw_rows = await cursor.fetchall()
            
            if hw_rows:
                result = f"✏️ Текущее домашнее задание (урок {lesson_num}):\n\n"
                for i, row in enumerate(hw_rows, 1):
                    content_type, text, file_id = row[1], row[2], row[3]
                    result += f"{i}. {content_type}: "
                    if text:
                        result += f"{text[:100]}...\n" if len(text) > 100 else f"{text}\n"
                    else:
                        result += "[медиа]\n"
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑️ Удалить ДЗ", callback_data=f"hw_delete:{course_id}:{lesson_num}")],
                    [InlineKeyboardButton(text="🔙 Назад к уроку", callback_data=ViewLessonCallback(course_id=course_id, lesson_num=lesson_num).pack())]
                ])
                
                await callback.message.edit_text(result + "\n📝 Отправьте новое содержимое ДЗ (текст, фото, видео или документ):", reply_markup=keyboard)
            else:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 Назад к уроку", callback_data=ViewLessonCallback(course_id=course_id, lesson_num=lesson_num).pack())]
                ])
                await callback.message.edit_text(
                    f"➕ Добавление домашнего задания к уроку {lesson_num}\n\n"
                    f"📝 Отправьте содержимое ДЗ (текст, фото, видео или документ):",
                    reply_markup=keyboard
                )
        except Exception as e:
            logger.error(f"Ошибка при управлении домашкой: {e}")
            await callback.message.edit_text(f"❌ Ошибка: {e}")


@dp.callback_query(LessonAddContentCallback.filter())
async def callback_lesson_add_content(callback: CallbackQuery, callback_data: LessonAddContentCallback, state: FSMContext):
    """Начало добавления контента в урок"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    
    course_id = callback_data.course_id
    lesson_num = callback_data.lesson_num
    content_type = callback_data.content_type
    
    logger.info(f"lesson_add_content: {content_type} в урок {lesson_num} курса {course_id}")
    
    # Устанавливаем состояние
    await state.set_state(AddContentFSM.waiting_content)
    await state.update_data(
        course_id=course_id,
        lesson_num=lesson_num,
        content_type=content_type,
        is_homework=False
    )
    
    content_type_names = {
        'text': 'текст',
        'photo': 'фото',
        'video': 'видео',
        'video_note': 'кружок (видео-сообщение)',
        'document': 'документ'
    }
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Отмена", callback_data=ViewLessonCallback(course_id=course_id, lesson_num=lesson_num).pack())]
    ])
    
    await callback.message.edit_text(
        f"➕ Добавление {content_type_names.get(content_type, content_type)} в урок {lesson_num}\n\n"
        f"📤 Отправьте {content_type_names.get(content_type, content_type)}:",
        reply_markup=keyboard
    )


@dp.message(Command("delete_lesson_part"))
async def cmd_delete_lesson_part(message: types.Message, command: CommandObject):
    """Удаление части урока"""
    logger.info(f"cmd_delete_lesson_part START: user_id={message.from_user.id}, args={command.args}")
    
    if message.from_user.id not in ADMIN_IDS_CONF:
        await message.answer("❌ Только для администраторов.")
        return
    
    args = command.args.split() if command.args else []
    if len(args) != 3:
        await message.answer("❌ Неверный формат. Используйте: /delete_lesson_part <course_id> <lesson_num> <part_num>")
        return
    
    course_id = args[0]
    lesson_num = int(args[1])
    part_num = int(args[2])
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверяем что часть существует
            cursor = await conn.execute('''
                SELECT course_id, lesson_num, content_type, text, file_id 
                FROM group_messages 
                WHERE course_id = ? AND lesson_num = ? AND level = ?
            ''', (course_id, lesson_num, part_num))
            
            row = await cursor.fetchone()
            if not row:
                await message.answer(f"❌ Часть {part_num} урока {lesson_num} курса {course_id} не найдена.")
                return
            
            # Удаляем часть
            await conn.execute('''
                DELETE FROM group_messages 
                WHERE course_id = ? AND lesson_num = ? AND level = ?
            ''', (course_id, lesson_num, part_num))
            
            await conn.commit()
            logger.info(f"cmd_delete_lesson_part: удалена часть {part_num} урока {lesson_num} курса {course_id}")
            await message.answer(f"✅ Часть {part_num} урока {lesson_num} курса {course_id} удалена.")
            
    except Exception as e:
        logger.error(f"Ошибка удаления части урока: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.callback_query(DeleteLessonPartCallback.filter())
async def callback_delete_lesson_part(callback: CallbackQuery, callback_data: DeleteLessonPartCallback):
    """Подтверждение и удаление части урока"""
    logger.info(f"callback_delete_lesson_part START: user_id={callback.from_user.id}, course_id={callback_data.course_id}, lesson_num={callback_data.lesson_num}, part_num={callback_data.part_num}, action={callback_data.action}")
    
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем инфо о части
            cursor = await conn.execute('''
                SELECT course_id, lesson_num, level, content_type, text, file_id 
                FROM group_messages 
                WHERE course_id = ? AND lesson_num = ? AND level = ?
            ''', (callback_data.course_id, callback_data.lesson_num, callback_data.part_num))
            
            row = await cursor.fetchone()
            if not row:
                await callback.message.edit_text("❌ Часть урока не найдена.")
                return
            
            course_id, lesson_num, level, content_type, text, file_id = row
            
            # Если action = "confirm" - удаляем часть
            if callback_data.action == "confirm":
                await conn.execute('''
                    DELETE FROM group_messages 
                    WHERE course_id = ? AND lesson_num = ? AND level = ?
                ''', (course_id, lesson_num, level))
                await conn.commit()
                logger.info(f"cmd_delete_lesson_part: удалена часть {level} урока {lesson_num} курса {course_id}")
                
                await callback.message.edit_text(f"✅ Часть {level} урока {lesson_num} курса {course_id} удалена.")
                logger.info(f"cmd_delete_lesson_part SUCCESS: часть {level} урока {lesson_num} курса {course_id} удалена")
            
            # action не указан - показываем подтверждение
            else:
                content_type_ru = {
                    'text': '📝 Текст',
                    'photo': '📷 Фото',
                    'video': '🎬 Видео',
                    'document': '📄 Документ'
                }.get(content_type, '❓ Неизвестно')
                
                result = (
                    f"📚 Удалить часть {level} урока {lesson_num} курса {course_id}?\n\n"
                    f"📌 Тип: {content_type_ru}\n\n"
                )
                
                if text:
                    result += f"📝 Текст:\n{text[:100]}{'...' if len(text) > 100 else ''}\n\n"
                if file_id:
                    result += f"📎 File ID: `{file_id}`\n\n"
                
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(
                        text="✅ Да, удалить", 
                        callback_data=DeleteLessonPartCallback(
                            course_id=course_id, lesson_num=lesson_num, part_num=level, action="confirm"
                        ).pack()
                    )],
                    [InlineKeyboardButton(text="❌ Отмена", callback_data=BackToListCallback().pack())]
                ])
                
                await callback.message.edit_text(result, reply_markup=keyboard)
                logger.info(f"callback_delete_lesson_part: запрос на удаление части {level} урока {lesson_num} курса {course_id}")
                
    except Exception as e:
        logger.error(f"Ошибка в callback_delete_lesson_part: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")


@dp.message(AddContentFSM.waiting_content)
async def process_add_content(message: types.Message, state: FSMContext):
    """Обработка добавления контента в урок"""
    if message.from_user.id not in ADMIN_IDS_CONF:
        await message.answer("❌ Только для администраторов.")
        await state.clear()
        return
    
    data = await state.get_data()
    course_id = data.get('course_id')
    lesson_num = data.get('lesson_num')
    content_type = data.get('content_type')
    is_homework = data.get('is_homework', False)
    
    logger.info(f"process_add_content: добавление {content_type} в урок {lesson_num} курса {course_id}, is_homework={is_homework}")
    
    try:
        # Определяем максимальный level для этого урока
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute('''
                SELECT MAX(level) FROM group_messages 
                WHERE course_id = ? AND lesson_num = ?
            ''', (course_id, lesson_num))
            max_level = await cursor.fetchone()
            new_level = (max_level[0] or 0) + 1
            
            # Получаем file_id и text в зависимости от типа контента
            file_id = None
            text_content = None
            
            if content_type == 'text':
                text_content = message.text or message.caption
            elif content_type == 'photo':
                if message.photo:
                    file_id = message.photo[-1].file_id
                text_content = message.caption
            elif content_type == 'video':
                if message.video:
                    file_id = message.video.file_id
                text_content = message.caption
            elif content_type == 'video_note':
                if message.video_note:
                    file_id = message.video_note.file_id
            elif content_type == 'document':
                if message.document:
                    file_id = message.document.file_id
                text_content = message.caption
            
            # Сохраняем в БД (message_id = 0 для direct_upload)
            await conn.execute('''
                INSERT INTO group_messages (group_id, course_id, lesson_num, content_type, file_id, text, level, is_homework, timestamp, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', ('direct_upload', course_id, lesson_num, content_type, file_id, text_content, new_level, is_homework, datetime.now().isoformat(), 0))
            
            await conn.commit()
            
            content_type_names = {
                'text': 'текст', 'photo': 'фото', 'video': 'видео',
                'video_note': 'кружок', 'document': 'документ'
            }
            
            hw_text = " (домашнее задание)" if is_homework else ""
            
            # Формируем статистику для текста
            stats_text = ""
            if content_type == 'text' and text_content:
                stats_text = f" ({len(text_content)} симв., {len(text_content.split())} сл.)"
            
            await message.answer(
                f"✅ {content_type_names.get(content_type, content_type).capitalize()}{hw_text}{stats_text} добавлен!\n"
                f"Урок {lesson_num}, часть {new_level}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👁️ Просмотреть урок", callback_data=ViewLessonCallback(course_id=course_id, lesson_num=lesson_num).pack())]
                ])
            )
            
            logger.info(f"process_add_content: успешно добавлено в урок {lesson_num} курса {course_id}, level={new_level}")
            
    except Exception as e:
        logger.error(f"Ошибка добавления контента: {e}")
        await message.answer(f"❌ Ошибка добавления контента: {e}")
    
    finally:
        await state.clear()


@dp.message(ManageHomeworkFSM.waiting_homework_content)
async def process_manage_homework(message: types.Message, state: FSMContext):
    """Обработка добавления/изменения домашнего задания"""
    if message.from_user.id not in ADMIN_IDS_CONF:
        await message.answer("❌ Только для администраторов.")
        await state.clear()
        return
    
    data = await state.get_data()
    course_id = data.get('course_id')
    lesson_num = data.get('lesson_num')
    
    logger.info(f"process_manage_homework: обновление домашки для урока {lesson_num} курса {course_id}")
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Удаляем старую домашку если есть
            await conn.execute('''
                DELETE FROM group_messages 
                WHERE course_id = ? AND lesson_num = ? AND is_homework = 1
            ''', (course_id, lesson_num))
            
            # Определяем тип контента и получаем file_id/text
            content_type = 'text'
            file_id = None
            text_content = message.text or message.caption
            
            if message.photo:
                content_type = 'photo'
                file_id = message.photo[-1].file_id
            elif message.video:
                content_type = 'video'
                file_id = message.video.file_id
            elif message.video_note:
                content_type = 'video_note'
                file_id = message.video_note.file_id
            elif message.document:
                content_type = 'document'
                file_id = message.document.file_id
            
            # Добавляем новую домашку (message_id = 0 для direct_upload)
            await conn.execute('''
                INSERT INTO group_messages (group_id, course_id, lesson_num, content_type, file_id, text, level, is_homework, timestamp, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', ('direct_upload', course_id, lesson_num, content_type, file_id, text_content, 1, True, datetime.now().isoformat(), 0))
            
            await conn.commit()
            
            await message.answer(
                f"✅ Домашнее задание обновлено для урока {lesson_num} курса {course_id}!",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👁️ Просмотреть урок", callback_data=ViewLessonCallback(course_id=course_id, lesson_num=lesson_num).pack())]
                ])
            )
            
            logger.info(f"process_manage_homework: домашка обновлена для урока {lesson_num} курса {course_id}")
            
    except Exception as e:
        logger.error(f"Ошибка обновления домашки: {e}")
        await message.answer(f"❌ Ошибка: {e}")
    
    finally:
        await state.clear()


@dp.message(Command("remind"))
async def cmd_remind(message: types.Message, command: CommandObject):
    """Напоминание пользователю"""
    logger.info(f"cmd_remind START: user_id={message.from_user.id}, args={command.args}")
    
    if message.from_user.id not in ADMIN_IDS_CONF:
        await message.answer("❌ Только для администраторов.")
        return
    
    args = command.args.split() if command.args else []
    if len(args) != 2:
        await message.answer("❌ Неверный формат. Используйте: /remind <user_id> <message>")
        return
    
    user_id = int(args[0])
    remind_message = args[1]
    
    try:
        # Формируем ссылку на пользователя
        user_link = f"[tg://resolve?domain=user&id={user_id}]"
        
        await bot.send_message(chat_id=user_id, text=f"⏰ Напоминание:\n\n{remind_message}")
        await message.answer(f"✅ Сообщение отправлено пользователю {user_id}")
        logger.info(f"cmd_remind SUCCESS: отправлено напоминание user_id={user_id}, msg={remind_message[:100]}")
        
    except Exception as e:
        logger.error(f"Ошибка отправки напоминания: {e}")
        await message.answer(f"❌ Ошибка: {e}")

# =========================== КОНЕЦ ЗАГРУЗКИ УРОКОВ ===========================


async def update_settings_file():
    """
    Обновляет файл settings.json - ТОЛЬКО добавляет новые данные из БД.
    НЕ затирает существующие поля!
    
    ВАЖНО: Эта функция теперь только добавляет, никогда не удаляет.
    """
    try:
        # Проверяем что settings.json не является директорией
        if os.path.isdir("settings.json"):
            logger.warning("⚠️ settings.json существует как директория, удаляю...")
            try:
                shutil.rmtree("settings.json")
                logger.info("✅ Директория settings.json удалена")
            except Exception as e_cleanup:
                logger.error(f"❌ Ошибка при удалении директории settings.json: {e_cleanup}")
                return  # Прерываем, не создаем новый файл

        # Загружаем текущие настройки
        current_settings = {}
        try:
            if os.path.isfile("settings.json"):
                with open("settings.json", "r", encoding="utf-8") as f:
                    current_settings = json.load(f)
                    logger.info(f"✅ settings.json загружен: {len(current_settings)} полей")
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.warning(f"⚠️ settings.json не найден или повреждён: {e}")
            # НЕ создаем новый файл автоматически! Пусть админ сам создаст.
            return

        # Сохраняем ВСЕ существующие поля как есть
        settings = {
            "message_interval": current_settings.get("message_interval", 12),
            "tariff_names": current_settings.get("tariff_names", {
                "v1": "Соло",
                "v2": "с проверкой",
                "v3": "Премиум"
            }),
            "groups": current_settings.get("groups", {}),
            "activation_codes": current_settings.get("activation_codes", {}),
            "course_descriptions": current_settings.get("course_descriptions", {})
        }

        # Добавляем ТОЛЬКО новые курсы из БД (если их нет в settings)
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT course_id, group_id FROM courses")
            courses_db = await cursor.fetchall()

            added_any = False
            for course_id, group_id in courses_db:
                # Добавляем в groups только если нет
                if group_id not in settings["groups"]:
                    settings["groups"][group_id] = course_id
                    added_any = True
                    logger.info(f"➕ Добавлен курс в settings: {group_id} → {course_id}")

            if added_any:
                with open("settings.json", "w", encoding="utf-8") as f:
                    json.dump(settings, f, ensure_ascii=False, indent=4)
                logger.info("✅ settings.json обновлён (добавлены новые курсы)")
            else:
                logger.info("ℹ️ settings.json не изменён (все курсы уже есть)")

    except Exception as e2291:
        logger.error(f"❌ Ошибка при обновлении settings.json: {e2291}", exc_info=True)



# ===============================  команды ИИ для работы с ДЗ  ===============================================================
# Вспомогательная функция для извлечения данных из сообщения по ID
async def old_get_homework_context_by_message_id(admin_group_message_id: int) -> tuple | None:
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
                            logger.debug("Кнопка не содержит подходящего callback_data в get_homework_context_by_message_id")
                            continue
        return None
    except Exception as e2315:
        logger.error(f"Ошибка при получении контекста ДЗ по message_id {admin_group_message_id}: {e2315}")
        return None

async def get_homework_context_by_message_id(admin_group_message_id: int) -> tuple | None:
    """Извлекает контекст ДЗ из таблицы pending_admin_homework по ID сообщения в админ-группе."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute(
                """SELECT student_user_id, course_numeric_id, lesson_num
                   FROM pending_admin_homework
                   WHERE admin_message_id = ?""",
                (admin_group_message_id,)
            )
            row = await cursor.fetchone()
            if row:
                # student_user_id, course_numeric_id, lesson_num
                return row[0], row[1], row[2]
            else:
                logger.warning(f"Контекст ДЗ не найден в pending_admin_homework для admin_message_id {admin_group_message_id}")
                return None
    except Exception as e:
        logger.error(f"Ошибка при получении контекста ДЗ из pending_admin_homework по message_id {admin_group_message_id}: {e}")
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
                    except Exception as e2339:
                        logger.debug(
                            f"Не удалось распаковать callback_data из кнопки в extract_homework_context_from_reply: {e2339}")
                        continue
    logger.warning("Не удалось извлечь контекст ДЗ из ответного сообщения (нет подходящих callback_data).")
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
        command_args: str | None,  # Это строка аргументов ПОСЛЕ самой команды
        is_approval: bool,
        bot: Bot  # <--- ДОБАВЛЯЕМ bot СЮДА
):
    admin_id = message.from_user.id
    user_id_student = None
    course_numeric_id_hw = None
    lesson_num_hw = None
    # feedback_text_hw инициализируется так, чтобы учесть случай отсутствия command_args
    feedback_text_hw = command_args.strip() if command_args else ""  # Если есть аргументы, берем их как фидбэк, иначе пустая строка

    original_bot_message_id_in_admin_group = None

    # Сценарий 1: Команда дана в ответ на сообщение бота с ДЗ
    if message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot.id:
        original_bot_message_in_admin_group = message.reply_to_message
        context_from_reply_markup = get_context_from_admin_message_markup(original_bot_message_in_admin_group)
        if context_from_reply_markup:
            user_id_student, course_numeric_id_hw, lesson_num_hw = context_from_reply_markup
            original_bot_message_id_in_admin_group = original_bot_message_in_admin_group.message_id

            # Если command_args пуст (то есть команда была просто /approve или /reject в reply),
            # а is_approval=False (т.е. reject), то ставим дефолтный фидбэк.
            # Если is_approval=True (approve) и command_args пуст, feedback_text_hw останется "".
            if not feedback_text_hw and not is_approval:
                feedback_text_hw = "Домашнее задание требует доработки."

            logger.info(
                f"Команда ({'/approve' if is_approval else '/reject'}) по REPLY от {admin_id}: "
                f"user={user_id_student}, c_id={course_numeric_id_hw}, l_num={lesson_num_hw}, "
                f"admin_msg_id={original_bot_message_id_in_admin_group}, feedback='{feedback_text_hw}'"
            )

    # Сценарий 2: Команда с аргументами (ID сообщения или полные данные + ВОЗМОЖНЫЙ ФИДБЭК)
    # command_args УЖЕ содержит ВСЮ строку после команды.
    # feedback_text_hw УЖЕ содержит эту строку (или пустую, если аргументов не было).
    # Нам нужно теперь из command_args извлечь префиксные аргументы (id, user, course, lesson)
    # и оставить остаток как фидбэк, ЕСЛИ user_id_student еще не определен (т.е. не было reply).

    if not user_id_student and command_args:  # Если не было reply, но есть аргументы
        args_list = command_args.split(maxsplit=3)  # Для user_id, course_id, lesson_num, [feedback]
        # или maxsplit=1 для message_id, [feedback]

        # Пытаемся как /cmd <message_id_в_админке> [фидбэк]
        if len(args_list) >= 1 and args_list[0].isdigit():
            potential_message_id = int(args_list[0])
            context_from_msg_id_db = await get_homework_context_by_message_id(potential_message_id)
            if context_from_msg_id_db:
                user_id_student, course_numeric_id_hw, lesson_num_hw = context_from_msg_id_db
                original_bot_message_id_in_admin_group = potential_message_id
                # Фидбэк - это все, что после ID сообщения
                current_feedback = args_list[1].strip() if len(args_list) > 1 and args_list[1] else ""
                if not current_feedback and not is_approval:
                    feedback_text_hw = "Домашнее задание требует доработки."
                else:
                    feedback_text_hw = current_feedback

                logger.info(
                    f"Команда ({'/approve' if is_approval else '/reject'}) по АРГУМЕНТУ MESSAGE_ID {potential_message_id} от {admin_id}: "
                    f"user={user_id_student}, c_id={course_numeric_id_hw}, l_num={lesson_num_hw}, feedback='{feedback_text_hw}'"
                )

        # Если не сработало как message_id, пытаемся как /cmd <user_id> <course_id> <lesson_num> [фидбэк]
        if not user_id_student and len(args_list) >= 3:  # Нужны как минимум user, course, lesson
            try:
                # Проверяем, являются ли первые три аргумента числами
                if args_list[0].isdigit() and args_list[1].isdigit() and args_list[2].isdigit():
                    user_id_student = int(args_list[0])
                    course_numeric_id_hw = int(args_list[1])
                    lesson_num_hw = int(args_list[2])
                    # Фидбэк - это четвертый элемент списка, если он есть
                    current_feedback = args_list[3].strip() if len(args_list) > 3 and args_list[3] else ""
                    if not current_feedback and not is_approval:
                        feedback_text_hw = "Домашнее задание требует доработки"
                    else:
                        feedback_text_hw = current_feedback

                    logger.info(
                        f"Команда ({'/approve' if is_approval else '/reject'}) по АРГУМЕНТАМ USER/COURSE/LESSON от {admin_id}: "
                        f"user={user_id_student}, c_id={course_numeric_id_hw}, l_num={lesson_num_hw}, feedback='{feedback_text_hw}'"
                    )
                else:  # Если первые три аргумента не числа, то это не тот формат
                    user_id_student = None  # Сброс
            except (ValueError, IndexError):
                user_id_student = None  # Сброс, если парсинг не удался

    # Сценарий 3: Команда БЕЗ reply и БЕЗ каких-либо распознаваемых аргументов в начале (т.е. command_args это просто фидбэк ИЛИ пусто)
    # ИЛИ если command_args был, но не подошел под форматы выше (message_id или user/course/lesson)
    # В этом случае, если user_id_student все еще None, пытаемся взять последнее ДЗ.
    if not user_id_student:
        # Если command_args был, но не распознался как префиксные аргументы, то он становится фидбэком
        # Если command_args не было, feedback_text_hw уже ""
        # feedback_text_hw уже содержит command_args.strip() if command_args else ""

        logger.info(
            f"Команда ({'/approve' if is_approval else '/reject'}) без явного контекста (reply/args). Ищем последнее ДЗ. Фидбэк из команды: '{feedback_text_hw}'")
        try:
            async with aiosqlite.connect(DB_FILE) as conn:
                cursor = await conn.execute(
                    """SELECT admin_message_id, student_user_id, course_numeric_id, lesson_num
                       FROM pending_admin_homework
                       WHERE admin_chat_id = ? 
                       ORDER BY admin_message_id DESC 
                       LIMIT 1""",
                    (message.chat.id,)
                )
                last_pending_hw_row = await cursor.fetchone()
                if last_pending_hw_row:
                    original_bot_message_id_in_admin_group, user_id_student, course_numeric_id_hw, lesson_num_hw = last_pending_hw_row

                    # Если из команды не пришел фидбэк, и это reject, ставим дефолтный
                    if not feedback_text_hw and not is_approval:
                        feedback_text_hw = "Домашнее задание требует доработки"

                    logger.info(
                        f"Найдено последнее ДЗ в pending_admin_homework для ({'/approve' if is_approval else '/reject'}): "
                        f"admin_msg_id={original_bot_message_id_in_admin_group}, user={user_id_student}, "
                        f"c_id={course_numeric_id_hw}, l_num={lesson_num_hw}, feedback='{feedback_text_hw}'"
                    )
                else:
                    logger.info(f"В pending_admin_homework нет ожидающих ДЗ для чата {message.chat.id}.")
                    # Если ДЗ не найдено, а фидбэк из команды был, это бессмысленно.
                    # user_id_student останется None.
        except Exception as e_fetch_last_pending:
            logger.error(f"Ошибка при извлечении последнего ДЗ из pending_admin_homework: {e_fetch_last_pending}")
            user_id_student = None  # Убедимся, что не продолжим с некорректными данными

    # Финальная обработка и отправка ответа
    if user_id_student and course_numeric_id_hw is not None and lesson_num_hw is not None:
        course_id_str_hw = await get_course_id_str(course_numeric_id_hw)
        await handle_homework_result(
            user_id=user_id_student,
            course_id=course_id_str_hw,
            course_numeric_id=course_numeric_id_hw,
            lesson_num=lesson_num_hw,
            admin_id=admin_id,
            feedback_text=feedback_text_hw,  # Передаем собранный фидбэк
            is_approved=is_approval,
            #bot=bot,
            callback_query=None,
            original_admin_message_id_to_delete=original_bot_message_id_in_admin_group
        )
        action_verb = "одобрено" if is_approval else "отклонено"
        student_info_for_reply = f"user {user_id_student} (курс {escape_md(course_id_str_hw)}, урок {lesson_num_hw})"
        reply_admin_text = f"✅ ДЗ для {student_info_for_reply} было {action_verb} командой."
        if feedback_text_hw:
            reply_admin_text += f"\nФидбэк: {escape_md(feedback_text_hw)}"

        await message.reply(escape_md(reply_admin_text), parse_mode=None)
    else:
        # Сообщение об ошибке, если контекст так и не был определен
        cmd_name_log = "/approve" if is_approval else "/reject"
        reply_text = (
            f"Не удалось определить ДЗ для команды `{cmd_name_log}`.\n"
            f"Убедитесь, что есть ожидающие проверки ДЗ, или используйте команду как ответ (reply) на сообщение с ДЗ, или укажите аргументы:\n"
            f"1. `{cmd_name_log} [фидбэк]` (в ответ на ДЗ)\n"
            f"2. `{cmd_name_log} <ID сообщения с ДЗ в этой группе> [фидбэк]`\n"
            f"3. `{cmd_name_log} <user_id студента> <course_num_id> <lesson_num> [фидбэк]`"
        )
        await message.reply(reply_text, parse_mode=None)


@dp.message(Command("approve"), F.chat.id == ADMIN_GROUP_ID)  # Используем вашу переменную ADMIN_GROUP_ID
async def cmd_approve_homework_handler(message: types.Message, command: CommandObject):
    logger.info(f"Получена команда /approve от админа {message.from_user.id}")
    await process_homework_command(message, command.args, is_approval=True, bot=bot)


@dp.message(Command("reject"), F.chat.id == ADMIN_GROUP_ID)  # Используем вашу переменную ADMIN_GROUP_ID
async def cmd_reject_homework_handler(message: types.Message, command: CommandObject):
    logger.info(f"Получена команда /reject от админа {message.from_user.id}")
    await process_homework_command(message, command.args, is_approval=False, bot=bot)


# Команды для взаимодействия с пользователем - в конце, аминь.
#=======================================================================================================================


@dp.callback_query(SelectLessonForRepeatCallback.filter())
async def cb_select_lesson_for_repeat_start(query: types.CallbackQuery, callback_data: SelectLessonForRepeatCallback,
                                            state: FSMContext):
    user_id = query.from_user.id
    course_numeric_id = callback_data.course_numeric_id
    course_id_str = await get_course_id_str(course_numeric_id)
    await query.answer("Загружаю содержание курса")

    if not course_id_str or course_id_str == "Неизвестный курс":
        await query.message.edit_text(escape_md("Ошибка: курс не найден."), parse_mode=None)
        return

    current_user_level = 1
    user_current_lesson_on_course = 0  # Последний отправленный урок на этом курсе/уровне
    async with aiosqlite.connect(DB_FILE) as conn_level:
        cursor_user_course_info = await conn_level.execute(
            "SELECT level, current_lesson FROM user_courses WHERE user_id = ? AND course_id = ?",
            # Статус не важен, можем смотреть и пройденные/остановленные
            (user_id, course_id_str)
        )
        user_info_row = await cursor_user_course_info.fetchone()
        if user_info_row:
            current_user_level, user_current_lesson_on_course = user_info_row
        else:  # Если нет записи в user_courses, возможно, это просмотр описания еще не начатого курса
            # В этом случае берем просто 1-й уровень для отображения контента
            logger.warning(
                f"Нет записи в user_courses для {user_id} и курса {course_id_str} при просмотре содержания. Показываю уровень 1.")

    lessons_buttons_builder = InlineKeyboardBuilder()
    lessons_text_list_for_message = [
        escape_md(f"Содержание курса «{await get_course_title(course_id_str)}» (уровень {current_user_level}):")]

    async with aiosqlite.connect(DB_FILE) as conn:
        # Используем SQL-запрос, который берет одну (первую) часть для каждого урока
        cursor_lessons = await conn.execute(
            """
            SELECT 
                gm.lesson_num, 
                COALESCE(NULLIF(gm.snippet, ''), SUBSTR(gm.text, 1, 50)) as lesson_title_raw
            FROM group_messages gm
            INNER JOIN (
                SELECT course_id, lesson_num, level, MIN(id) as min_id
                FROM group_messages
                WHERE course_id = ? AND lesson_num > 0 AND level = ?
                GROUP BY course_id, lesson_num, level
            ) as first_parts ON gm.id = first_parts.min_id
            WHERE gm.course_id = ? AND gm.lesson_num > 0 AND gm.level = ? 
            ORDER BY gm.lesson_num
            """, (course_id_str, current_user_level, course_id_str, current_user_level)
        )
        available_lessons = await cursor_lessons.fetchall()

    if not available_lessons:
        await query.message.edit_text(
            escape_md(
                f"На уровне {current_user_level} курса «{await get_course_title(course_id_str)}» пока нет уроков."),
            parse_mode=None
        )
        return

    for l_num, l_title_raw_db in available_lessons:
        # Убираем переносы строк из заголовка и экранируем
        lesson_title_clean = " ".join(l_title_raw_db.splitlines()) if l_title_raw_db else f"Урок {l_num}"
        lesson_title_safe = escape_md(
            lesson_title_clean[:40] + ("…" if len(lesson_title_clean) > 40 else ""))  # Ограничиваем длину для кнопки

        status_emoji = "☑️"
        if l_num < user_current_lesson_on_course:
            status_emoji = "✅"
        elif l_num == user_current_lesson_on_course:
            # Для простоты отображения в списке, не будем здесь учитывать homework_pending
            # Если пользователь зашел в "содержание", он просто видит, до какого урока дошел
            status_emoji = "▶️"  # Текущий урок, до которого дошел пользователь

        lessons_text_list_for_message.append(
            f"{status_emoji} {l_num}. {lesson_title_safe}")  # Добавляем в текстовый список для сообщения

        lessons_buttons_builder.button(
            text=f"{l_num}. {lesson_title_clean[:25]}" + ("…" if len(lesson_title_clean) > 25 else ""),
            # Текст для кнопки
            callback_data=CourseCallback(action="menu_cur", course_id=course_numeric_id, lesson_num=l_num).pack()
        )

    lessons_buttons_builder.adjust(1)  # По одной кнопке на урок

    # Кнопка для ввода номера урока вручную и кнопка "Назад"
    lessons_buttons_builder.row(
        InlineKeyboardButton(text="✍️ Ввести номер", callback_data=f"manual_lesson_repeat:{course_numeric_id}"),
        InlineKeyboardButton(text="⬅️ Назад в меню",  # Callback ведет в send_main_menu для ТЕКУЩЕГО урока пользователя
                             callback_data=ShowActiveCourseMenuCallback(course_numeric_id=course_numeric_id,
                                                                        lesson_num=user_current_lesson_on_course).pack())
    )

    final_message_text = "\n".join(lessons_text_list_for_message)
    final_message_text += escape_md("\n\nНажмите на урок, чтобы получить его повторно, или введите номер.")

    try:
        if query.message:
            await query.message.edit_text(
                text=final_message_text,
                reply_markup=lessons_buttons_builder.as_markup(),
                parse_mode=None
            )
        else:  # Маловероятно для callback, но на всякий случай
            await bot.send_message(user_id, final_message_text, reply_markup=lessons_buttons_builder.as_markup(),
                                   parse_mode=None)
    except TelegramBadRequest as e_edit_lessons:
        logger.error(f"Ошибка при редактировании сообщения со списком уроков: {e_edit_lessons}")
        # Если редактирование не удалось, пробуем отправить новое сообщение
        await bot.send_message(user_id, final_message_text, reply_markup=lessons_buttons_builder.as_markup(),
                               parse_mode=None)


# Добавьте эту функцию в main.py
async def award_karma_points(user_id: int, points: int, reason: str):
    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute("""
            INSERT INTO user_karma (user_id, balance) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET balance = balance + excluded.balance
        """, (user_id, points))
        await conn.execute("INSERT INTO user_karma_log (user_id, points_changed, reason) VALUES (?, ?, ?)",
                           (user_id, points, reason))
        await conn.commit()
        
        cursor = await conn.execute("SELECT balance FROM user_karma WHERE user_id = ?", (user_id,))
        new_balance = (await cursor.fetchone())[0]

    logger.info(f"Начислено {points} КБ пользователю {user_id}")
    try:
        await bot.send_message(user_id, f"✨ +{points} Карма-Баллов! Баланс: {new_balance} КБ", parse_mode=None)
    except:
        pass

# Вспомогательная функция для получения текущего урока пользователя
async def get_user_current_lesson(user_id: int, course_id_str: str) -> int:
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            "SELECT current_lesson FROM user_courses WHERE user_id = ? AND course_id = ? AND status = 'active'",
            (user_id, course_id_str))
        row = await cursor.fetchone()
        return row[0] if row else 0


# Обработчик для кнопки "Ввести номер урока вручную"
@dp.callback_query(lambda c: c.data.startswith("manual_lesson_repeat:"))
async def cb_manual_lesson_repeat_prompt(query: types.CallbackQuery, state: FSMContext):
    course_numeric_id = int(query.data.split(":")[1])
    course_id_str = await get_course_id_str(course_numeric_id)

    await state.set_state(RepeatLessonForm.waiting_for_lesson_number_to_repeat)
    await state.update_data(
        course_numeric_id_for_repeat=course_numeric_id,
        course_id_str_for_repeat=course_id_str  # Сохраняем также строковый ID
    )
    await query.message.edit_text(
        escape_md(f"Введите номер урока курса «{await get_course_title(course_id_str)}», который хотите повторить:"),
        parse_mode=None
        # Можно добавить кнопку "Отмена"
    )
    await query.answer()


# Обработчик ввода номера урока
@dp.message(RepeatLessonForm.waiting_for_lesson_number_to_repeat, F.text.regexp(r'^\d+$'))
async def process_lesson_number_for_repeat(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    try:
        lesson_num_to_repeat = int(message.text)
    except ValueError:
        await message.reply(escape_md("Пожалуйста, введите корректный номер урока (только цифры)."),
                            parse_mode=None)
        return

    data = await state.get_data()
    course_id_str = data.get("course_id_str_for_repeat")
    # course_numeric_id = data.get("course_numeric_id_for_repeat") # Уже есть в course_id_str

    if not course_id_str:
        logger.error(f"Не найден course_id_str в state для RepeatLessonForm, user {user_id}")
        await message.reply(escape_md("Произошла ошибка, не могу определить курс. Попробуйте снова из меню."),
                            parse_mode=None)
        await state.clear()
        return

    # Проверка, существует ли такой урок на текущем уровне и доступен ли он для повтора
    # (обычно все уроки уровня доступны для повтора, если пользователь на этом уровне)
    current_user_level = 1  # По умолчанию
    user_current_lesson_on_course = 0  # Последний ОТПРАВЛЕННЫЙ урок
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor_user_info = await conn.execute(
            "SELECT level, current_lesson FROM user_courses WHERE user_id = ? AND course_id = ?",
            (user_id, course_id_str))
        user_info_row = await cursor_user_info.fetchone()
        if user_info_row:
            current_user_level, user_current_lesson_on_course = user_info_row

        cursor_lesson_exists = await conn.execute(
            "SELECT 1 FROM group_messages WHERE course_id = ? AND lesson_num = ? AND level = ?",
            (course_id_str, lesson_num_to_repeat, current_user_level)
        )
        lesson_exists_on_level = await cursor_lesson_exists.fetchone()

    if not lesson_exists_on_level:
        await message.reply(escape_md(
            f"Урок с номером {lesson_num_to_repeat} не найден на вашем текущем уровне ({current_user_level}) для этого курса или еще не доступен. Пожалуйста, выберите другой номер."),
                            parse_mode=None)
        return  # Оставляем пользователя в состоянии, чтобы он мог ввести другой номер

    # Если урок найден, отправляем его
    await message.reply(escape_md(f"Присылаю вам урок №{lesson_num_to_repeat}..."), parse_mode=None)
    await send_lesson_to_user(user_id, course_id_str, lesson_num_to_repeat, repeat=True, level=current_user_level)

    await state.clear()
    # После отправки урока можно вернуть пользователя в главное меню активного курса
    # Нужно получить version_id
    async with aiosqlite.connect(DB_FILE) as conn:
        v_id_cursor = await conn.execute("SELECT version_id FROM user_courses WHERE user_id=? AND course_id=?",
                                         (user_id, course_id_str))
        v_id_row = await v_id_cursor.fetchone()
        if v_id_row:
            await send_main_menu(user_id, course_id_str, user_current_lesson_on_course, v_id_row[0],
                                 user_course_level_for_menu=current_user_level)


@dp.message(RepeatLessonForm.waiting_for_lesson_number_to_repeat)  # Ловим нечисловой ввод
async def process_invalid_lesson_number_for_repeat(message: types.Message, state: FSMContext):
    await message.reply(escape_md("Неверный формат. Пожалуйста, введите номер урока цифрами или нажмите 'Назад'."),
                        parse_mode=None)



@dp.callback_query(MainMenuAction.filter(F.action == "stop_course"))
async def cb_stop_current_course(query: types.CallbackQuery, callback_data: MainMenuAction, state: FSMContext):
    user_id = query.from_user.id
    course_numeric_id_to_stop = callback_data.course_id_numeric
    course_id_to_stop_str = await get_course_id_str(course_numeric_id_to_stop)

    logger.info(f"cb_stop_current_course START: user_id={user_id}, stop_course_id={course_id_to_stop_str} (numeric: {course_numeric_id_to_stop})")

    try:
        # Деактивируем курс (ставим статус 'inactive' или 'paused')
        # и останавливаем для него scheduled_task
        success, message_text = await deactivate_course(user_id, course_id_to_stop_str)

        await query.answer(escape_md(message_text), show_alert=True)

        if success:
            logger.info(f"cb_stop_current_course SUCCESS: курс {course_id_to_stop_str} деактивирован для user {user_id}")
            await query.message.edit_text(
                escape_md(f"Курс «{await get_course_title(course_id_to_stop_str)}» был остановлен.\n"
                           "Вы можете выбрать другой курс или активировать новый."),
                parse_mode=None
            )
            # Перенаправляем на выбор другого курса
            await cb_select_other_course(query, state)  # Переиспользуем существующий обработчик
        else:
            logger.warning(f"cb_stop_current_course FAILED: не удалось деактивировать курс {course_id_to_stop_str} для user {user_id}")
            # Если деактивация не удалась, можно просто обновить меню или ничего не делать
            pass

    except Exception as e:
        logger.error(f"Ошибка при остановке курса {course_id_to_stop_str} для {user_id}: {e}")
        await query.answer("Не удалось остановить курс", show_alert=True)


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
            parse_mode=None,
            reply_markup=None  # Убираем клавиатуру
        )
        await callback.answer("Часовой пояс сохранен!")
    except Exception as e2888:
        logger.error(f"Ошибка сохранения часового пояса {timezone_name} для {user_id}: {e2888}")
        await callback.answer("Не удалось сохранить часовой пояс.", show_alert=True)

@dp.message(F.location)
async def handle_location(message: types.Message):
    """Обработка полученной геолокации"""
    user_id = message.from_user.id
    lat = message.location.latitude
    lng = message.location.longitude
    logger.info(f"Пользователь {user_id} отправил геолокацию: {lat}, {lng}")
    try:
        # Определяем часовой пояс по координатам todo: непонятно, как это делается


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

    except Exception as e2917:
        logger.error(f"Ошибка определения часового пояса: {e2917}")
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
    except Exception as e2943:
        logger.error(f"Ошибка при получении часового пояса: {e2943}")
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
    except Exception as e2963:
        logger.error(f"Error while checking homework status: {e2963}")
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
    except Exception as e2995:
        logger.error(f"Ошибка при обработке оценки поддержки: {e2995}")
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
            parse_mode=None
        )

        # Уведомляем админа об успешной отправке
        await message.answer("Сообщение пользователю отправлено.")
    except Exception as e3032:
        logger.error(f"Ошибка при отправке ответа пользователю: {e3032}", exc_info=True)
        await message.answer("Произошла ошибка при отправке сообщения пользователю.")

    await state.clear()



@dp.message(CommandStart())
@db_exception_handler
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    """Обработчик команды /start."""
    logger.info(f"!!!!!!!!!! CMD_START ВЫЗВАН для пользователя {message.from_user.id} !!!!!!!!!!")
    user = message.from_user
    user_id = user.id
    first_name = user.first_name or user.username or "Пользователь"
    logger.info(f"cmd_start {user_id=}, ADMIN_IDS_CONF={ADMIN_IDS_CONF}, is_admin={user_id in ADMIN_IDS_CONF}")

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

            # Проверяем, является ли пользователь админом (сначала!)
            if user_id in ADMIN_IDS_CONF:
                logger.info(f"cmd_start: User {user_id} is admin, checking for active course...")
                # Получаем данные активного курса админа (если есть)
                try:
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
                    logger.info(f"cmd_start: admin current_course query result = {current_course}")
                except Exception as e_admin_query:
                    logger.error(f"cmd_start: Error querying admin course: {e_admin_query}")
                    current_course = None
                
                if current_course:
                    # Админ с активным курсом (режим тестирования)
                    course_id, lesson_num, version_id, course_name, version_name, status, hw_status = current_course
                    course_numeric_id = await get_course_id_int(course_id) if course_id else None
                    
                    logger.info(f"cmd_start: Admin {user_id} has active course {course_id}, showing admin test mode")
                    
                    # Проверяем тест-режим
                    test_mode_active = user_id in ADMIN_TEST_MODE
                    test_mode_status = "⚡ ВКЛЮЧЕН" if test_mode_active else "🕐 ВЫКЛЮЧЕН"
                    test_mode_interval = f"{TEST_MODE_INTERVAL_MINUTES} мин" if test_mode_active else "12 ч"

                    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⏹ Остановить тестирование курса", callback_data=MainMenuAction(action="stop_course", course_id_numeric=course_numeric_id).pack())],
                        [InlineKeyboardButton(text="📚 Список уроков", callback_data=ViewLessonCallback(course_id=course_id, lesson_num=lesson_num).pack())],
                        [InlineKeyboardButton(text="🔙 К админскому меню", callback_data="admin_menu")]
                    ])

                    await bot.send_message(
                        user_id,
                        f"👑 РЕЖИМ ТЕСТИРОВАНИЯ\n\n"
                        f"Вы администратор с активным курсом:\n"
                        f"🎓 Курс: {course_name}\n"
                        f"🔑 Тариф: {version_name}\n"
                        f"📚 Текущий урок: {lesson_num}\n\n"
                        f"🕐 Тест-режим: {test_mode_status} ({test_mode_interval})\n"
                        f"{'⚡ Уроки каждые ' + str(TEST_MODE_INTERVAL_MINUTES) + ' мин' if test_mode_active else '💡 Для быстрого тестирования: /test_mode'}\n\n"
                        f"💡 Команды:\n"
                        f"• /show_codes — курсы и коды\n"
                        f"• /add_course — создать курс\n"
                        f"• /upload_lesson — загрузить уроки\n"
                        f"• /list_lessons — список уроков\n"
                        f"• /list_admins — список админов\n"
                        f"• /add_admin <ID> — добавить админа\n"
                        f"• /remove_admin <ID> — удалить админа\n"
                        f"• /set_hw_timeout <сек> — таймаут AI-проверки\n"
                        f"• /export_db — экспорт базы\n"
                        f"• /import_db — импорт базы\n"
                        f"• /remind <id> <msg> — напоминание\n"
                        f"• /test_mode — переключить тест-режим\n\n"
                        f"Для теста активируйте курс кодом.",
                        reply_markup=admin_keyboard,
                        parse_mode=None
                    )
                else:
                    logger.info(f"cmd_start: Admin {user_id} has NO active course, showing admin menu")
                    admin_keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="📚 Список курсов", callback_data="admin_list_courses")],
                        [InlineKeyboardButton(text="➕ Создать курс", callback_data="admin_add_course")]
                    ])
                    await bot.send_message(
                        user_id,
                        f"👑 АДМИН-МЕНЮ\n\n"
                        f"У вас нет активного курса для тестирования.\n\n"
                        f"💡 Команды:\n"
                        f"• /show_codes — курсы и коды\n"
                        f"• /add_course — создать курс\n"
                        f"• /upload_lesson — загрузить уроки\n"
                        f"• /list_lessons — список уроков\n"
                        f"• /list_admins — список админов\n"
                        f"• /add_admin <ID> — добавить админа\n"
                        f"• /remove_admin <ID> — удалить админа\n"
                        f"• /set_hw_timeout <сек> — таймаут AI-проверки\n\n"
                        f"Или активируйте курс кодом для тестирования.",
                        reply_markup=admin_keyboard,
                        parse_mode=None
                    )
                return

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

            # Если у пользователя нет активного курса
            if not current_course:
                logger.info(f"cmd_start: No active course found for {user_id}, asking for activation code")
                await message.answer(escape_md("❌ Нет активных курсов. Активируйте курс через код"), parse_mode=None)

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
                except Exception as e2875:
                    logger.error(f"Ошибка при отправке фото: {e2875}", exc_info=True)
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
            logger.info(f"{total_lessons=} tariff_name = {tariff_name}")
            # Общее количество курсов для кнопки "Мои курсы"
            total_courses = len(completed_courses) + len(active_courses)
            courses_button_text = f"📚 Мои курсы ({total_courses})"

            logger.info(f"Старт задачи для шедулера для {user_id=}")
            await start_lesson_schedule_task(user_id)
            # Генерация клавиатуры
            #homework_pending = await check_homework_pending(user_id, course_id, lesson_num)
            logger.info(f"перед созданием клавиатуры {course_numeric_id=}")
            keyboard = get_main_menu_inline_keyboard(  # await убрали
                course_numeric_id = course_numeric_id,
                lesson_num=lesson_num,
                user_tariff=version_id,
                homework_pending=True if hw_status != 'approved' and hw_status != 'not_required' else False
            )

            welcome_message = (
                f"*С возвращением*, {escape_md(first_name)}!\n\n"
                f"🎓 Курс: {escape_md(course_name)}\n"
                f"🔑 Тариф: {escape_md(tariff_name)}\n"
                f"📚 Текущий урок: {lesson_num}"
            )
            logger.info(f"{welcome_message=}")
            await message.answer(welcome_message, reply_markup=keyboard, parse_mode=None)


    except Exception as e2945:
        logger.error(f"Error in cmd_start: {e2945}", exc_info=True)
        await message.answer("Произошла ошибка при обработке команды. Пожалуйста, попробуйте позже.", parse_mode=None)


@dp.callback_query(lambda c: c.data == "admin_menu")
async def callback_admin_menu(callback: CallbackQuery):
    """Возврат к админскому меню"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    
    admin_menu_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📚 Курсы и коды", callback_data="show_codes_menu")],
        [InlineKeyboardButton(text="➕ Добавить курс", callback_data="add_course_menu")],
        [InlineKeyboardButton(text="📚 Список уроков", callback_data="list_lessons_menu")]
    ])

    # Проверяем включен ли тест-режим
    test_mode_status = "⚡ 5 мин" if callback.from_user.id in ADMIN_TEST_MODE else "🕐 12 ч"

    await callback.message.edit_text(
        f"👑 Админское меню\n\n"
        f"💡 Команды:\n"
        f"• /show_codes — курсы и коды\n"
        f"• /add_course — создать курс\n"
        f"• /upload_lesson — загрузить уроки\n"
        f"• /list_lessons — список уроков\n"
        f"• /list_admins — список админов\n"
        f"• /set_hw_timeout <мин> — таймаут AI-проверки\n"
        f"• /export_db — экспорт базы\n"
        f"• /import_db — импорт базы\n"
        f"• /remind <id> <msg> — напоминание\n"
        f"• /test_mode — тест-режим ({test_mode_status})\n",
        reply_markup=admin_menu_keyboard,
        parse_mode=None
    )


@dp.callback_query(lambda c: c.data == "add_course_menu")
async def callback_add_course_menu(callback: CallbackQuery):
    """Переход к добавлению курса"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    await callback.message.edit_text(
        "➕ *Добавление курса*\n\n"
        "Используйте команду:\n"
        "/add_course — для пошагового создания\n\n"
        "Или быстрое создание:\n"
        "/add_course <group_id> <course_id> <code1> <code2> <code3>",
        parse_mode=None
    )


@dp.callback_query(lambda c: c.data == "list_lessons_menu")
async def callback_list_lessons_menu(callback: CallbackQuery):
    """Переход к списку уроков"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    await show_lessons_list(callback.from_user.id, callback.message.chat.id, callback.message.message_id)


@dp.callback_query(lambda c: c.data == "show_codes_menu")
async def callback_show_codes_menu(callback: CallbackQuery):
    """Показать коды активации"""
    if callback.from_user.id not in ADMIN_IDS_CONF:
        await callback.answer("❌ Только для администраторов.", show_alert=True)
        return
    
    await callback.answer()
    
    try:
        settings = await load_settings()
        if not settings or "activation_codes" not in settings:
            await callback.message.edit_text("📭 Коды активации не найдены.")
            return
        
        codes = settings["activation_codes"]
        if not codes:
            await callback.message.edit_text("📭 Нет зарегистрированных кодов.")
            return
        
        # Группируем коды по курсам
        courses = {}
        for code, data in codes.items():
            course = data.get("course", "unknown")
            version = data.get("version", "v1")
            price = data.get("price", 0)
            
            if course not in courses:
                courses[course] = []
            courses[course].append({
                "code": code,
                "version": version,
                "price": price
            })
        
        # Формируем ответ
        result = "🔐 *Коды активации:*\n\n"
        
        for course_name, course_codes in sorted(courses.items()):
            result += f"📚 *{escape_md(course_name)}*\n"
            for item in course_codes:
                code = item["code"]
                version = item["version"]
                price = item["price"]
                result += f"   • `{escape_md(code)}` — {escape_md(version)}"
                if price:
                    result += f" ({escape_md(str(price))}₽)"
                result += "\n"
            result += "\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_menu")]
        ])
        
        await callback.message.edit_text(result, reply_markup=keyboard, parse_mode=None)
        
    except Exception as e:
        logger.error(f"Ошибка при показе кодов: {e}")
        await callback.message.edit_text(f"❌ Ошибка: {e}")


async def send_course_description(user_id: int, course_id_str: str):  # Принимаем строковый ID
    """Отправляет описание курса пользователю, пробуя разные источники.
        courses.description.
        group_messages с lesson_num = 0.
        group_messages с lesson_num IS NULL.
        Если ничего из вышеперечисленного, берется текст первой текстовой части урока №1 (если есть)."""
    import traceback
    logger.info("=" * 80)
    logger.info(f"send_course_description START")
    logger.info(f"ЛОГ #0: CALL STACK:")
    for line in traceback.format_stack()[:-1]:
        logger.info(f"  {line.strip()}")
    logger.info(f"ЛОГ #1: user_id={user_id}")
    logger.info(f"ЛОГ #2: course_id_str='{course_id_str}'")
    logger.info(f"ЛОГ #3: Вызов get_course_id_int для '{course_id_str}'")
    course_numeric_id_for_log = await get_course_id_int(course_id_str)
    logger.info(f"ЛОГ #4: course_numeric_id={course_numeric_id_for_log}")
    logger.info(f"ЛОГ #5: Вызов get_course_title для '{course_id_str}'")
    course_title_for_log = await get_course_title(course_id_str)
    logger.info(f"ЛОГ #6: course_title='{course_title_for_log}'")
    logger.info(f"ЛОГ #7: Начинаем поиск описания в БД")
    description_to_send = None

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # 1. Попытка получить описание из courses.description
            logger.info(f"ЛОГ #8: Попытка #1 - courses.description")
            cursor_courses_desc = await conn.execute(
                "SELECT description FROM courses WHERE course_id = ?",
                (course_id_str,)
            )
            row_courses_desc = await cursor_courses_desc.fetchone()
            logger.info(f"ЛОГ #9: row_courses_desc={row_courses_desc}")
            if row_courses_desc and row_courses_desc[0] and row_courses_desc[0].strip():
                description_to_send = row_courses_desc[0].strip()
                logger.info(f"ЛОГ #10: Найдено описание для курса '{course_id_str}' в таблице 'courses'.")
                logger.info(f"ЛОГ #11: description_to_send (первые 100 симв)='{description_to_send[:100]}...'")
            else:
                logger.info(f"ЛОГ #10: В courses.description пусто, пробуем урок 0")
                # 2. Если в courses.description пусто, ищем урок 0 в group_messages
                logger.info(f"ЛОГ #11: Попытка #2 - lesson_num = 0 в group_messages")
                cursor_gm_lesson0 = await conn.execute(
                    "SELECT text FROM group_messages WHERE course_id = ? AND lesson_num = 0 ORDER BY id ASC LIMIT 1",
                    (course_id_str,)
                )
                row_gm_lesson0 = await cursor_gm_lesson0.fetchone()
                logger.info(f"ЛОГ #12: row_gm_lesson0={row_gm_lesson0}")
                if row_gm_lesson0 and row_gm_lesson0[0] and row_gm_lesson0[0].strip():
                    description_to_send = row_gm_lesson0[0].strip()
                    logger.info(f"ЛОГ #13: Найдено описание для курса '{course_id_str}' как урок 0 в 'group_messages'.")
                    logger.info(f"ЛОГ #14: description_to_send (первые 100 симв)='{description_to_send[:100]}...'")
                else:
                    logger.info(f"ЛОГ #13: Урок 0 не найден, пробуем lesson_num IS NULL")
                    # 3. Если и урока 0 нет, ищем урок с lesson_num IS NULL (если такая логика предполагалась)
                    logger.info(f"ЛОГ #14: Попытка #3 - lesson_num IS NULL")
                    cursor_gm_lesson_null = await conn.execute(
                        "SELECT text FROM group_messages WHERE course_id = ? AND lesson_num IS NULL ORDER BY id ASC LIMIT 1",
                        (course_id_str,)
                    )
                    row_gm_lesson_null = await cursor_gm_lesson_null.fetchone()
                    logger.info(f"ЛОГ #15: row_gm_lesson_null={row_gm_lesson_null}")
                    if row_gm_lesson_null and row_gm_lesson_null[0] and row_gm_lesson_null[0].strip():
                        description_to_send = row_gm_lesson_null[0].strip()
                        logger.info(f"ЛОГ #16: Найдено описание для курса '{course_id_str}' как урок NULL в 'group_messages'.")

            # 4. Если ничего не найдено, ищем первую текстовую часть первого реального урока (lesson_num=1)
            if not description_to_send:
                logger.info(
                    f"Описание не найдено в courses.description, lesson_num=0 или lesson_num IS NULL для '{course_id_str}'. Ищем текст урока 1.")
                cursor_gm_lesson1_text = await conn.execute(
                    """SELECT text FROM group_messages 
                       WHERE course_id = ? AND lesson_num = 1 AND content_type = 'text' AND text IS NOT NULL AND TRIM(text) != ''
                       ORDER BY id ASC LIMIT 1""",
                    (course_id_str,)
                )
                row_gm_lesson1_text = await cursor_gm_lesson1_text.fetchone()
                if row_gm_lesson1_text and row_gm_lesson1_text[0]:
                    description_to_send = row_gm_lesson1_text[0].strip()
                    # Возможно, добавить префикс, что это начало первого урока
                    description_to_send = "Из первого урока:\n" + description_to_send
                    logger.info(f"В качестве описания для '{course_id_str}' взят текст урока 1.")

            if description_to_send:
                logger.info(f"ЛОГ #17: description_to_send найден, длина={len(description_to_send)}")
                # Удаляем HTML-теги, если они есть (простая очистка)
                # cleaned_description = re.sub(r'<[^>]+>', '', description_to_send)
                # Для MarkdownV2 специфичные HTML теги не работают, так что re.sub может быть не нужен,
                # если только в тексте нет непреднамеренных < >.
                # Главное - правильно экранировать для MarkdownV2.

                # Разбиваем на части, если описание слишком длинное
                max_len = 4000  # Максимальная длина сообщения Telegram (с запасом)
                escaped_desc = escape_md(description_to_send)  # Экранируем один раз весь текст
                logger.info(f"ЛОГ #18: escaped_desc длина={len(escaped_desc)}")

                for i in range(0, len(escaped_desc), max_len):
                    part = escaped_desc[i:i + max_len]
                    await bot.send_message(
                        user_id,
                        part,
                        parse_mode=None,
                        disable_web_page_preview=True
                    )
                logger.info(f"ЛОГ #19: Описание отправлено пользователю {user_id}")
                logger.info(f"ЛОГ #20: send_course_description END")
                logger.info("=" * 80)
                logger.info(
                    f"Описание для '{course_id_str}' (длина {len(escaped_desc)}) успешно отправлено пользователю {user_id}.")
            else:
                logger.warning(
                    f"Полное описание курса (courses.description, урок 0, урок NULL, урок 1) не найдено для course_id='{course_id_str}'.")
                await bot.send_message(user_id, escape_md("Подробное описание для этого курса сейчас недоступно."),
                                       parse_mode=None)

    except Exception as e_scd_v2:
        logger.error(f"Ошибка в send_course_description v2 для course_id='{course_id_str}': {e_scd_v2}", exc_info=True)
        await bot.send_message(user_id, escape_md("Не удалось загрузить описание курса. Попробуйте позже."),
                               parse_mode=None)


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

    await message.answer(escape_md(help_text), parse_mode=None)


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
                line = f"- _{escaped_title}_ - *{lesson_count}* {escaped_lesson_word}"
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
            parse_mode=None  # Указываем режим парсинга
        )

        # Формируем текст ответа с кнопками
        response_text = ""
        if active_courses:
            response_text += "Активные курсы:\n"
            response_text += "\n".join([f"- {title}" for title, course_id, version_id, current_lesson, idd in active_courses]) + "\n\n"
        if completed_courses:
            response_text += "Завершенные курсы:\n"
            response_text += "\n".join([f"- {title}" for title, course_id, version_id, idd in completed_courses])

        if not active_courses and not completed_courses:
            response_text = "У вас нет активных или завершенных курсов."

        # Проверяем, есть ли активные курсы, чтобы взять данные для меню
        if active_courses:
            # Берем данные из первого активного курса для примера
            title, course_id, version_id, lesson_num, idd = active_courses[0]
        else:
            # Если нет активных курсов, задаем значения по умолчанию или None
            idd = None
            lesson_num = 0
            version_id = None

        # Создаем кнопки меню
        keyboard = get_main_menu_inline_keyboard(
            course_numeric_id=idd,  # Определите course_id
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
    except Exception as e3375:
        logger.error(f"Error in cmd_mycourses: {e3375}")
        await query.answer("Произошла ошибка при обработке запроса", show_alert=True)


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

    except Exception as e3398:
        logger.error(f"Error in show_lesson_content: {e3398}")
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
    except Exception as e3418:
        logger.error(f"Ошибка при получении последнего завершенного курса для отзыва: {e3418}")

    if last_completed_course_id:
        course_title = await get_course_title(last_completed_course_id)
        prompt_text = f"Пожалуйста, напишите ваш отзыв о пройденном курсе «{escape_md(course_title)}»:"
        await state.update_data(course_id_for_review=last_completed_course_id) # Сохраняем ID курса
    else:
        prompt_text = "Пожалуйста, напишите ваш отзыв о пройденном курсе:"
        await state.update_data(course_id_for_review="неизвестный (не найден)")

    await query.message.edit_text(escape_md(prompt_text), parse_mode=None)
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
            # CREATE TABLE IF NOT EXISTS course_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, course_id TEXT, review_text TEXT, timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            await conn.execute(
                "INSERT INTO course_reviews (user_id, course_id, review_text) VALUES (?, ?, ?)",
                (user_id, course_id_for_review, review_text_raw)
            )
            await conn.commit()
        await message.reply(escape_md("Спасибо за ваш отзыв! Мы ценим ваше мнение. 🎉  Введите код следующего курса который хотите пройти!"),
                            parse_mode=None)

        if ADMIN_GROUP_ID:
            user_info = await bot.get_chat(user_id)
            user_details = user_info.full_name
            if user_info.username:
                user_details += f" (@{user_info.username})"

            admin_message = (
                f"📝 Новый отзыв о курсе!\n"
                f"👤 Пользователь: {escape_md(user_details)} ID: {user_id}\n"
                f"📚 Курс: {escape_md(str(course_id_for_review))}\n"
                f"💬 Отзыв:\n{escape_md(review_text_raw)}"
            )
            await bot.send_message(ADMIN_GROUP_ID, admin_message, parse_mode=None)

    except Exception as e3469:
        logger.error(f"Ошибка при сохранении/отправке отзыва о курсе: {e3469}")
        await message.reply(escape_md("Произошла ошибка при обработке вашего отзыва. Пожалуйста, попробуйте позже."),
                            parse_mode=None)
    finally:
        await state.clear()



@dp.callback_query(F.data == "select_other_course")
async def cb_select_other_course(query: types.CallbackQuery, state: FSMContext):
    user_id = query.from_user.id
    logger.info(f"Пользователь {user_id} нажал 'Выбрать другой курс' / 'Все курсы'")
    await query.answer()

    async with aiosqlite.connect(DB_FILE) as conn:
        # 1. Получаем все системные курсы с их базовым тарифом/ценой для отображения
        #    и их числовым ID для некоторых колбэков.
        #    Берем минимальную цену, если тарифов несколько.
        cursor_all_courses = await conn.execute(
            """
            SELECT 
                c.course_id, 
                c.title, 
                c.id as course_numeric_id,
                MIN(cv.price) as min_price, 
                (SELECT cv_inner.version_id FROM course_versions cv_inner 
                 WHERE cv_inner.course_id = c.course_id ORDER BY cv_inner.price ASC LIMIT 1) as base_version_id
            FROM courses c 
            LEFT JOIN course_versions cv ON c.course_id = cv.course_id 
            GROUP BY c.course_id, c.title, c.id
            ORDER BY c.title
            """
        )
        all_system_courses = await cursor_all_courses.fetchall()
        # all_system_courses будет: [(course_id_str, title, course_numeric_id, min_price, base_version_id), ...]

        # 2. Получаем все курсы пользователя со статусом и текущим уроком
        cursor_user_courses = await conn.execute(
            """
            SELECT uc.course_id, uc.status, uc.current_lesson, uc.version_id, c.id as course_numeric_id
            FROM user_courses uc
            JOIN courses c ON uc.course_id = c.course_id
            WHERE uc.user_id = ?
            """, (user_id,)
        )
        user_courses_raw = await cursor_user_courses.fetchall()
        user_courses_data = {} # course_id_str -> (status, current_lesson, version_id, course_numeric_id)
        active_course_for_back_button = None

        for uc_course_id_str, uc_status, uc_lesson, uc_version, uc_numeric_id in user_courses_raw:
            user_courses_data[uc_course_id_str] = {
                "status": uc_status, "current_lesson": uc_lesson,
                "version_id": uc_version, "numeric_id": uc_numeric_id
            }
            if uc_status == 'active' and not active_course_for_back_button: # Запоминаем первый активный для кнопки "Назад"
                active_course_for_back_button = {
                    "numeric_id": uc_numeric_id, "current_lesson": uc_lesson,
                }


    if not all_system_courses:
        await query.message.edit_text(escape_md("К сожалению, сейчас нет доступных курсов для выбора."),
                                      parse_mode=None, reply_markup=None)
        return

    builder = InlineKeyboardBuilder()
    message_text_parts = [escape_md("Переключайте курсы или читайте их описания:")]

    for i, (course_id_str, title, course_num_id_sys, min_price, base_version_id_sys) in enumerate(all_system_courses,
                                                                                                  1):
        course_title_safe = escape_md(title)
        user_course_info = user_courses_data.get(course_id_str)

        course_block_header = ""
        action_button_text = ""
        action_button_callback_data = None

        # Определяем текст и callback для основной кнопки действия
        if user_course_info:
            status = user_course_info["status"]
            current_lesson_user = user_course_info["current_lesson"]

            if status == 'active':
                course_block_header = f"\n{i}. ▶️ *{course_title_safe}* (активен)"
                action_button_text = f"{i}. 🚀 Перейти"
                action_button_callback_data = ShowActiveCourseMenuCallback(course_numeric_id=course_num_id_sys,
                                                                           lesson_num=current_lesson_user).pack()
            elif status == 'completed':
                course_block_header = f"\n{i}. ✅ *{course_title_safe}* (пройден)"
                action_button_text = f"{i}. 🔁 Повтор/Уровни"
                action_button_callback_data = RestartCourseCallback(course_numeric_id=course_num_id_sys,
                                                                    action="restart_current_level").pack()
            elif status == 'inactive':
                course_block_header = f"\n{i}. ⏸️ *{course_title_safe}* (остановлен)"
                action_button_text = f"{i}. 🔄 Возобновить"
                action_button_callback_data = ShowActiveCourseMenuCallback(course_numeric_id=course_num_id_sys,
                                                                           lesson_num=current_lesson_user).pack()
            else:
                price_str = f"{min_price} руб." if min_price is not None and min_price > 0 else "По коду"
                course_block_header = f"\n{i}. ✨ *{course_title_safe}* ({escape_md(price_str)})"
                action_button_text = f"{i}. 💰 Купить/Инфо"
                action_button_callback_data = BuyCourseCallback(course_numeric_id=course_num_id_sys).pack()
        else:
            price_str = f"{min_price} руб." if min_price is not None and min_price > 0 else "Инфо по активации"
            course_block_header = f"\n{i}. 🆕 *{course_title_safe}* ({escape_md(price_str)})"
            action_button_text = f"{i}. 💰 Купить/Инфо"
            action_button_callback_data = BuyCourseCallback(course_numeric_id=course_num_id_sys).pack()

        message_text_parts.append(course_block_header)

        # Формируем ряд кнопок
        buttons_for_this_course_row = []
        if action_button_text and action_button_callback_data:
            buttons_for_this_course_row.append(
                InlineKeyboardButton(text=action_button_text, callback_data=action_button_callback_data)
            )

        # Кнопка "Описание" с номером и названием
        short_title_for_desc_button = title[:18] + '…' if len(title) > 18 else title
        buttons_for_this_course_row.append(
            InlineKeyboardButton(
                text=f"{i}. ℹ️ {escape_md(short_title_for_desc_button)}",
                callback_data=CourseDetailsCallback(action="show_description",
                                                    course_numeric_id=course_num_id_sys).pack()
            )
        )
        builder.row(*buttons_for_this_course_row)

    if active_course_for_back_button:
        message_text_parts.append("")
        builder.row(InlineKeyboardButton(
            text="⬅️ В меню активного курса",
            callback_data=ShowActiveCourseMenuCallback(
                course_numeric_id=active_course_for_back_button["numeric_id"],
                lesson_num=active_course_for_back_button["current_lesson"]
            ).pack()
        ))

    # Кнопка возврата в главное меню (для всех)
    builder.row(InlineKeyboardButton(
        text="🔙 /start - В главное меню",
        callback_data="admin_menu" if user_id in ADMIN_IDS_CONF else "menu_cur"
    ))

    final_message_text = "\n".join(message_text_parts)

    # Пагинация (пока не реализована, но можно добавить логику здесь, если all_system_courses большой)
    # if len(all_system_courses) > COURSES_PER_PAGE:
    #     # Добавить кнопки пагинации
    #     pass

    try:
        # ... (логика edit_text / send_message как была) ...
        if query.message:
            await query.message.edit_text(
                text=final_message_text,
                reply_markup=builder.as_markup(),
                parse_mode=None
            )
        # ... (обработка ошибок)
    except TelegramBadRequest as e_edit_courses_v2:
        logger.warning(
            f"Не удалось отредактировать сообщение v2 для списка курсов: {e_edit_courses_v2}. Отправляю новое.")
        await bot.send_message(
            chat_id=user_id, text=final_message_text, reply_markup=builder.as_markup(), parse_mode=None
        )
    except Exception as e_cb_select_other_v2:
        logger.error(f"Общая ошибка в cb_select_other_course_v2: {e_cb_select_other_v2}", exc_info=True)
        await query.answer("Произошла ошибка при отображении списка курсов", show_alert=True)


# Обработчики для новых CallbackData
@dp.callback_query(CourseDetailsCallback.filter(F.action == "show_description"))
async def cb_show_course_description(query: types.CallbackQuery, callback_data: CourseDetailsCallback):
    course_numeric_id = callback_data.course_numeric_id  # <--- ИЗМЕНЕНИЕ
    course_id_str = await get_course_id_str(course_numeric_id)

    if not course_id_str or course_id_str == "Неизвестный курс":
        await query.answer("Ошибка: курс не найден", show_alert=True)
        return

    await query.answer("Загружаю описание")
    await send_course_description(query.from_user.id, course_id_str)
    # После описания можно вернуть пользователя к списку курсов или в главное меню
    # await cb_select_other_course(query, state) # Вернуть к списку курсов (нужен state)


@dp.callback_query(ShowActiveCourseMenuCallback.filter())
async def cb_show_active_course_main_menu(query: types.CallbackQuery, callback_data: ShowActiveCourseMenuCallback,
                                          state: FSMContext):
    user_id = query.from_user.id
    course_numeric_id = callback_data.course_numeric_id
    requested_lesson_num = callback_data.lesson_num  # Урок, с которого хотим начать (или текущий)

    course_id_str = await get_course_id_str(course_numeric_id)
    if not course_id_str or course_id_str == "Неизвестный курс":
        await query.answer("Ошибка: не удалось найти курс", show_alert=True)
        return

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            "SELECT version_id, hw_status, hw_type, level, status, current_lesson FROM user_courses WHERE user_id = ? AND course_id = ?",
            # Добавили status и current_lesson
            (user_id, course_id_str)
        )
        course_user_details = await cursor.fetchone()

    if not course_user_details:
        await query.answer("Информация о вашем прогрессе по этому курсу не найдена", show_alert=True)
        await cb_select_other_course(query, state)
        return

    version_id, hw_status, hw_type, user_level, current_status_db, current_lesson_db = course_user_details

    # Если курс был inactive, "активируем" его для продолжения
    if current_status_db == 'inactive':
        logger.info(f"Пользователь {user_id} возобновляет неактивный курс {course_id_str}.")
        async with aiosqlite.connect(DB_FILE) as conn_reactivate:
            # Обновляем статус на active и время последнего урока (чтобы шедулер не сработал сразу)
            # Можно также обновить first_lesson_sent_time, если логика его использует для отсчета интервалов.
            # Здесь мы просто меняем статус. Прогресс (current_lesson) сохраняется.
            now_utc_str = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
            await conn_reactivate.execute(
                "UPDATE user_courses SET status = 'active', last_lesson_sent_time = ? WHERE user_id = ? AND course_id = ?",
                (now_utc_str, user_id, course_id_str)
            )
            await conn_reactivate.commit()
        await start_lesson_schedule_task(user_id)  # Запускаем шедулер для пользователя
        await query.answer(f"Курс «{escape_md(await get_course_title(course_id_str))}» возобновлен", show_alert=True)
    else:
        await query.answer(f"Перехожу в меню курса «{escape_md(await get_course_title(course_id_str))}»")

    if query.message:
        try:
            await query.message.delete()
        except TelegramBadRequest:
            pass

    # Используем current_lesson_db, так как requested_lesson_num из callback_data
    # может быть просто "маркером" для входа в меню.
    # Или, если мы хотим перейти именно к lesson_num из callback_data (например, после "Содержание курса"):
    lesson_to_show_in_menu = requested_lesson_num  # или current_lesson_db, если это более актуально

    await send_main_menu(
        user_id=user_id,
        course_id=course_id_str,
        lesson_num=lesson_to_show_in_menu,
        version_id=version_id,
        homework_pending=(hw_status == 'pending' or hw_status == 'rejected'),  # Обновленный hw_status
        hw_type=hw_type,
        user_course_level_for_menu=user_level
    )


#  Обработчик для RestartCourseCallback:
@dp.callback_query(RestartCourseCallback.filter())
async def cb_restart_or_next_level_course(query: types.CallbackQuery, callback_data: RestartCourseCallback,
                                          state: FSMContext):
    user_id = query.from_user.id
    course_numeric_id_to_process = callback_data.course_numeric_id # <--- ИЗМЕНЕНИЕ
    action = callback_data.action
    course_id_to_process = await get_course_id_str(course_numeric_id_to_process)  # Получаем строковый ID для работы с БД
    logger.info(f"Пользователь {user_id} выбрал действие '{action}' для курса {course_id_to_process}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor_current_info = await conn.execute(
                "SELECT version_id, level FROM user_courses WHERE user_id = ? AND course_id = ?",
                (user_id, course_id_to_process)
            )
            current_info = await cursor_current_info.fetchone()
            if not current_info:
                await query.answer("Не удалось найти информацию о вашем курсе", show_alert=True)
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
                    await query.answer(f"Контент для {new_level_for_user}-го уровня пока не готов", show_alert=True)
                    return
                log_details = f"Переход на уровень {new_level_for_user}"
                user_message_feedback = f"Вы перешли на {new_level_for_user}-й уровень курса '{escape_md(await get_course_title(course_id_to_process))}' Уроки начнутся заново"
            elif action == "restart_current_level":
                # new_level_for_user остается current_user_level_db
                log_details = f"Повторное прохождение уровня {current_user_level_db}"
                user_message_feedback = f"Прогресс по текущему уровню ({current_user_level_db}) курса '{escape_md(await get_course_title(course_id_to_process))}' сброшен Уроки начнутся заново"
            else:
                await query.answer("Неизвестное действие", show_alert=True)
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


    except Exception as e3645:
        logger.error(f"Ошибка при '{action}' для курса {course_id_to_process}, user {user_id}: {e3645}", exc_info=True)
        await query.answer("Произошла ошибка при обработке вашего запроса", show_alert=True)

# Заглушка для ROBOKASSA_MERCHANT_LOGIN и ROBOKASSA_PASSWORD1
ROBOKASSA_MERCHANT_LOGIN = os.getenv("ROBOKASSA_MERCHANT_LOGIN", "your_robokassa_login")
ROBOKASSA_PASSWORD1 = os.getenv("ROBOKASSA_PASSWORD1", "your_robokassa_password1")


# Понадобится библиотека для запросов к вашему ИИ, например, 'google-generativeai' или 'openai'
# import google.generativeai as genai
# genai.configure(api_key="YOUR_AI_API_KEY")

async def get_ai_selected_task_id(user_id: int) -> int | None:
    # 1. Собрать контекст
    async with aiosqlite.connect(DB_FILE) as conn:
        # Получаем завершенные задачи
        cursor_completed = await conn.execute(
            "SELECT task_id FROM user_completed_tasks WHERE user_id = ?", (user_id,)
        )
        completed_ids = [row[0] for row in await cursor_completed.fetchall()]

        # Получаем все доступные задачи (ID и категорию)
        cursor_all = await conn.execute(
            "SELECT id, task_category FROM task_pool WHERE is_active = TRUE"
        )
        all_tasks = await cursor_all.fetchall()

    # Фильтруем те, что еще не выполнены
    available_tasks_for_prompt = [task for task in all_tasks if task[0] not in completed_ids]
    if not available_tasks_for_prompt:
        return None  # Задания кончились

    # 2. Формируем промпт (текст промпта как в примере выше)
    prompt = f"""
    Ты — ассистент-психолог... (и так далее, как в примере выше)
    Контекст пользователя:
    - User ID: {user_id}
    - Завершенные задания (ID): {completed_ids}

    Вот список всех доступных для выбора заданий в формате [ID, Категория]:
    {available_tasks_for_prompt}

    ВЕРНИ ТОЛЬКО JSON ОБЪЕКТ С ОДНИМ КЛЮЧОМ "task_id".
    """

    try:
        # 3. Отправляем запрос к ИИ (это пример для Gemini, адаптируйте под свою модель)
        # model = genai.GenerativeModel('gemini-pro')
        # response = await model.generate_content_async(prompt)
        # ai_response_text = response.text

        # ЗАГЛУШКА: пока нет ИИ, выбираем случайное
        import random
        selected_task = random.choice(available_tasks_for_prompt)
        ai_response_text = json.dumps({"task_id": selected_task[0]})
        # --- конец заглушки ---

        logger.info(f"Ответ от ИИ: {ai_response_text}")

        # 4. Парсим ответ
        data = json.loads(ai_response_text)
        return int(data["task_id"])

    except Exception as e:
        logger.error(f"Ошибка при взаимодействии с ИИ для выбора задания: {e}")
        # В случае ошибки ИИ, можно выдать случайное задание из доступных
        import random
        return random.choice(available_tasks_for_prompt)[0]


# Создаем новый обработчик команды
@dp.message(Command("get_task"))
async def cmd_get_new_task(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    await message.answer("Подбираю для вас наиболее подходящее задание, минутку...")

    new_task_id = await get_ai_selected_task_id(user_id)

    if not new_task_id:
        await message.answer("🎉 Поздравляю! Вы выполнили все доступные задания марафона!")
        return

    # Находим задание в нашей БД
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            "SELECT task_text, report_format, karma_points FROM task_pool WHERE id = ?", (new_task_id,)
        )
        task_data = await cursor.fetchone()

    if not task_data:
        await message.answer("Произошла ошибка при поиске задания. Попробуйте еще раз.")
        logger.error(f"ИИ вернул ID задания {new_task_id}, но оно не найдено в task_pool.")
        return

    task_text, report_format, karma_points = task_data

    # Отправляем задание пользователю
    full_message = f"✨ **Ваше новое задание ({karma_points} КБ):**\n\n"
    full_message += task_text
    if report_format:
        full_message += f"\n\n*Для отчета, отправьте сообщение в формате:*\n`{report_format}`"

    await message.answer(escape_md(full_message), parse_mode=None)

    # Сохраняем в FSM, какого задания мы ждем отчет
    await state.set_state(Form.waiting_for_homework)  # Нужно будет создать это ��остояние
    await state.update_data(current_task_id=new_task_id)



def calculate_robokassa_signature(*args) -> str:
    return hashlib.md5(":".join(str(a) for a in args).encode()).hexdigest()


# Команда для отправки сообщения конкретному пользователю от имени бота
# Доступна только администраторам (или по специальному ключу, если вызывает другой бот)

@dp.message(Command("send_to_user"), F.from_user.id.in_(ADMIN_IDS_CONF))  # ADMIN_IDS_CONF - ваш список ID админов
async def cmd_send_to_user_handler(message: types.Message, command: CommandObject, bot: Bot): # Добавил bot в ар��ументы
    if not command.args:
        await message.reply("Использование: /send_to_user <user_id> <текст сообщения>\n"
                            "Или ответьте на сообщение пользователя этой командой, указав только текст: /send_to_user <текст сообщения>")
        return

    args_str = command.args
    target_user_id = None
    text_to_send = ""

    # Вариант 1: Команда дана как reply на сообщение пользователя (которое было переслано в админ-чат)
    # и мы хотим извлечь ID пользователя из этого reply.
    # Это полезно, если в админ-чате есть пересланные сообщения от пользователей.
    # Однако, это усложняет, так как нужно понять, что это именно пересланное сообщение.
    # Проще всего, если команда /send_to_user всегда ожидает user_id первым аргументом.

    # Основной вариант: /send_to_user <user_id> <текст>
    args_list = args_str.split(maxsplit=1)
    if len(args_list) == 2 and args_list[0].isdigit():
        try:
            target_user_id = int(args_list[0])
            text_to_send = args_list[1]
        except ValueError:
            await message.reply("Ошибка: User ID должен быть числом.")
            return
    else:
        await message.reply("Ошибка в формате команды. Используйте: /send_to_user <user_id> <текст сообщения>")
        return

    if not text_to_send:
        await message.reply("Ошибка: Текст сообщения не может быть пустым.")
        return

    try:
        # Отправляем сообщение пользователю.
        # Если текст может содержать Markdown от админа, используйте parse_mode.
        # Для "анонимной" пересылки parse_mode=None или экранирование, если это просто текст.
        await bot.send_message(target_user_id, text_to_send, parse_mode=None) # Или ParseMode.MARKDOWN_V2, если админ будет использовать разметку
        await message.reply(f"Сообщение успешно отправлено пользователю {target_user_id}.")
        logger.info(f"Админ {message.from_user.id} отправил сообщение пользователю {target_user_id}: {text_to_send[:50]}...")
    except TelegramBadRequest as e:
        if "chat not found" in str(e).lower() or "bot was blocked by the user" in str(e).lower():
            await message.reply(f"Не удалось отправить сообщение: пользователь {target_user_id} не найден или за��локировал бота.")
            logger.warning(f"Ошибка отправки сообщения пользователю {target_user_id} от админа {message.from_user.id}: {e}")
            # Здесь можно добавить логику деактивации пользова��еля в вашей БД, если он заблокировал бота.
        else:
            await message.reply(f"Произошла ошибка Telegram при отправке сообщения пользователю {target_user_id}: {e}")
            logger.error(f"Ошибка Telegram при отправке сообщения пользователю {target_user_id} от админа {message.from_user.id}: {e}")
    except Exception as e:
        await message.reply(f"Произошла неизвестная ошибка при отправке сообщения пользователю {target_user_id}.")
        logger.error(f"Неизвестная ошибка при отправке сообщения пользователю {target_user_id} от админа {message.from_user.id}: {e}", exc_info=True)


@dp.callback_query(BuyCourseCallback.filter())
async def cb_buy_course_prompt(query: types.CallbackQuery, callback_data: BuyCourseCallback, state: FSMContext):
    user_id = query.from_user.id
    course_numeric_id_to_buy = callback_data.course_numeric_id  # <--- ИЗМЕНЕНИЕ
    course_id_to_buy_str = await get_course_id_str(course_numeric_id_to_buy)  # Получаем строковый ID

    if not course_id_to_buy_str or course_id_to_buy_str == "Неизвестный курс":
        await query.answer("Ошибка: курс не найден", show_alert=True)
        return
    logger.info(
        f"Пользователь {user_id} нажал 'Купить/Узнать' для курса {course_id_to_buy_str} (ID: {course_numeric_id_to_buy})")

    logger.info(f"Пользователь {user_id} инициировал 'покупку' курса {course_id_to_buy_str}")

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor_course_info = await conn.execute(
            "SELECT cv.title, cv.price, cv.version_id, c.title AS main_course_title FROM course_versions cv JOIN courses c ON cv.course_id = c.course_id WHERE cv.course_id = ? ORDER BY cv.price ASC LIMIT 1",
            (course_id_to_buy_str,)
        )
        course_info = await cursor_course_info.fetchone()

    if not course_info:
        logger.error(f"Ошибка: не удалось найти информацию о курсе для покупки с ID {course_id_to_buy_str}")
        await query.answer("Информация о курсе для покупки не найдена", show_alert=True)
        return

    tariff_title, price, version_id_to_buy, main_course_title = course_info

    if price is None or price <= 0:
        logger.error(f"Ошибка: не указано или некорректное значение цены для курса {course_id_to_buy_str}")
        await query.answer(
            "Этот курс не продается напрямую или является бесплатным Возможно, для него нужен код активации",
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
        f"Для покупки курса «{escape_md(main_course_title)}» {escape_md(tariff_title)}:\n\n"
        f"Сумма к оплате: {price} руб\n\n"
        f"{escape_md(payment_instructions)}\n\n"  # Отображаем инструкцию
        f"После получения кода активации, отправьте его в этот чат",
        reply_markup=builder.as_markup(),
        parse_mode=None
    )


    """ То есть, в конце cb_buy_course_prompt не должно быть await state.set_state(...).
    И обработчики для AwaitingPaymentConfirmation и AwaitingPaymentProof становятся ненужными в этом простом сценарии.
    Пользователь увидит:
    "После оплаты с вами свяжутся... Полученный код нужно будет отправить в этот чат..."
    И когда он отправит код, сработает handle_homework (в той его части, где if not user_course_data), который вызовет activate_course. """

    #await state.set_state(AwaitingPaymentConfirmation.waiting_for_activation_code_after_payment)
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

    await message.reply(escape_md(activation_message_text), parse_mode=None)

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
            logger.info(f"{numeric_id=} Пользователь {user_id} активировал курс {actual_course_id} версии {actual_version_id}")
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
            parse_mode=None
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
                await query.answer("Вы не записаны ни на один активный курс", show_alert=True)
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

    except Exception as e3882:
        logger.error(f"Ошибка в cmd_progress_callback: {e3882}", exc_info=True)
        await query.answer("⚠️ Произошла ошибка при получении прогресса", show_alert=True)


#27-05 покажет список тарифов, текущий тариф и рассчитает разницу в цене.
@dp.callback_query(ChangeTariffCallback.filter())
async def cb_change_tariff_prompt(query: types.CallbackQuery, callback_data: ChangeTariffCallback, state: FSMContext):
    user_id = query.from_user.id
    course_id_to_change_str = callback_data.course_id_str
    course_title_obj = await get_course_title(course_id_to_change_str)
    course_title_safe = escape_md(str(course_title_obj))

    logger.info(f"Пользователь {user_id} хочет сменить тариф для курса '{course_id_to_change_str}'")

    async with aiosqlite.connect(DB_FILE) as conn:
        # 1. Получаем текущий тариф и его цену для пользователя
        cursor_current_user_tariff = await conn.execute(
            """
            SELECT uc.version_id, cv.price 
            FROM user_courses uc
            JOIN course_versions cv ON uc.course_id = cv.course_id AND uc.version_id = cv.version_id
            WHERE uc.user_id = ? AND uc.course_id = ? AND uc.status IN ('active', 'inactive')
            """,  # 'inactive' тоже учитываем, если он остановил курс и хочет сменить тариф
            (user_id, course_id_to_change_str)
        )
        current_tariff_info_row = await cursor_current_user_tariff.fetchone()

        if not current_tariff_info_row:
            await query.answer("Не удалось найти ваш текущий тариф для этого курса.", show_alert=True)
            logger.warning(f"Не найден текущий тариф для user {user_id}, course {course_id_to_change_str}")
            return

        current_user_version_id, current_user_tariff_price = current_tariff_info_row
        current_user_tariff_price = current_user_tariff_price if current_user_tariff_price is not None else 0  # Если цена None, считаем 0

        # 2. Получаем все доступные тарифы для этого курса из course_versions
        cursor_all_tariffs = await conn.execute(
            "SELECT version_id, title, price FROM course_versions WHERE course_id = ? ORDER BY price",
            (course_id_to_change_str,)
        )
        all_tariffs_for_course = await cursor_all_tariffs.fetchall()

    if not all_tariffs_for_course:
        await query.answer("Для этого курса нет других тарифов для смены.", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    message_text_parts = [f"Выберите новый тариф для курса «*{course_title_safe}*»:\n"]
    found_current_in_list = False

    for version_id_option, tariff_title_option_raw, price_option_raw in all_tariffs_for_course:
        tariff_title_option_safe = escape_md(str(tariff_title_option_raw))
        price_option = price_option_raw if price_option_raw is not None else 0  # Если цена None, считаем 0

        price_option_str = f"{price_option} руб." if price_option > 0 else "Бесплатно"  # или "Специальная цена"

        line = f"Тариф «*{tariff_title_option_safe}*» - {escape_md(price_option_str)}"

        if version_id_option == current_user_version_id:
            line += " (*ваш текущий*)"
            message_text_parts.append(line)
            found_current_in_list = True
        else:
            message_text_parts.append(line)
            price_difference = price_option - current_user_tariff_price
            button_text = f"Выбрать «{tariff_title_option_safe}»"

            if price_difference > 0:
                button_text += f" (доплатить {price_difference} руб.)"
            elif price_difference < 0:  # Новый тариф дешевле
                button_text += f" (без доплаты)"  # Возврата нет
            else:  # Цена такая же (маловероятно для другого тарифа, но возможно)
                button_text += f" (цена та же)"

            builder.button(
                text=button_text,
                callback_data=SelectNewTariffToUpgradeCallback(  # Используем новый CallbackData
                    course_id_str=course_id_to_change_str,
                    new_version_id=version_id_option
                    # price_difference и new_tariff_full_price здесь не нужны,
                    # их можно будет рассчитать/получить в следующем обработчике
                ).pack()
            )

    if not found_current_in_list and current_user_version_id:
        # Если текущий тариф пользователя почему-то не нашелся в общем списке тарифов курса
        current_tariff_name_obj = settings.get("tariff_names", {}).get(current_user_version_id,
                                                                       f"Тариф {current_user_version_id}")
        current_tariff_name_safe = escape_md(str(current_tariff_name_obj))
        message_text_parts.append(
            f"Ваш текущий тариф: «*{current_tariff_name_safe}*» (цена {current_user_tariff_price} руб.)")

    builder.adjust(1)  # Каждая кнопка тарифа на новой строке

    # Кнопка "Назад в меню активного курса"
    # Нужно получить current_lesson для активного курса
    active_course_data_for_back = await get_user_course_data(user_id)  # (course_numeric_id, current_lesson, version_id)
    if active_course_data_for_back and await get_course_id_str(
            active_course_data_for_back[0]) == course_id_to_change_str:
        builder.row(InlineKeyboardButton(
            text="⬅️ Назад в меню курса",
            callback_data=ShowActiveCourseMenuCallback(
                course_numeric_id=active_course_data_for_back[0],  # числовой ID
                lesson_num=active_course_data_for_back[1]
            ).pack()
        ))
    else:  # Если текущий активный курс другой или его нет, возвращаемся к общему списку
        builder.row(InlineKeyboardButton(text="⬅️ К списку всех курсов", callback_data="select_other_course"))

    final_text = "\n".join(message_text_parts)
    if query.message:
        try:
            await query.message.edit_text(final_text, reply_markup=builder.as_markup(),
                                          parse_mode=None)
        except TelegramBadRequest as e_edit_tariff:
            logger.error(f"Ошибка edit_text в cb_change_tariff_prompt: {e_edit_tariff}")
            # Если не удалось отредактировать, отправим новым сообщением
            await bot.send_message(user_id, final_text, reply_markup=builder.as_markup(),
                                   parse_mode=None)
            if query.message: await query.message.delete()  # Попытаемся удалить старое, если новое отправлено
    await query.answer()


# 27-05 покажет детали доплаты и инструкцию по оплате.
@dp.callback_query(SelectNewTariffToUpgradeCallback.filter())
async def cb_confirm_new_tariff_and_pay_diff(query: types.CallbackQuery,
                                             callback_data: SelectNewTariffToUpgradeCallback, state: FSMContext):
    user_id = query.from_user.id
    course_id_str = callback_data.course_id_str
    new_selected_version_id = callback_data.new_version_id

    logger.info(f"Пользователь {user_id} выбрал новый тариф {new_selected_version_id} для курса {course_id_str}")

    # ... (ваш код для получения current_price, new_tariff_title_raw, new_tariff_price) ...
    # Убедитесь, что этот код корректно получает данные.
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor_current = await conn.execute(
            "SELECT cv.price FROM user_courses uc JOIN course_versions cv ON uc.course_id=cv.course_id AND uc.version_id=cv.version_id WHERE uc.user_id=? AND uc.course_id=? AND uc.status IN ('active','inactive')",
            (user_id, course_id_str)
        )
        current_tariff_price_row = await cursor_current.fetchone()
        current_price = current_tariff_price_row[0] if current_tariff_price_row and current_tariff_price_row[
            0] is not None else 0

        cursor_new = await conn.execute(
            "SELECT title, price FROM course_versions WHERE course_id=? AND version_id=?",
            (course_id_str, new_selected_version_id)
        )
        new_tariff_info_row = await cursor_new.fetchone()
        if not new_tariff_info_row:
            await query.answer("Ошибка: выбранный новый тариф не найден", show_alert=True)
            logger.error(
                f"Новый тариф {new_selected_version_id} для курса {course_id_str} не найден в course_versions")
            return

        new_tariff_title_raw, new_tariff_price_raw = new_tariff_info_row
        new_tariff_price = new_tariff_price_raw if new_tariff_price_raw is not None else 0

    course_title_obj = await get_course_title(course_id_str)
    course_title_safe = escape_md(str(course_title_obj))
    new_tariff_title_safe = escape_md(str(new_tariff_title_raw))  # Экранируем здесь

    price_difference = round(new_tariff_price - current_price, 2)  # Округляем на всякий случай

    text_parts = [
        f"Вы собираетесь сменить тариф для курса «*{course_title_safe}*» на «*{new_tariff_title_safe}*»."
        # Экранируем точку
    ]

    payment_needed = False
    if price_difference > 0:
        text_parts.append(f"Сумма к доплате: *{price_difference} руб*.")  # Экранируем точку
        payment_instructions_from_env = PAYMENT_INSTRUCTIONS_TEMPLATE

        payment_instructions_formatted = payment_instructions_from_env.format(
            price=price_difference,
            course_title=str(course_title_obj),  # Неэкранированные для format
            new_tariff_title=str(new_tariff_title_raw),  # Неэкранированные для format
            user_id=user_id
        )
        payment_instructions_safe = escape_md(payment_instructions_formatted)  # Экранируем результат форматирования
        text_parts.append(f"\n*Инструкция по оплате разницы:*\n{payment_instructions_safe}")
        payment_needed = True
    elif price_difference < 0:
        text_parts.append(
            f"Новый тариф дешевле вашего текущего. Переход будет без доплаты.")  # Экранируем точки и скобки
    else:
        text_parts.append(
            f"Цена нового тарифа такая же, как у вашего текущего. Переход будет без доплаты.")  # Экранируем точку

    text_parts.append(
        f"\nПосле смены тарифа ваш прогресс по текущему уровню курса будет сброшен, и вы начнете его заново с новым тарифом.")  # Экранируем точку

    if payment_needed:
        text_parts.append(
            f"После оплаты разницы и получения нового кода активации (или если у вас уже есть код для тарифа «*{new_tariff_title_safe}*»), отправьте его в этот чат.")  # Экранируем скобки и точку
    else:
        text_parts.append(
            f"Если у вас есть код активации для тарифа «*{new_tariff_title_safe}*», отправьте его в этот чат для смены тарифа. Если код не требуется для этого перехода (например, бесплатный тариф или автоматическое обновление после подтверждения админом), обратитесь в поддержку для завершения смены тарифа.")  # Экранируем скобки и точки
    logger.info(f"Проверка текста перед отправкой:\n{text_parts}")
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Назад к выбору тарифов",
                   callback_data=ChangeTariffCallback(course_id_str=course_id_str).pack())
    builder.adjust(1)

    final_text = "\n".join(text_parts)

    if query.message:
        try:
            await query.message.edit_text(final_text, reply_markup=builder.as_markup(),
                                          parse_mode=None)
            await query.answer()  # Answer после успешного edit_text
            logger.info(f"555Сообщение успешно отредактировано после выбора нового тарифа")
        except TelegramBadRequest as e_edit_confirm_tariff:  # Уникальное имя переменной
            logger.error(f"333Ошибка edit_text в cb_confirm_new_tariff_and_pay_diff: {e_edit_confirm_tariff}")
            # Попробуем отправить новым сообщением
            try:
                if query.message: await query.message.delete()  # Сначала удаляем старое, если есть
            except Exception:
                pass
            await bot.send_message(user_id, final_text, reply_markup=builder.as_markup(),
                                   parse_mode=None)
            await query.answer()  # Answer после send_message
        except Exception as e_generic_confirm_tariff:  # Уникальное имя переменной
            logger.error(f"Общая ошибка в cb_confirm_new_tariff_and_pay_diff: {e_generic_confirm_tariff}",
                         exc_info=True)
            await query.answer("Произошла ошибка  Попробуйте еще раз", show_alert=True)
    else:
        # Если query.message None, что маловероятно для callback_query
        await bot.send_message(user_id, final_text, reply_markup=builder.as_markup(), parse_mode=None)
        await query.answer()

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

async def old_check_state(message: types.Message, state: FSMContext) -> bool:
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
    except Exception as e3979:
        logger.error(f"Error updating homework status in database: {e3979}")

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
    except TelegramBadRequest as e4035:
        logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e4035}")
    except Exception as e4037:
        logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e4037}", exc_info=True)


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
    logger.info(
        f"process_homework_action: action={callback_data.action}, admin_msg_id={callback_query.message.message_id}")
    # await callback_query.answer() # Перенесем answer после потенциального редактирования

    try:
        user_id = callback_data.user_id
        course_numeric_id = callback_data.course_id  # Это числовой ID из AdminHomeworkCallback
        course_id_str = await get_course_id_str(course_numeric_id)  # Получаем строковый ID
        lesson_num = callback_data.lesson_num
        # message_id из callback_data - это ID ИСХОДНОГО сообщения студента, а не сообщения в админ-чате.
        # ID сообщения в админ-чате, на котором кнопки: callback_query.message.message_id
        admin_message_id_with_buttons = callback_query.message.message_id
        action = callback_data.action
        admin_user_id = callback_query.from_user.id

        if not course_id_str or course_id_str == "Неизвестный курс":
            logger.error(f"Не удалось получить строковый ID курса для числового ID: {course_numeric_id}")
            await callback_query.answer("Ошибка: не удалось определить курс.", show_alert=True)
            return

        current_fsm_state = await state.get_state()
        if current_fsm_state == Form.feedback:
            logger.warning(
                f"process_homework_action вызван, когда бот уже в состоянии Form.feedback. Возможно, админ нажал кнопку, пока бот ждал текст. Очищаю состояние.")
            # Можно либо проигнорировать этот вызов, либо очистить состояние и продолжить,
            # предполагая, что новое нажатие кнопки важнее.
            # Пока просто залогируем и продолжим. Если это создает проблемы, нужно будет решить, как лучше обрабатывать.
            # await state.clear() # Опционально, если хотим прервать ожидание текста

        if action == "approve_hw":
            await callback_query.answer("Одобряю ДЗ...")
            await handle_homework_result(user_id, course_id_str, course_numeric_id, lesson_num, admin_user_id, "", True,
                                         callback_query, admin_message_id_with_buttons)
        elif action == "reject_hw":
            await callback_query.answer("Отклоняю ДЗ...")
            await handle_homework_result(user_id, course_id_str, course_numeric_id, lesson_num, admin_user_id,
                                         "Домашнее задание требует доработки.", False, callback_query,
                                         admin_message_id_with_buttons)  # Добавил дефолтный текст
        elif action in ["approve_reason", "reject_reason"]:
            # ===== НАЧАЛО БЛОКИРОВКИ =====
            HOMEWORK_BEING_PROCESSED[admin_message_id_with_buttons] = admin_user_id
            logger.info(
                f"ДЗ с admin_message_id: {admin_message_id_with_buttons} заблокировано для ручной проверки админом {admin_user_id}.")
            # =============================
            # Сохраняем все необходимые данные, включая ID сообщения с кнопками
            await state.update_data(
                student_user_id_for_feedback=user_id,  # Переименовал для ясности в state
                course_id_str_for_feedback=course_id_str,
                course_numeric_id_for_feedback=course_numeric_id,
                lesson_num_for_feedback=lesson_num,
                admin_message_id_for_feedback=admin_message_id_with_buttons,
                # ID сообщения, которое нужно будет обновить
                action_type_for_feedback=action.split("_")[0],  # "approve" or "reject"
                admin_id_for_feedback=admin_user_id,
                # callback_query_for_feedback=callback_query # callback_query не сериализуется в FSM, сохранять не нужно
            )

            prompt_text = "Пожалуйста, введите ваш комментарий для студента (одобрение):" if action == "approve_reason" else "Пожалуйста, введите причину отклонения для студента:"
            # Определяем исходный текст/caption сообщения, к которому добавим prompt_text
            original_message_content = ""
            if callback_query.message.text:
                original_message_content = callback_query.message.text
            elif callback_query.message.caption:
                original_message_content = callback_query.message.caption

            current_message_text_or_caption = callback_query.message.caption if callback_query.message.photo or callback_query.message.document or callback_query.message.video else callback_query.message.text
            if current_message_text_or_caption is None:
                current_message_text_or_caption = ""  # На случай, если и caption и text None (хотя для ДЗ это маловероятно)

            new_text_for_admin_message = current_message_text_or_caption + f"\n\n⏳ {escape_md(prompt_text)}"  # Экранируем prompt_text

            try:
                # Если это сообщение с медиа, мы должны использовать edit_message_caption
                # Если это текстовое сообщение, то edit_message_text
                if callback_query.message.photo or callback_query.message.document or \
                        callback_query.message.video or callback_query.message.audio or \
                        callback_query.message.voice or callback_query.message.animation:
                    await bot.edit_message_caption(
                        chat_id=callback_query.message.chat.id,
                        message_id=admin_message_id_with_buttons,
                        caption=new_text_for_admin_message,  # Не забыть про лимиты на длину caption
                        reply_markup=None,  # Убираем кнопки
                        parse_mode = None
                    )
                else:  # Текстовое сообщение
                    await bot.edit_message_text(
                        chat_id=callback_query.message.chat.id,
                        message_id=admin_message_id_with_buttons,
                        text=new_text_for_admin_message,
                        reply_markup=None,
                        parse_mode=None
                    )
                await callback_query.answer()
            except TelegramBadRequest as e_edit_prompt:  # Уникальный идентификатор
                logger.error(
                    f"Не удалось отредактировать сообщение для запроса причины (ID: {admin_message_id_with_buttons}): {e_edit_prompt}")
                await callback_query.answer("Ошибка при запросе причины.", show_alert=True)
                await state.clear()
                return

            await state.set_state(Form.feedback)

    except Exception as e227:
        logger.error(f"❌ Ошибка в process_homework_action: {e227}", exc_info=True)
        await callback_query.answer("Произошла внутренняя ошибка.", show_alert=True)
        await state.clear()  # Очищаем состояние при любой ошибке в этом обработчике


# Обработка callback-запроса для оставления отзыва
@dp.callback_query(F.data == "menu_feedback")
async def cmd_feedback(query: types.CallbackQuery, state: FSMContext):
    """Обработка callback-запроса для оставления отзыва."""
    await query.message.edit_text("Пожалуйста, напишите ваш отзыв:")
    await state.set_state(Form.feedback)
    await query.answer()

@dp.message(Form.feedback, F.chat.id == ADMIN_GROUP_ID) # Убедимся, что это админ и он в админ-группе
async def process_feedback(message: types.Message, state: FSMContext):
    logger.info(f"process_feedback: получен текст от админа {message.from_user.id} для ДЗ.")
    try:
        user_data = await state.get_data()
        # Используем ключи, которые сохранили в process_homework_action
        student_user_id = user_data.get("student_user_id_for_feedback")
        course_id_str = user_data.get("course_id_str_for_feedback")
        course_numeric_id = user_data.get("course_numeric_id_for_feedback")
        lesson_num = user_data.get("lesson_num_for_feedback")
        admin_message_id_to_update = user_data.get("admin_message_id_for_feedback") # ID сообщения с ДЗ в админке
        action_type = user_data.get("action_type_for_feedback")  # "approve" or "reject"
        # admin_id_who_clicked_button = user_data.get("admin_id_for_feedback") # ID того, кто нажал кнопку

        # ID админа, который написал текст фидбэка
        admin_id_who_wrote_feedback = message.from_user.id
        feedback_text = message.text

        if not all(
                [student_user_id, course_id_str, course_numeric_id is not None, lesson_num is not None, action_type]):
            logger.error(f"Неполные данные в FSM для process_feedback: {user_data}")
            await message.reply(escape_md(
                "Ошибка: не удалось получить все данные для обработки отзыва. Пожалуйста, начните проверку ДЗ заново."),
                                parse_mode=None)
            await state.clear()
            return

        is_approved = action_type == "approve"

        # Вызываем handle_homework_result, передавая ID сообщения в админке для обновления
        await handle_homework_result(
            user_id=student_user_id,
            course_id=course_id_str,
            course_numeric_id=course_numeric_id,
            lesson_num=lesson_num,
            admin_id=admin_id_who_wrote_feedback,  # Админ, который написал фидбэк
            feedback_text=feedback_text,
            is_approved=is_approved,
            #bot=bot,
            callback_query=None,  # Здесь нет callback_query, это обычное сообщение
            original_admin_message_id_to_delete=admin_message_id_to_update
            # Передаем ID сообщения для обновления/ответа
        )
        # Сообщение админу, что его фидбэк принят, уже не нужно, т.к. handle_homework_result отправит статус.
        # await message.reply(escape_md(f"Комментарий для ДЗ (user {student_user_id}) отправлен."), parse_mode=None)

    except Exception as e:
        logger.error(f"❌ Ошибка в process_feedback: {e}", exc_info=True)
        await message.reply(escape_md("Произошла ошибка при обработке вашего комментария."),
                            parse_mode=None)
    finally:
        # ===== СНЯТИЕ БЛОКИРОВКИ =====
        if admin_message_id_to_update and admin_message_id_to_update in HOMEWORK_BEING_PROCESSED:
            del HOMEWORK_BEING_PROCESSED[admin_message_id_to_update]
            logger.info(f"Блокировка с ДЗ (admin_message_id: {admin_message_id_to_update}) снята.")
        # ==============================
        await state.clear()  # Обязательно очищаем состояние



async def get_daily_task_menu(user_id: int, course_id: str, step_number: int) -> dict | None:
    """     Формирует ежедневное меню заданий: 1 главное + несколько дополнительных.    """
    async with aiosqlite.connect(DB_FILE) as conn:
        # 1. Получаем категорию "задания дня" из marathon_flow
        cursor = await conn.execute(
            "SELECT task_category FROM marathon_flow WHERE course_id = ? AND step_number = ?",
            (course_id, step_number)
        )
        flow_step = await cursor.fetchone()
        if not flow_step or not flow_step[0]:
            logger.error(f"Не найден шаг {step_number} или категория для курса {course_id} в marathon_flow")
            return None

        main_category = flow_step[0]

        # 2. Выбираем одно ГЛАВНОЕ задание из нужной категории
        # Исключаем те, что пользователь уже делал (нужна таблица user_tasks_log)
        main_task_cursor = await conn.execute(
            "SELECT * FROM task_templates WHERE category = ? ORDER BY RANDOM() LIMIT 1",
            (main_category,)
        )
        main_task = await main_task_cursor.fetchone()

        if not main_task:
            logger.warning(f"Не найдено ни одного задания для главной категории {main_category}!")
            return None

        # 3. Выбираем 2 ДОПОЛНИТЕЛЬНЫХ задания из ДРУГИХ категорий
        secondary_tasks_cursor = await conn.execute(
            "SELECT * FROM task_templates WHERE category != ? ORDER BY RANDOM() LIMIT 2",
            (main_category,)
        )
        secondary_tasks = await secondary_tasks_cursor.fetchall()

        # Преобразуем кортежи в словари для удобства
        columns = [desc[0] for desc in main_task_cursor.description]
        main_task_dict = dict(zip(columns, main_task))

        secondary_tasks_list = [dict(zip(columns, row)) for row in secondary_tasks]

        return {
            "main_task": main_task_dict,
            "secondary_tasks": secondary_tasks_list
        }


# ЗАГЛУШКА: Вам нужно реализовать эту функцию, чтобы она брала данные из user_courses
async def get_user_current_marathon_step(user_id):
    # Пример: SELECT course_id, current_lesson FROM user_courses WHERE user_id = ? AND status = 'active'
    return "karma_marafon_v1", 1  # Возвращает (course_id, step_number)

# Обработчик кнопки "Получить задания на сегодня"
@dp.callback_query(F.data == "get_daily_tasks")
async def cb_get_daily_tasks(query: types.CallbackQuery):
    user_id = query.from_user.id

    course_id, step_number = await get_user_current_marathon_step(user_id)

    task_menu = await get_daily_task_menu(user_id, course_id, step_number)

    if not task_menu:
        await query.answer("Не удалось составить для вас список заданий. Попробуйте позже.")
        return

    # Формируем красивое сообщение
    main_task = task_menu['main_task']
    message_text = (
        f"✨ *Главная практика дня ({main_task['karma_points']} КБ):*\n"
        f"_{escape_md(main_task['title'])}_\n"
        f"{escape_md(main_task['description'])}\n\n"
        f"🔍 *Дополнительные возможности (по 1-2 КБ за каждое):*\n"
    )

    builder = InlineKeyboardBuilder()

    # Кнопка для основного задания
    builder.button(text=f"✅ Выполнить: {main_task['title'][:30]}...",
                   callback_data=f"do_task:{main_task['id']}")  # Отправляем ID из БД

    for task in task_menu['secondary_tasks']:
        message_text += f"• {escape_md(task['title'])} ({task['karma_points']} КБ)\n"
        builder.button(text=f"✅ Выполнить: {task['title'][:30]}...",
                       callback_data=f"do_task:{task['id']}")

    builder.adjust(1)

    await query.message.edit_text(message_text, reply_markup=builder.as_markup(), parse_mode=None)
    await query.answer()


# вызывается из process_feedback - вверху функция
async def handle_homework_result(
        user_id: int, course_id: str, course_numeric_id: int, lesson_num: int,
        admin_id: int, feedback_text: str, is_approved: bool,
        callback_query: types.CallbackQuery = None, original_admin_message_id_to_delete: int = None
):
    log_prefix = f"handle_homework_result(user={user_id}, lesson={lesson_num}):"
    logger.info(
        f"{log_prefix} Запуск. approved={is_approved}, admin_id={admin_id}, admin_msg_id={original_admin_message_id_to_delete}")

    # ID сообщения в админ-чате, которое нужно обработать.
    # Оно приходит либо из callback_query (если нажал админ), либо напрямую (если вызвал ИИ).
    message_id_to_process = original_admin_message_id_to_delete
    if callback_query and callback_query.message:
        message_id_to_process = callback_query.message.message_id

    if not message_id_to_process:
        logger.error(f"{log_prefix} Критическая ошибка: не удалось определить ID сообщения для обработки.")
        return

    # ===== Блок защиты от гонок =====
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверяем, существует ли еще эта ДЗ в таблице ожидающих
            cursor = await conn.execute(
                "SELECT 1 FROM pending_admin_homework WHERE admin_message_id = ?",
                (original_admin_message_id_to_delete,)
            )
            if not await cursor.fetchone():
                logger.warning(f"{log_prefix} Попытка повторной обработки ДЗ. Игнорируем.")
                if callback_query:
                    # Отправляем юмористический ответ
                    if ADMIN_GROUP_ID and original_admin_message_id_to_delete:
                        try:
                            funny_phrase = random.choice(RACE_CONDITION_HUMOR)
                            await bot.send_message(
                                chat_id=ADMIN_GROUP_ID,
                                text=f"🤖 ИИ-ассистент (про ДЗ выше):\n{funny_phrase}",
                                reply_to_message_id=original_admin_message_id_to_delete,
                                parse_mode=None
                            )
                        except Exception as e_humor:
                            logger.error(f"Не удалось отправить юмористический ответ: {e_humor}")
                    # --- КОНЕЦ БЛОКА ЮМОРА ---
                    await callback_query.answer("Это ДЗ уже было обработано.", show_alert=True)
                return
    except Exception as e_check:
        logger.error(f"{log_prefix} Ошибка при пред-проверке статуса ДЗ: {e_check}")
        if callback_query:
            await callback_query.answer("Ошибка при проверке статуса ДЗ", show_alert=True)
        return
    # ===== КОНЕЦ БЛОКА ЗАЩИТЫ =====
    
    # Проверяем, было ли ДЗ отправлено на n8n (админ имеет приоритет)
    if message_id_to_process in homework_sent_to_n8n:
        logger.info(f"{log_prefix} ДЗ уже было отправлено на n8n, но админ имее���� приоритет")
        homework_sent_to_n8n.discard(message_id_to_process)

    # Основная логика
    try:
        # 1. Обновляем статус в БД
        hw_status = "approved" if is_approved else "rejected"
        await update_homework_status(user_id, course_id, lesson_num, hw_status)  # Обновляем статус ДЗ
        logger.info(f"{log_prefix} Статус ДЗ в БД обн��влен на '{hw_status}'.")

        async with aiosqlite.connect(DB_FILE) as conn:
            # 2. Готовим данные для сообщений
            # === ИСПРАВЛЕНИЕ ЗДЕСЬ ===
            cursor_uc = await conn.execute(
                "SELECT version_id, level FROM user_courses WHERE user_id = ? AND course_id = ? AND status='active'",
                (user_id, course_id))
            user_course_info = await cursor_uc.fetchone() or ('v0', 1)
            version_id, user_level = user_course_info
            tariff_name = get_tariff_name(version_id)
            logger.info(f"{log_prefix} Получены данные о тарифе: {tariff_name}, уровень: {user_level}")
            cursor_tl = await conn.execute(
                "SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ? AND lesson_num > 0", (course_id,))
            total_lessons_data = await cursor_tl.fetchone()
            # ========================

            total_lessons = total_lessons_data[0] if total_lessons_data and total_lessons_data[0] is not None else 0

            # 3. Логика и отправка сообщения пользователю
            if is_approved and lesson_num >= total_lessons and total_lessons > 0:
                course_title_safe = escape_md(await get_course_title(course_id))
                message_text_completion = (
                    f"🎉 Поздравляем с успешным завершением курса «{course_title_safe}»! 🎉\n\n" "Вы прошли все уроки. Что вы хотите сделать дальше?")
                builder_completion = InlineKeyboardBuilder()
                builder_completion.button(text="Выбрать другой курс", callback_data="select_other_course")
                builder_completion.button(text="Оставить отзыв", callback_data="leave_feedback")
                await bot.send_message(user_id, message_text_completion, reply_markup=builder_completion.as_markup(),
                                       parse_mode=None)
                await conn.execute(
                    "UPDATE user_courses SET status = 'completed', is_completed = 1 WHERE user_id = ? AND course_id = ?",
                    (user_id, course_id))
            else:
                message_to_user_main_part = ""
                if is_approved:
                    message_to_user_main_part = f"✅ Ваше домашнее задание по курсу *{escape_md(course_id)}*, урок *{lesson_num}* принято"
                    if feedback_text: message_to_user_main_part += f"\n\n*Комментарий:*\n{escape_md(feedback_text)}"
                    # --- НАЧАЛО ИНТЕГРАЦИИ --- todo 05-07
                    # Начисляем баллы за успешное ДЗ старого типа
                    course_title = await get_course_title(course_id)
                    reason_text = f"Успешное выполнение урока {lesson_num} курса «{course_title}»"
                    await award_karma_points(user_id, 10, reason_text)  # Например, даем 10 баллов за любой урок
                    # --- КОНЕЦ ИНТЕГРАЦИИ ---
                else:
                    message_to_user_main_part = f"❌ Ваше домашнее задание по курсу *{escape_md(course_id)}*, урок *{lesson_num}* отклонено"
                    if feedback_text: message_to_user_main_part += f"\n\n*Причина:*\n{escape_md(feedback_text)}"

                await bot.send_message(user_id, message_to_user_main_part, parse_mode=None)
                await send_main_menu(user_id, course_id, lesson_num, version_id, homework_pending=(not is_approved),
                                     user_course_level_for_menu=user_level)

            # ========================

            # 4. Формируем и отправляем ОДНО сообщение в админ-чат
            if ADMIN_GROUP_ID:
                admin_actor_name = "🤖 ИИ ассистент"
                if admin_id != 0:
                    try:
                        actor_chat = await bot.get_chat(admin_id)
                        admin_actor_name = escape_md(actor_chat.full_name or f"ID:{admin_id}")
                    except Exception:
                        admin_actor_name = f"Актор ID:{admin_id}"

                user_name_safe = escape_md(await get_user_name(user_id))
                action_str = "✅ ОДОБРЕНО" if is_approved else "❌ ОТКЛОНЕНО"

                final_admin_notification = (f"Статус ДЗ (сообщение выше): *{action_str}*\n"
                                            f"Студент: *{user_name_safe}* (ID: {user_id})\n"
                                            f"Проверил: *{admin_actor_name}*")
                if feedback_text:
                    final_admin_notification += f"\n\n*Комментарий:*\n_{escape_md(feedback_text)}_"

                if message_id_to_process:
                    try:
                        await bot.edit_message_reply_markup(chat_id=ADMIN_GROUP_ID, message_id=message_id_to_process)
                        await bot.send_message(ADMIN_GROUP_ID, final_admin_notification,
                                               reply_to_message_id=message_id_to_process, parse_mode=None)
                        logger.info(f"{log_prefix} Единое уведомление в админ-чат отправлено.")
                    except Exception as e_admin_notify:
                        logger.error(f"{log_prefix} Не удалось отправит�� уведомление в админ-чат: {e_admin_notify}")

            # 5. Удаляем из pending и логируем
            if message_id_to_process:
                await conn.execute("DELETE FROM pending_admin_homework WHERE admin_message_id = ?",
                                   (message_id_to_process,))

            await conn.commit()
            logger.info(
                f"{log_prefix} Запись о ДЗ #{message_id_to_process} удалена из pending_admin_homework и изменения закоммичены.")

            action_details = "одобрено" if is_approved else "отклонено"
            if feedback_text: action_details += " с комментарием"
            await log_action(user_id, "HOMEWORK_REVIEWED", course_id, lesson_num, new_value=hw_status,
                             details=f"Проверил: {admin_id}. Результат: {action_details}")

        if callback_query:
            await callback_query.answer()

    except Exception as e4324:
        logger.error(f"❌ Ошибка в handle_homework_result: {e4324}", exc_info=True)
        if callback_query:
            await callback_query.answer("Произошла ошибка при обработке ДЗ.", show_alert=True)


async def get_user_name(user_id: int) -> str:
    """Получает имя пользователя по ID."""
    logger.info(F"user_id=  {user_id}")
    try:
        user = await bot.get_chat(user_id)
        return user.first_name or user.username or str(user_id)
    except Exception as e4336:
        logger.error(f"Ошибка при получении имени пользователя: {e4336}")
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
            await message.answer("Не могу определить, какому пользователю отправить это сообщение.  Убедите����, что вы ответили на запрос поддержки, инициированный кнопкой.", parse_mode=None)

    except Exception as e4386:
        logger.error(f"Ошибка при обработке ответа админа: {e4386}")
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
                    "✅ В��ш запрос отправлен в поддержку. Ожидайте ответа.",
                    reply_markup=types.ReplyKeyboardRemove(),  # Убираем клавиатуру
                    parse_mode=None
                )

                logger.info(f"handle_support_message всё отправили ")

            except TelegramBadRequest as e4440:
                logger.error(f"Ошибка отправки сообщения: {e4440}")
                await message.answer("❌ Не удалось отправить запрос. Попробуйте позже.",parse_mode=None)
        else:
            await message.answer("⚠️ Служба поддержки временно недоступна.",parse_mode=None)

    except Exception as e4445:
        logger.error(f"Ошибка при обработке сообщения от пользователя: {e4445}")
        await message.answer("❌ Произошла ошибка при обработ��е запроса. Попробуйте позже.", parse_mode=None)

    finally:
        # Сбрасываем состояние
        await state.clear()



# =========================== теперь всё остальное

@dp.message(F.text, F.chat.type == "private", ~F.text.startswith('/'))
@db_exception_handler
async def handle_text(message: types.Message, state: FSMContext):
    """
    Минималистичный обработчик текста. Проверяет курс и передаёт дальше.
    """
    # Проверяем, есть ли активное FSM состояние
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"handle_text: пропускаем, есть активное состояние FSM: {current_state}")
        # Используем continue_propagation чтобы пропустить другим обработчикам
        from aiogram.dispatcher.event.bases import UNHANDLED
        return UNHANDLED
    
    user_id = message.from_user.id
    text = message.text.strip()
    logger.info(f"handle_text: {text=} {user_id=}")

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
    except Exception as e4510:
        logger.error(f"Ошибка при обработке оценки поддержки: {e4510}")
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
        except (sqlite3.OperationalError, aiosqlite.Error) as e4525:
            logger.warning(f"DB error on attempt {attempt + 1}: {e4525}. Retrying in {delay}s...")
            if attempt == retries - 1:
                logger.error(f"Max retries reached. Aborting query: {query}")
                raise  # Re-raise the exception if retries are exhausted
            await asyncio.sleep(delay)
        except Exception as e4530:
            logger.error(f"Unexpected error during DB execution: {e4530}")
            raise


# ----------------- новый обработчик и текстовой домашки и фото -------- от пользователя ------------
@dp.message(F.content_type.in_({'photo', 'document', 'text'}), F.chat.type == "private", ~F.text.startswith('/'))
@db_exception_handler
async def handle_homework(message: types.Message):
    """Обрабатывает отправку домашних заданий (фото/документы/текст)"""
    user_id = message.from_user.id
    logger.info(f"=" * 80)
    logger.info(f"handle_homework START")
    logger.info(f"ЛОГ #0: user_id={user_id}")
    logger.info(f"ЛОГ #0: message.text={message.text}")
    logger.info(f"=" * 80)

    # Получаем данные о курсе
    user_course_data = await get_user_course_data(user_id)
    logger.info(f"ЛОГ #1: user_course_data={user_course_data}")
    logger.info(f" строка 4162 {user_course_data=}")
    if not user_course_data:
        logger.info(f"ЛОГ #2: user_course_data=None, активируем курс")
        # ОСТАНОВИТЬ шедулер чтобы не было гонки данных
        await stop_lesson_schedule_task(user_id)
        logger.info(f"ЛОГ #2a: Шедулер остановлен перед активацией")
        
        await message.answer("Проверяю код", parse_mode=None)
        activation_result = await activate_course(user_id, message.text) # Get status code
        is_activated = activation_result[0]
        activation_message = activation_result[1]
        logger.info(f"ЛОГ #3: activation_result={activation_result}")
        logger.info(f"ЛОГ #4: is_activated={is_activated}")

        await message.answer(activation_message, parse_mode=None) # answer

# ======================== вот тут активация ===================================
        if is_activated:
            logger.info(f"ЛОГ #5: 444 is_activated now")
            # ОСТАНОВИТЬ шедулер ещё раз на всякий случай
            await stop_lesson_schedule_task(user_id)
            logger.info(f"ЛОГ #5a: Шедулер остановлен перед загрузкой данных")
            
            # Load course data to get course_id and version_id
            async with aiosqlite.connect(DB_FILE) as conn:
                try:
                    cursor = await safe_db_execute(
                        conn,
                        "SELECT course_id, version_id FROM user_courses WHERE user_id = ?",
                        (user_id,)
                    )

                    new_course_data = await cursor.fetchone()
                    course_id, version_id = new_course_data

                    logger.info(f"ЛОГ #6: ACTIVATION: course_id={course_id}, version_id={version_id} ИЗ БД")
                    logger.info(f"ЛОГ #7: ПЕРЕД КОММИТОМ course_id={course_id}")

                    await conn.commit()  # СРАЗУ коммит!
                    logger.info(f"ЛОГ #8: COMMIT после активации: course_id={course_id}, version_id={version_id}")
                    logger.info(f"ЛОГ #9: ПОСЛЕ КОММИТА course_id={course_id}")

                except Exception as e4585:
                    logger.error(f" 😱 Ой-ой! Какая-то ошибка с базой после активации: {e4585}")
                    await message.answer(" 😥 Кажется, база данных уснула. Попробуйте чуть позже", parse_mode=None)
                    return

            # ТЕПЕРЬ получаем доп информацию ПОСЛЕ коммита
            logger.info(f"ЛОГ #10: Вызов get_course_title для course_id={course_id}")
            course_title = await get_course_title(course_id)
            logger.info(f"ЛОГ #11: course_title={course_title}")

            logger.info(f"ЛОГ #12: Вызов get_course_id_int для course_id={course_id}")
            course_numeric_id = await get_course_id_int(course_id)
            logger.info(f"ЛОГ #13: course_numeric_id={course_numeric_id}")

            tariff_name = get_tariff_name(version_id)
            logger.info(f"ЛОГ #14: tariff_name={tariff_name}")

            logger.info(f"ЛОГ #15: ПЕРЕД send_course_description course_id={course_id}")

            if course_numeric_id == 0:
                logger.error(f"Не найден курс {course_id=}")
            lesson_num = 0  # After activation the first lesson is shown

            # Get the lesson interval information based on user_id and version
            message_interval = settings.get("message_interval", 24)
            logger.info(f" message_interval = {message_interval} ")

            await send_course_description(user_id, course_id)
            
            # ПЕРЕЗАПУСТИТЬ шедулер для нового курса
            await start_lesson_schedule_task(user_id)
            logger.info(f"ЛОГ #16: Шедулер перезапущен для нового курса")

            logger.info(f"3 перед созданием клавиатуры course_id={course_id}, course_numeric_id={course_numeric_id}")
            keyboard = get_main_menu_inline_keyboard(  # await убрали
                course_numeric_id = course_numeric_id,
                lesson_num=lesson_num,
                user_tariff=version_id
            )

            # Проверяем тест-режим
            is_test_mode = user_id in ADMIN_TEST_MODE
            test_mode_text = f"⚡ Интервал: {TEST_MODE_INTERVAL_MINUTES} мин (тест-режим)\n💡 Выключить: /test_mode\n\n" if is_test_mode else f"Интервал между уроками: {message_interval} ч\n\n"

            # Формируем приветственное сообщение с информацией о курсе и тарифе
            first_name = message.from_user.first_name or message.from_user.username or "Пользователь"
            welcome_message = (
                f"*Добро пожаловать*, {escape_md(first_name)}\n\n"
                f"Вы успешно активировали *{escape_md(course_title)}*\n"
                f"Ваш тариф: *{escape_md(tariff_name)}*\n"
                f"{test_mode_text}"
                f"Желаем удачи в прохождении курса"
            )
            logger.info(f"3332 {welcome_message=}")
            await message.answer(welcome_message, reply_markup=keyboard, parse_mode=None)


        return # break here

    course_numeric_id, current_lesson, version_id = user_course_data
    logger.info(f"handle_homework START: course_numeric_id={course_numeric_id}, current_lesson={current_lesson}, version_id={version_id}")
    
    # Проверка что course_numeric_id правильный
    course_id_from_func = await get_course_id_str(course_numeric_id)
    logger.info(f"course_id_from_func={course_id_from_func} для course_numeric_id={course_numeric_id}")
    
    # ===== НОВАЯ ЗАЩИТА =====
    if current_lesson == 0:
        await message.answer(
            "Пожалуйста, дождитесь первого урока, чтобы сдать домашнее задание."
        )
        return
    # =======================
    course_id = course_id_from_func  # Используем правильное значение
    
    # ===== ЛОГИРОВАНИЕ ПЕРЕД ПРОВЕРКОЙ hw_status =====
    logger.info(f"ПЕРЕД ПРОВЕРКОЙ: user_id={user_id}, course_id={course_id}, lesson={current_lesson}, version_id={version_id}")
    
    # Проверка hw_status с детальным логом
    async with aiosqlite.connect(DB_FILE) as conn_hw:
        cursor_debug = await conn_hw.execute(
            "SELECT hw_status, hw_type FROM user_courses WHERE user_id = ? AND course_id = ? AND status = 'active'",
            (user_id, course_id)
        )
        debug_row = await cursor_debug.fetchone()
        logger.info(f"DEBUG hw_status ПЕРЕД проверкой: hw_status={debug_row[0] if debug_row else 'NULL'}, hw_type={debug_row[1] if debug_row and debug_row[1] else 'NULL'}")

    # ===== ПРОВЕРКА НА ПРОПУСК ДЗ =====
    skip_keywords = ["*пропускаю*", "пропускаю", "пропуск", "/skip"]
    message_text_lower = (message.text or "").lower().strip()
    if any(kw in message_text_lower for kw in skip_keywords) or message_text_lower in skip_keywords:
        await message.answer("⏭ Домашка пропущена и автоматически зачтена!", parse_mode=None)
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                UPDATE user_courses 
                SET hw_status = 'approved'
                WHERE user_id = ? AND course_id = ?
            """, (user_id, course_id))
            await conn.commit()
            await conn.execute(
                "DELETE FROM pending_admin_homework WHERE student_user_id = ? AND course_numeric_id = ? AND lesson_num = ?",
                (user_id, course_numeric_id, current_lesson)
            )
            await conn.commit()
        logger.info(f"ДЗ для user_id={user_id}, lesson={current_lesson} пропущено пользователем")
        return
    # ====================================

    # ===== УДАЛЕНИЕ СТАРЫХ PENDING ДЗ =====
    async with aiosqlite.connect(DB_FILE) as conn_del:
        await conn_del.execute(
            "DELETE FROM pending_admin_homework WHERE student_user_id = ? AND course_numeric_id = ? AND lesson_num = ?",
            (user_id, course_numeric_id, current_lesson)
        )
        await conn_del.commit()
    # =======================================

    # ===== ПРОВЕРКА: ЕСЛИ ДЗ УЖЕ ОДОБРЕНО ДЛЯ ЭТОГО УРОКА =====
    logger.info("=" * 80)
    logger.info(f"ЛОГ A1: ПРОВЕРКА hw_status НАЧАЛО")
    logger.info(f"ЛОГ A2: user_id={user_id}")
    logger.info(f"ЛОГ A3: course_id={course_id}")
    logger.info(f"ЛОГ A4: course_numeric_id={course_numeric_id}")
    logger.info(f"ЛОГ A5: current_lesson={current_lesson}")
    logger.info(f"ЛОГ A6: version_id={version_id}")

    async with aiosqlite.connect(DB_FILE) as conn_check:
        logger.info(f"ЛОГ A7: Подключение к БД для проверки hw_status")
        cursor_hw_status = await conn_check.execute(
            "SELECT hw_status, hw_type FROM user_courses WHERE user_id = ? AND course_id = ? AND status = 'active'",
            (user_id, course_id)
        )
        logger.info(f"ЛОГ A8: SQL запрос выполнен")
        hw_status_row = await cursor_hw_status.fetchone()
        logger.info(f"ЛОГ A9: hw_status_row={hw_status_row}")

        if hw_status_row:
            logger.info(f"ЛОГ A10: hw_status_row[0]={hw_status_row[0]}")
            logger.info(f"ЛОГ A11: hw_status_row[1]={hw_status_row[1]}")
            logger.info(f"ЛОГ A12: hw_status_row[0] == 'approved' → {hw_status_row[0] == 'approved'}")
        else:
            logger.info(f"ЛОГ A13: hw_status_row is None")

        logger.info(f"ЛОГ A14: Проверка условия hw_status_row and hw_status_row[0] == 'approved'")

        if hw_status_row and hw_status_row[0] == 'approved':
            logger.info(f"ЛОГ A15: УСЛОВИЕ ВЫПОЛНЕНО - hw_status='approved'")
            logger.info(f"ЛОГ A16: ДЗ для урока {current_lesson} уже одобрено")
            logger.info(f"ЛОГ A17: version_id={version_id}")
            logger.info(f"ЛОГ A18: Если version_id='v1' это нормально (самопроверка)")
            logger.info(f"ЛОГ A19: Если version_id='v2' или 'v3' это ПРОБЛЕМА!")
            logger.info(f"ЛОГ A20: ВОЗВРАТ ИЗ ФУНКЦИИ")
            logger.info("=" * 80)

            # 🔴 ЗАКОММЕНЧЕНО - ВАЖНО ДЛЯ ОТЛАДКИ!
            # ДЗ уже одобрено — игнорируем, просто говорим когда следующий урок
            # logger.info(f"ДЗ для урока {current_lesson} уже одобрено — игнорируем повторную отправку")

            # 🔴 РАЗКОММЕНТИРОВАТЬ ПОСЛЕ ОТЛАДКИ:
            # next_lesson_display_text_safe = escape_md(await get_next_lesson_time(user_id, course_id, current_lesson))
            # await message.answer(
            #     f"✅ Домашка уже засчитана!\n\n"
            #     f"🕒 Следующий урок: {next_lesson_display_text_safe}",
            #     parse_mode=None
            # )
            # return

            # 🔴 ВРЕМЕННО - ПРОДОЛЖАЕМ ОБРАБОТКУ ДЛЯ ОТЛАДКИ!
            logger.info(f"🔴 ПРОДОЛЖАЕМ ОБРАБОТКУ НЕСМОТРЯ НА hw_status='approved' ДЛЯ ОТЛАДКИ!")

        else:
            logger.info(f"ЛОГ A15: УСЛОВИЕ НЕ ВЫПОЛНЕНО - hw_status != 'approved'")
            logger.info(f"ЛОГ A16: Продолжаем обработку ДЗ")

    logger.info("=" * 80)

    # Если тариф v1 → самопроверка
    if version_id == 'v1':
        try:
            # Экранируем сообщение с помощью escape_md
            await message.answer(
                escape_md("✅ Домашка принята для самопроверки и будет одобрена автоматически!"),
                parse_mode=None
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

                if  0 < total_lessons <= current_lesson:
                    logger.info(
                        f"Курс {course_id} (v1) завершен для {user_id} после самоодобрения ДЗ урока {current_lesson}.")
                    course_title_safe = escape_md(await get_course_title(course_id))
                    message_text_completion = (
                        f"🎉 Поздравляем с успешным завершением курса «{course_title_safe}»! 🎉\n\n"
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
                        parse_mode=None
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
        except Exception as e4669:
            logger.error(f"Ошибка при отправке сообщения об авто-аппруве: {e4669}", exc_info=True)
        return

    # Получаем ожидаемый hw_type для текущего урока из БД
    expected_hw_type = None
    async with aiosqlite.connect(DB_FILE) as conn:
        # Ищем запись о ДЗ для текущего урока пользователя
        # Предполагаем, что is_homework=True и hw_type установлены для соответствующей части урока
        cursor_hw_info = await conn.execute(
            """SELECT hw_type 
               FROM group_messages 
               WHERE course_id = ? AND lesson_num = ? AND is_homework = 1 
               ORDER BY id DESC LIMIT 1""",  # Берем последнюю часть ДЗ, если их несколько
            (course_id, current_lesson)
        )
        hw_info_row = await cursor_hw_info.fetchone()
        if hw_info_row and hw_info_row[0]:
            expected_hw_type = hw_info_row[0].strip().lower()
            logger.info(f"Для урока {current_lesson} курса {course_id} ожидается ДЗ типа: '{expected_hw_type}'")
        else:
            logger.warning(
                f"Не найден ожидаемый тип ДЗ (hw_type) для урока {current_lesson} курса {course_id}. Разрешаю любой тип.")
            expected_hw_type = "any"  # Если тип не указан в базе, считаем, что подходит любой

    # Определяем тип присланного ДЗ
    submitted_content_type = message.content_type.lower()  # 'text', 'photo', 'document', etc.

    # Проверка типа ДЗ - поддерживаем множественные типы через "|" (например: "text|photo")
    is_type_allowed = False
    
    if expected_hw_type == "any":
        is_type_allowed = True
    elif "|" in expected_hw_type:
        # Множественные типы: "text|photo|document"
        allowed_types = [t.strip().lower() for t in expected_hw_type.split("|")]
        is_type_allowed = submitted_content_type in allowed_types
    else:
        # Один тип - прямое совпадение
        is_type_allowed = submitted_content_type == expected_hw_type

    # Специальные случаи
    if not is_type_allowed:
        # Если ожидается текст, но прислали медиа с подписью - это может быть ок
        if expected_hw_type == "text" and submitted_content_type in ["photo", "video", "document", "animation"] and message.caption:
            is_type_allowed = True  # Медиа с подписью считаем как текст
    
    if not is_type_allowed:
        logger.warning(f"Несоответствие типа ДЗ для урока {current_lesson} курса {course_id}. "
                       f"Ожидался: '{expected_hw_type}', получен: '{submitted_content_type}'.")
        await message.reply(
            escape_md(
                f"Вы прислали ДЗ не того типа. Для этого урока ожидается: **{expected_hw_type.replace('|', ' или ').capitalize()}**. "
                f"Пожалуйста, отправьте ДЗ корректного типа"),
            parse_mode=None
        )
        return  # Прерываем обработку ДЗ



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
    logger.info(f"admin_message_content: {admin_message_content} {file_id=}")
    
    admin_message_text = (
        f"📝 Новое ДЗ {homework_type}\n"
        f"👤 Пользователь: {escape_md(user_display_name)} ID: {user_id}\n"
        f"📚 Курс: {escape_md(display_course_title)}\n"
        f"⚡ Тариф: {escape_md(version_id)}\n"
        f"📖 Урок: {current_lesson}\n"
        f"🤖 Через {HW_TIMEOUT_SECONDS} сек уйдет на AI-проверку\n"
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
                parse_mode=None
            )
        elif message.video:
            sent_admin_message = await bot.send_video(
                chat_id=ADMIN_GROUP_ID,
                video=message.video.file_id,
                caption=caption_with_description,
                reply_markup=admin_keyboard,
                parse_mode=None
            )
        elif message.document:
            sent_admin_message = await bot.send_document(
                chat_id=ADMIN_GROUP_ID,
                document=message.document.file_id,
                caption=caption_with_description,
                reply_markup=admin_keyboard,
                parse_mode=None
            )
        elif message.audio:  # Новый тип
            sent_admin_message = await bot.send_audio(
                chat_id=ADMIN_GROUP_ID,
                audio=message.audio.file_id,
                caption=caption_with_description,
                reply_markup=admin_keyboard,
                parse_mode=None
            )
        elif message.voice:  # Новый тип
            sent_admin_message = await bot.send_voice(
                chat_id=ADMIN_GROUP_ID,
                voice=message.voice.file_id,
                caption=caption_with_description,
                # Для voice caption обычно не отображается клиентами, но API его принимает
                reply_markup=admin_keyboard,
                parse_mode=None
            )
        elif message.animation:  # Новый тип (GIF)
            sent_admin_message = await bot.send_animation(
                chat_id=ADMIN_GROUP_ID,
                animation=message.animation.file_id,
                caption=caption_with_description,
                reply_markup=admin_keyboard,
                parse_mode=None
            )
        elif message.text:  # Если это текстовая домашка
            # text здесь - это message.text.strip()
            final_admin_text = admin_message_text + f"\n✏️ Текст ДЗ:\n{escape_md(text)}"  # text уже взят из message.text.strip()
            sent_admin_message = await bot.send_message(
                ADMIN_GROUP_ID,
                final_admin_text,
                reply_markup=admin_keyboard,
                parse_mode=None
            )
        else:
            logger.warning(f"Получен неподдерживаемый тип контента для ДЗ от user {user_id}: {message.content_type}")
            await message.answer(escape_md("Неподдерживаемый тип файла для домашнего задания."),
                                 parse_mode=None)
            return  # Выходим, если тип не поддерживается
        logger.info(f"ДЗ отправлено админам: msg_id={sent_admin_message.message_id}")

        if sent_admin_message:
            try:
                async with aiosqlite.connect(DB_FILE) as conn:
                    await conn.execute("""
                        INSERT INTO pending_admin_homework
                        (admin_message_id, admin_chat_id, student_user_id, course_numeric_id, lesson_num, student_message_id)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        sent_admin_message.message_id,
                        ADMIN_GROUP_ID,
                        user_id,
                        course_numeric_id,
                        current_lesson,
                        message.message_id
                    ))
                    await conn.commit()
                    logger.info(
                        f"ДЗ для студента {user_id}, урок {current_lesson} зарегистрировано в pending_admin_homework с admin_message_id {sent_admin_message.message_id}")
            except Exception as e_pending_hw:
                logger.error(f"Не удалось сохранить информацию о ДЗ в pending_admin_homework: {e_pending_hw}")

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

        hw_hint = ""
        try:
            async with aiosqlite.connect(DB_FILE) as conn_time:
                cursor_next = await conn_time.execute("""
                    SELECT last_sent_time FROM user_courses 
                    WHERE user_id = ? AND course_id = ? AND status = 'active'
                """, (user_id, course_id))
                next_row = await cursor_next.fetchone()
                if next_row and next_row[0]:
                    last_sent_str = next_row[0]
                    try:
                        last_sent = datetime.strptime(last_sent_str, '%Y-%m-%d %H:%M:%S')
                        last_sent = pytz.utc.localize(last_sent)
                        interval_hours = float(settings.get("message_interval", 24))
                        next_lesson_time = last_sent + timedelta(hours=interval_hours)
                        if datetime.now(pytz.utc) > next_lesson_time:
                            hw_hint = "\n\n💡 Чтобы пропустить ДЗ, напишите *пропускаю* или /skip"
                    except:
                        pass
        except:
            pass

        await message.answer(
            f"✅ {homework_type} на проверке!{hw_hint}",
            parse_mode=None
        )

    except Exception as e4826:
        logger.error(f"Ошибка отправки домашки админам: {e4826}", exc_info=True)
        await message.answer(escape_md("Произошла ошибка при отправке вашего ДЗ. Попробуйте позже."),
                             parse_mode=None)

# единое главное меню для пользователя. Теперь с левелами
async def send_main_menu(user_id: int, course_id: str, lesson_num: int, version_id: str,
                         homework_pending: bool = False, hw_type: str = 'none', user_course_level_for_menu: int = 1):
    """Отправляет главное меню активного курса, отображая прогресс и опции."""
    logger.info(
        f"send_main_menu START: user_id={user_id}, course_id='{course_id}', lesson_num={lesson_num}, version_id='{version_id}', "
        f"homework_pending={homework_pending}, hw_type='{hw_type}', level={user_course_level_for_menu}"
    )
    
    try:
        course_numeric_id = await get_course_id_int(course_id)
        if course_numeric_id == 0 and course_id:  # Если get_course_id_int вернул 0, но course_id был
            logger.error(
                f"Критическая ошибка: не удалось получить числовой ID для курса '{course_id}' в send_main_menu.")
            await bot.send_message(user_id, escape_md(
                "Произошла ошибка при загрузке меню курса (ID не найден). Обратитесь в поддержку."),
                                   parse_mode=None)
            return

        # Экранируем все динамические строки, которые пойдут в сообщение
        course_title_safe = escape_md(await get_course_title(course_id))
        tariff_name_safe = escape_md(settings.get("tariff_names", {}).get(version_id, "Базовый"))
        interval_value = settings.get("message_interval", 24)
        interval_safe_str = escape_md(str(interval_value)) + " ч"
        next_lesson_display_text_safe = escape_md(await get_next_lesson_time(user_id, course_id, lesson_num))


        # добавочка 21 мая
        # Проверяем, есть ли в принципе ДЗ для текущего lesson_num на этом уровне
        lesson_has_homework_defined = False
        expected_hw_type_for_this_lesson = "не определен"
        async with aiosqlite.connect(DB_FILE) as conn_hw_check:
            cursor_hw_def = await conn_hw_check.execute(
                """SELECT hw_type 
                   FROM group_messages 
                   WHERE course_id = ? AND lesson_num = ? AND level = ? AND is_homework = 1
                   LIMIT 1""",
                (course_id, lesson_num, user_course_level_for_menu)
            )
            hw_def_row = await cursor_hw_def.fetchone()
            if hw_def_row:
                lesson_has_homework_defined = True
                if hw_def_row[0] and isinstance(hw_def_row[0], str) and hw_def_row[0].strip().lower() != 'none':
                    expected_hw_type_for_this_lesson = escape_md(hw_def_row[0])
                else:
                    expected_hw_type_for_this_lesson = "любое"

        domashka_text = escape_md("не требуется")  # Экранируем сразу
        if lesson_has_homework_defined:  # Если для этого урока в принципе есть ДЗ
            if homework_pending:  # Если текущий статус в user_courses - pending или rejected
                domashka_text = f"ожидается ({expected_hw_type_for_this_lesson})"
            else:  # ДЗ для этого урока было, и сейчас оно принято (hw_status = 'approved' или 'none'/'not_required' и т.п.)
                # Или это урок 0, для которого ДЗ не бывает pending.
                if lesson_num == 0:  # Для урока-описания
                    domashka_text = escape_md("не предусмотрена")
                else:
                    domashka_text = f"принята (тип: {expected_hw_type_for_this_lesson})"

        # Узнаем общее количество уроков на текущем уровне
        total_lessons_on_level = 0
        async with aiosqlite.connect(DB_FILE) as conn_total:
            cursor_total = await conn_total.execute(
                "SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ? AND lesson_num > 0 AND level = ?",
                (course_id, user_course_level_for_menu)
            )
            row_total = await cursor_total.fetchone()
            if row_total and row_total[0] is not None:
                total_lessons_on_level = row_total[0]

        # --- Формирование основного текста сообщения ---
        is_last_lesson_on_level_sent = (lesson_num >= total_lessons_on_level and total_lessons_on_level > 0)
        is_level_completed_no_hw_pending = is_last_lesson_on_level_sent and not homework_pending
        
        # Проверяем тест-режим для админов
        is_test_mode = user_id in ADMIN_TEST_MODE
        test_mode_badge = " ⚡[ТЕСТ]" if is_test_mode else ""

        base_text_lines = [
            f"🎓 Курс: {course_title_safe}{test_mode_badge}",
            f"🔑 Тариф: {tariff_name_safe}",
            f"📖 Урок (отправлен): {lesson_num} из {total_lessons_on_level}",
            f"🥇 Уровень: {user_course_level_for_menu}",
            f"⏳ Интервал: {TEST_MODE_INTERVAL_MINUTES} мин ⚡" if is_test_mode else f"⏳ Интервал: {interval_safe_str}",
            f"📝 Домашка к уроку {lesson_num}: {domashka_text}"
        ]

        if is_level_completed_no_hw_pending:
            async with aiosqlite.connect(DB_FILE) as conn_next_level:
                cursor_next_level = await conn_next_level.execute(
                    "SELECT 1 FROM group_messages WHERE course_id = ? AND level = ? AND lesson_num > 0 LIMIT 1",
                    (course_id, user_course_level_for_menu + 1)
                )
                hasNextLevel = await cursor_next_level.fetchone()
            if hasNextLevel:
                base_text_lines.append(
                    f"🎉 Текущий уровень завершен! Вы можете перейти на следующий уровень через меню 'Все курсы' (кнопка \"Повторить/Обновить\" для этого курса).")
            else:
                base_text_lines.append(f"🎉 Поздравляем, курс полностью завершен!")
        elif lesson_num > 0 or (lesson_num == 0 and total_lessons_on_level > 0):
            base_text_lines.append(f"🕒 Следующий урок: {next_lesson_display_text_safe}")

        final_text = "\n".join(base_text_lines)
        # Список уроков lessons_overview_lines БОЛЬШЕ НЕ ДОБАВЛЯЕТСЯ сюда

        # --- Формирование клавиатуры (остается почти таким же) ---
        builder = InlineKeyboardBuilder()
        if lesson_num > 0:  # Кнопка повтора урока, если это не "урок 0" (описание)
            builder.button(
                text=f"🔁 Урок {lesson_num} (повтор)",  # Это ваш "Повторить последний урок активного курса"
                callback_data=CourseCallback(action="menu_cur", course_id=course_numeric_id, lesson_num=lesson_num).pack()
            )

        if total_lessons_on_level > 0:  # Кнопка "Содержание/Повтор" остается
            builder.button(
                text="📚 Содержание/Повтор",  # Изменено название для ясности
                callback_data=SelectLessonForRepeatCallback(course_numeric_id=course_numeric_id).pack()
            )
        # ... (остальные кнопки Прогресс, Все курсы и т.д. как были) ...
        builder.row()

        builder.button(text="📈 Прогресс", callback_data="menu_progress")
        builder.button(text="🗂️ Все курсы", callback_data="select_other_course")
        builder.row()
        if course_numeric_id > 0:  # Только если это не "ошибка, курс не найден"
            builder.button(
                text="⏹️ Остановить курс",
                callback_data=MainMenuAction(action="stop_course", course_id_numeric=course_numeric_id).pack()
            )

            # --- НОВАЯ КНОПКА ---
        if course_numeric_id > 0:  # Если это действительный курс
            builder.button(
                text="💎 Сменить тариф",
                callback_data=ChangeTariffCallback(course_id_str=course_id).pack()  # course_id здесь строковый
            )
        # --- КОНЕЦ НОВОЙ КНОПКИ ---
        builder.row()
        builder.button(text="🔙 /start - В главное меню", callback_data="admin_menu")
        builder.button(text="📞 Поддержка", callback_data="menu_support")
        builder.adjust(2)

        # --- Удаление предыдущего меню ---
        previous_menu_id = None
        async with aiosqlite.connect(DB_FILE) as conn_get_prev_menu:
            # ... (получение previous_menu_id)
            cursor_prev_menu = await conn_get_prev_menu.execute(
                "SELECT last_menu_message_id FROM user_courses WHERE user_id = ? AND course_id = ? AND status = 'active'",
                (user_id, course_id)
            )
            row_prev_menu = await cursor_prev_menu.fetchone()
            if row_prev_menu and row_prev_menu[0]:
                previous_menu_id = row_prev_menu[0]

        if previous_menu_id:
            try:
                await bot.delete_message(chat_id=user_id, message_id=previous_menu_id)
            except Exception:  # Мягко обрабатываем ошибку удаления
                pass

        sent_message = await bot.send_message(
            user_id,
            final_text,
            reply_markup=builder.as_markup(),
            parse_mode=None
        )

        async with aiosqlite.connect(DB_FILE) as conn_update_menu:
            await conn_update_menu.execute(
                "UPDATE user_courses SET last_menu_message_id = ? WHERE user_id = ? AND course_id = ? AND status = 'active'",
                (sent_message.message_id, user_id, course_id)
            )
            await conn_update_menu.commit()
            logger.info(f"send_main_menu END: Успешно отправлено меню для user_id={user_id}, course_id='{course_id}', menu_message_id={sent_message.message_id}")
    except Exception as e_sm_outer:  # Уникальный идентификатор ошибки
        logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА в send_main_menu для user {user_id}, course {course_id}: {e_sm_outer}",
                     exc_info=True)
        # ... (fallback сообщение) ...
        try:
            # Попытка отправить простое сообщение об ошибке, если основное меню не удалось
            await bot.send_message(user_id, escape_md(
                "Возникла серьезная ошибка при отображении меню курса. Мы уже работаем над этим. Попробуйте команду /start или обратитесь в поддержку."),
                                   parse_mode=None)
        except Exception as e_fallback:  # Уникальный идентификатор ошибки
            logger.error(
                f"Не удалось отправить даже fallback сообщение об ошибке меню пользователю {user_id}: {e_fallback}")

# Обработчик последний - чтобы не мешал другим обработчикам работать. Порядок имеет значение
@dp.message(F.text, ~F.text.startswith('/'))  # Игнорируем команды
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
            return await message.answer("Неверное кодовое слово. ��опробуйте еще раз или свяжитесь с поддержкой.", parse_mode=None)

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
                    details=f"Активирова�� кодом: {escape_md(message.text.strip())}"
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

    except Exception as e4980:
        logger.error(f"Общая ошибка в process_message: {e4980}", exc_info=True)
        await message.answer("Произошла общая ошибка. Пожалуйста, попробуйте позже.", parse_mode=None)



#  Обработчик входящего контента от пользователя
@dp.message(F.photo | F.video | F.document | (F.text & ~F.text.startswith('/')))
async def handle_user_content(message: types.Message, state: FSMContext):
    """Обработчик пользовательского контента для ДЗ"""
    # Проверяем, есть ли активное FSM состояние
    current_state = await state.get_state()
    if current_state is not None:
        logger.info(f"handle_user_content: пропускаем, есть активное состояние FSM: {current_state}")
        # Используем continue_propagation чтобы пропустить другим обработчикам
        from aiogram.dispatcher.event.bases import UNHANDLED
        return UNHANDLED
    
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

    except Exception as e5016:
        logger.error(f"Ошибка при обработке контента: {e5016}")
        await message.answer("Произошла ошибка при обработке вашего сообщения.", parse_mode=None)

#=======================Конец обработчиков текстовых сообщений=========================================

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    """Обработчик для фотографий."""
    logger.info(f"88 handle_photo  ")
    try:
        await message.answer("Фотография получена!", parse_mode=None)
    except Exception as e5028:
        logger.error(f"Ошибка при обработке фотографии: {e5028}")

@dp.message(F.video)
async def handle_video(message: types.Message):
    """Обработчик для видео."""
    logger.info(f"89 handle_video  ")
    try:
        await message.answer("Видео получено!", parse_mode=None)
    except Exception as e5037:
        logger.error(f"Ошибка при обработке видео: {e5037}")

@dp.message(F.document)
async def handle_document(message: types.Message):
    """Обработчик для документов."""
    logger.info(f"90 handle_document  ")
    try:
        await message.answer("Документ получен!", parse_mode=None)
    except Exception as e5046:
        logger.error(f"Ошибка при обработке документа: {e5046}")


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

    # Проверяем режим работы
    webhook_mode_env = os.getenv("WEBHOOK_MODE", "true").lower()
    use_webhook = webhook_mode_env not in ["false", "0", "no", "off"]

    if use_webhook:
        # final_webhook_path = f"{WEBHOOK_PATH_CONF.rstrip('/')}/{BOT_TOKEN_CONF}"
        # webhook_url = f"{WEBHOOK_HOST_CONF.rstrip('/')}{final_webhook_path}"

        # WEBHOOK_SECRET_PATH_CONF = os.getenv("WEBHOOK_SECRET_PATH")  # Путь для секретного ключа вебхука 01-07
        # WEBHOOK_SECRET_TOKEN_CONF = os.getenv("WEBHOOK_SECRET_TOKEN")

        webhook_url = f"{WEBHOOK_HOST_CONF.rstrip('/')}/{WEBHOOK_SECRET_PATH_CONF.strip('/')}"

        await bot.set_webhook(
            webhook_url,
            drop_pending_updates=True,
            secret_token=WEBHOOK_SECRET_TOKEN_CONF  # <--- ДОБАВИТЬ ЭТОТ АРГУМЕНТ
        )
        logger.info(f"Webhook set to: {webhook_url}")
    else:
        logger.info("Skipping webhook setup (WEBHOOK_MODE=false)")



    logger.info("Запуск фоновых задач для пользователей (таймеры)...")
    
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT user_id FROM users")
        users_rows = await cursor.fetchall()
        for user_row in users_rows:
            user_id = user_row[0]
            if user_id not in lesson_check_tasks or lesson_check_tasks[user_id].done():
                asyncio.create_task(start_lesson_schedule_task(user_id))
            else:
                logger.info(f"Task for user {user_id} already running or scheduled.")
    
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT admin_message_id, admin_chat_id FROM pending_admin_homework")
        pending_messages = await cursor.fetchall()
        
        if pending_messages:
            logger.info(f"Очистка {len(pending_messages)} pending ДЗ в админ-группе...")
            for admin_msg_id, admin_chat_id in pending_messages:
                try:
                    await bot.edit_message_reply_markup(
                        chat_id=admin_chat_id,
                        message_id=admin_msg_id,
                        reply_markup=None
                    )
                except Exception as e_clean:
                    logger.debug(f"Не удалось убрать кнопки с сообщения {admin_msg_id}: {e_clean}")
        
        cursor_count = await conn.execute("SELECT COUNT(*) FROM pending_admin_homework")
        count = (await cursor_count.fetchone())[0]
        if count > 0:
            await conn.execute("DELETE FROM pending_admin_homework")
            await conn.commit()
            logger.info(f"Очищено {count} старых ДЗ из pending_admin_homework при старте")
    
    asyncio.create_task(check_pending_homework_timeout())
    logger.info("Фоновые задачи запущены.")

    await send_startup_message(bot, ADMIN_GROUP_ID)  # <--- ВОТ ВЫЗОВ


async def on_shutdown():
    global bot
    logger.warning("Shutting down..")

    # Проверяем режим работы
    webhook_mode_env = os.getenv("WEBHOOK_MODE", "true").lower()
    use_webhook = webhook_mode_env not in ["false", "0", "no", "off"]

    if use_webhook:
        await bot.delete_webhook()
        logger.info("Webhook deleted.")
    else:
        logger.info("Skipping webhook deletion (WEBHOOK_MODE=false)")

    logger.info("Cancelling background tasks...")
    if 'lesson_check_tasks' in globals() and lesson_check_tasks: # Проверка на существование
        active_tasks = [task for task in lesson_check_tasks.values() if task and not task.done()]
        if active_tasks:
            for task in active_tasks:
                task.cancel()
            # Ожидание завершения задач
            results = await asyncio.gather(*active_tasks, return_exceptions=True)
            # Логирование результатов отмены (опц��онально)
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
    logger.info("=" * 60)
    logger.info("🚀 Запуск AntBot v4.0")
    logger.info("=" * 60)

    load_dotenv()
    logger.info("✅ Переменные окружения загружены из .env")

    # Загрузка переменных с именами из вашего .env
    BOT_TOKEN_CONF = os.getenv("BOT_TOKEN")
    admin_ids_str = os.getenv("ADMIN_IDS")
    WEBHOOK_HOST_CONF = os.getenv("WEBHOOK_HOST") #WEBHOOK_HOST = 'https://antbot.alwaysdata.net/'
    webapp_port_str = os.getenv("WEB_SERVER_PORT", "8080")  # Используем WEB_SERVER_PORT из .env
    WEBAPP_HOST_CONF = os.getenv("WEBAPP_HOST", "::") # '::' как дефолт, если не указано
    WEBHOOK_PATH_CONF = os.getenv("WEBHOOK_PATH", "/bot/") # '/bot/' как дефолт

    # Валидация обязательных перем��нных
    if not BOT_TOKEN_CONF:
        logger.critical("❌ BOT_TOKEN не най��ен. Завершение.")
        raise ValueError("BOT_TOKEN не найден.")
    logger.info("✅ BOT_TOKEN найден")
    
    # Проверяем режим работы: webhook или polling
    # WEBHOOK_MODE=true/false (по умолчанию true - webhook)
    webhook_mode_env = os.getenv("WEBHOOK_MODE", "true").lower()
    if webhook_mode_env in ["false", "0", "no", "off"]:
        use_webhook = False
        logger.info("📡 Режим работы: POLLING (WEBHOOK_MODE=false)")
    elif os.getenv("WEBHOOK_HOST"):
        use_webhook = True
        logger.info(f"📡 Режим работы: WEBHOOK")
        logger.info(f"   Host: {WEBHOOK_HOST_CONF}")
        logger.info(f"   По��т сервера: {webapp_port_str}")
        logger.info(f"   Хост приложения: {WEBAPP_HOST_CONF}")
    else:
        # По умолчанию webhook, но без настроенного WEBHOOK_HOST - ошибка
        use_webhook = True
        logger.warning("⚠️ WEBHOOK_MODE не указан или =true, но WEBHOOK_HOST не настроен!")
        logger.warning("   Бот запустится в режиме webhook, но работать не будет.")
        logger.warning("   Для включения polling: WEBHOOK_MODE=false")
        logger.info(f"📡 Режим работы: WEBHOOK (ожидается настройка)")
    
    # Валидация webhook переменных (только для webhook режима)
    if use_webhook:
        if not WEBHOOK_SECRET_PATH_CONF:
            raise ValueError("WEBHOOK_SECRET_PATH не найден.")
        if not WEBHOOK_SECRET_TOKEN_CONF:
            raise ValueError("WEBHOOK_SECRET_TOKEN не найден.")
        logger.info("✅ Webhook секреты настроены")

    # Парсинг и установка значений
    if admin_ids_str:
        try:
            ADMIN_IDS_CONF = [int(admin_id.strip()) for admin_id in admin_ids_str.split(',')]
            logger.info(f"👤 Администраторы: {len(ADMIN_IDS_CONF)} ID загружено")
        except ValueError:
            logger.warning(f"⚠️ Некорректный формат ADMIN_IDS: '{admin_ids_str}'. Оставляем пустым.")
            ADMIN_IDS_CONF = []
    else:
        ADMIN_IDS_CONF = []
        logger.warning("⚠️ ADMIN_IDS не указаны. Управление ботом ограничено.")

    try:
        WEBAPP_PORT_CONF = int(webapp_port_str) if webapp_port_str else 8080  # Дефолт 8080
        logger.info(f"🔌 Порт приложения: {WEBAPP_PORT_CONF}")
    except ValueError:
        logger.warning(f"⚠️ Некорректный формат WEB_SERVER_PORT: '{webapp_port_str}'. Устанавливаем 8080.")
        WEBAPP_PORT_CONF = 8080


    bot = Bot(
        token=BOT_TOKEN_CONF,
        #default=DefaultBotProperties(parse_mode=None)
        default=DefaultBotProperties(parse_mode=None)
    )
    # dp = Dispatcher() # <--- УБЕРИТЕ ЭТУ СТРОКУ

    # Регистрация хэ��длеров (убедитесь, что они импортированы или определены)
    # from .handlers import register_all_my_handlers
    # register_all_my_handlers(dp)

    await init_db()
    
    settings = await load_settings()
    if settings and "groups" in settings: # Более безопасная проверка
        COURSE_GROUPS = list(map(int, settings.get("groups", {}).keys()))
        logger.info(f"📋 Группы курсов: {len(settings.get('groups', {}))} групп загружено")
        for group_id, group_name in settings.get('groups', {}).items():
            logger.info(f"   - {group_name}: {group_id}")
        
        # Очистка "осиротевших" курсов (которых нет в settings.json)
        deleted_count, removed_courses = await cleanup_orphaned_courses(settings)
        if deleted_count > 0:
            logger.warning(f"🧹 У��алено {deleted_count} активных записей для недоступных курсов: {removed_courses}")
            # Уведомляем админов
            try:
                admin_notification = (
                    f"⚠️ *Очистка базы данных*\n\n"
                    f"Удалено {deleted_count} активных записей студентов\n"
                    f"для недоступных курсов (отсутствуют в settings.json):\n"
                )
                for course in removed_courses:
                    admin_notification += f"  • `{escape_md(course)}`\n"
                admin_notification += "\nСтуденты отписаны от этих курсов."
                await bot.send_message(ADMIN_GROUP_ID, admin_notification, parse_mode=None)
                logger.info("📨 Админы уведомлены об очистке осиротевших курсов")
            except Exception as e:
                logger.error(f"❌ Ошибка при отправке уведомления админам: {e}")
    else:
        COURSE_GROUPS = []
        logger.warning("⚠️ Настройки 'groups' не загружены или отсутствуют, COURSE_GROUPS пуст.")
    await import_settings_to_db()

    # Передаем актуальные значения в лямбду для on_startup
    # Имена аргументов в лямбде могут быть любыми, главное порядок и что они передаются в on_startup
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    if use_webhook:
        app = web.Application()

        # Формируем финальный путь для регистрации в aiohttp
        final_webhook_path_for_aiohttp = f"/{WEBHOOK_SECRET_PATH_CONF.strip('/')}" #01-07

        webhook_requests_handler = SimpleRequestHandler(
            dispatcher=dp,
            bot=bot,
            secret_token=WEBHOOK_SECRET_TOKEN_CONF
        )
        webhook_requests_handler.register(app, path=final_webhook_path_for_aiohttp)

        # Настройка эндпоинтов мониторинга
        await setup_metrics_endpoints(
            app=app,
            db_file=DB_FILE,
            bot_token=BOT_TOKEN_CONF,
            admin_group_id=ADMIN_GROUP_ID,
            n8n_domain=N8N_DOMAIN,
            webhook_url=f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}"
        )

        logger.info(f"📨 Обработчики сообщений: {len(dp.message.handlers)}")
        logger.info(f"📨 Обработчики колбэков: {len(dp.callback_query.handlers)}")

        setup_application(app, dp, bot=bot)
        
        # Путь для результатов проверки ДЗ
        app.router.add_post(f"{WEBHOOK_PATH_CONF.rstrip('/')}/n8n_hw_result", handle_n8n_hw_approval)
        app.router.add_post(f"{WEBHOOK_PATH_CONF.rstrip('/')}/n8n_hw_processing_error", handle_n8n_hw_error)
        app.router.add_post(f"{WEBHOOK_PATH_CONF.rstrip('/')}/n8n_expert_answer/{{user_id}}/{{message_id}}",handle_n8n_expert_answer)

        logger.info(f"🔗 Дополнительные маршруты n8n:")
        logger.info(f"   - /n8n_hw_result")
        logger.info(f"   - /n8n_hw_processing_error")
        logger.info(f"   - /n8n_expert_answer_callback")
        
        # Запускаем веб-сервер (без access-логов)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, host=WEBAPP_HOST_CONF, port=WEBAPP_PORT_CONF)
        await site.start()
        
        actual_host_log = WEBAPP_HOST_CONF if WEBAPP_HOST_CONF != "::" else "0.0.0.0"
        logger.info("=" * 60)
        logger.info("✅ БОТ УСПЕШНО ЗАПУЩЕН")
        logger.info("=" * 60)
        logger.info(f"🌐 Сервер: {actual_host_log}:{WEBAPP_PORT_CONF}")
        logger.info(f"🔗 Webhook path: {final_webhook_path_for_aiohttp}")
        logger.info(f"🔑 Secret token: {'установлен' if WEBHOOK_SECRET_TOKEN_CONF else 'отсутствует'}")
        logger.info("=" * 60)
        
        # Ожидаем прерывания
        try:
            while True:
                await asyncio.sleep(3600)  # спим 1 час
        except asyncio.CancelledError:
            logger.info("Webhook server stopped")
    else:
        # Polling режим - запускаем минимальный HTTP сервер для health checks
        app = web.Application()
        app.router.add_get('/health/live', liveness_probe)
        app.router.add_get('/health', liveness_probe)
        
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, host="0.0.0.0", port=WEBAPP_PORT_CONF)
        await site.start()
        
        logger.info("=" * 60)
        logger.info("✅ БОТ УСПЕШНО ЗАПУЩЕН В РЕЖИМЕ POLLING")
        logger.info(f"🌐 Health endpoint: http://0.0.0.0:{WEBAPP_PORT_CONF}/health/live")
        logger.info("=" * 60)
        
        # Запускаем polling в фоне
        polling_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
        
        # Ждем пока polling работает
        try:
            await polling_task
        except asyncio.CancelledError:
            logger.info("Polling stopped")
        finally:
            await runner.cleanup()

# ==========================================
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
