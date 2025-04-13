import asyncio, logging, json, random, string, os, re, aiosqlite, datetime, shutil, sys
import functools
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, CommandStart, BaseFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup,
                           KeyboardButton, Message, CallbackQuery, ChatFullInfo, FSInputFile)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

# Фикс для консоли Windows
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')


# Загрузка переменных из .env
load_dotenv()

MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 3


def setup_logging():
    """Настройка логирования с ротацией и UTF-8"""
    log_file = 'bot.log'
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(lineno)d - %(message)s  %(levelname)s',
        datefmt='%H:%M:%S',
        handlers=[
            RotatingFileHandler(
                log_file,
                maxBytes=MAX_LOG_SIZE,
                backupCount=LOG_BACKUP_COUNT,
                encoding='utf-8'  # Фикс кодировки для Windows
            ),
            logging.StreamHandler()
        ]
    )

logger = logging.getLogger(__name__)  # Создание логгера для текущего модуля

# == Константы и конфиг ==
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения.")
logger.info(f"BOT_TOKEN: {BOT_TOKEN}")

# Получение ADMIN_IDS с проверкой
admin_ids_raw = os.getenv("ADMIN_IDS", "")
try:
    ADMIN_IDS = [int(id.strip()) for id in admin_ids_raw.split(",") if id.strip().isdigit()]
except ValueError:
    raise ValueError("ADMIN_IDS содержит некорректные данные. Убедитесь, что это список чисел, разделенных запятыми.")

ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID', 0))


SETTINGS_FILE = "settings.json"

DB_FILE = "bot.db"
MAX_LESSONS_PER_PAGE = 7  # пагинация для view_completed_course
DEFAULT_COUNT_MESSAGES = 7  # макс количество сообщений при выводе курсов

# Initialize bot and dispatcher
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# Callback data classes
class CourseCallback(CallbackData, prefix="course"):
    action: str
    course_id: str
    lesson_num: int = 0


# декоратор для обработки ошибок в БД
def db_exception_handler(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except aiosqlite.Error as e:
            logger.error(f"Database error in {func.__name__}: {e}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла ошибка при работе с базой данных.")
                    break
            return None
        except TelegramAPIError as e:
            logger.error(f"Telegram API error in {func.__name__}: {e}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла ошибка в работе Telegram API.")
                    break
            return None
        except Exception as e:
            logger.error(f"Unexpected error in ... {func.__name__}: {e}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла неизвестная ошибка.")
                    break
            return None

    return wrapper


async def populate_course_versions(settings):
    """Заполняет таблицу course_versions данными из settings.json."""
    logger.info("Заполнение таблицы course_versions...")
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
                    logger.info(f"Добавлена запись в course_versions: {course_id=}, {version_id=}, {version_title=}, {version_price=}")
                else:
                     logger.info(f"Запись уже существует в course_versions: {course_id=}, {version_id=}")
            await conn.commit()
        logger.info("Таблица course_versions успешно заполнена.")
    except Exception as e:
        logger.error(f"Ошибка при заполнении таблицы course_versions: {e}")


def load_settings():
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
last_stats_sent = None


# Создаем кэш для хранения информации о курсе и тарифе
course_info_cache = {}

async def get_course_info(user_id: int, course_id: str, version_id: str) -> dict:
    """Получает информацию о курсе и тарифе из базы данных или из кэша."""
    cache_key = f"{course_id}:{version_id}"

    if cache_key in course_info_cache:
        logger.info(f"Используем кэш для {cache_key}")
        return course_info_cache[cache_key]

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT c.title, cv.title 
            FROM courses c
            JOIN course_versions cv ON c.course_id = cv.course_id
            WHERE c.course_id = ? AND cv.version_id = ?
        """, (course_id, version_id))
        result = await cursor.fetchone()

        if result:
            course_title, tariff_title = result
            course_info = {
                "course_title": course_title,
                "tariff_title": tariff_title
            }
            course_info_cache[cache_key] = course_info
            logger.info(f"Получили данные из базы для {cache_key} и сохранили в кэш")
            return course_info
        else:
            logger.error(f"Не удалось получить данные для {cache_key}")
            return {}

async def get_main_menu_text(user_id: int, course_id: str, lesson_num: int, version_id: str) -> str:
    """Возвращает основной текст для меню, используя кэшированные данные."""
    course_info = await get_course_info(user_id, course_id, version_id)
    course_title = course_info.get("course_title", "Неизвестный курс")
    tariff_title = course_info.get("tariff_title", "Неизвестный тариф")

    return (
        f"🎓 Курс: {course_title}\n"
        f"🔑 Тариф: {tariff_title}\n"
        f"📚 Текущий урок: {lesson_num}"
    )


@db_exception_handler
async def check_lesson_schedule(user_id: int):
    """Проверяет расписание уроков и отправляет урок, если пришло время."""
    logger.info(f"🔄 Проверка расписания для пользователя {user_id}")

    async with aiosqlite.connect(DB_FILE) as conn:
        # Шаг 1: Получаем данные пользователя
        cursor = await conn.execute("""
            SELECT course_id, current_lesson, version_id, 
                   last_lesson_sent_time, hw_status, last_menu_message_id
            FROM user_courses 
            WHERE user_id = ? AND status = 'active'
        """, (user_id,))
        user_data = await cursor.fetchone()

        if not user_data:
            logger.info(f"❌ Нет активных курсов: {user_id}")
            return

        course_id, current_lesson, version_id, last_sent_time, hw_status, menu_message_id = user_data

        # Шаг 2: Проверка статуса ДЗ
        if hw_status not in ('approved', 'not_required'):
            logger.info(f"⏳ Ожидаем ДЗ или проверку: {user_id}")
            return

        # Шаг 3: Проверка времени
        message_interval = settings.get("message_interval", 24)
        if last_sent_time:
            try:
                last_sent = datetime.strptime(last_sent_time, '%Y-%m-%d %H:%M:%S')
                next_time = last_sent + timedelta(hours=message_interval)

                if datetime.now() < next_time:
                    # Формируем текст с временем
                    time_left = next_time - datetime.now()
                    hours = time_left.seconds // 3600
                    minutes = (time_left.seconds % 3600) // 60
                    time_message = f"⏳ Следующий урок через {hours}ч {minutes}мин\n"

                    # Получаем основной текст сообщения
                    message_text = await get_main_menu_text(user_id, course_id, current_lesson, version_id)
                    full_message = f"{message_text}\n\n{time_message}"

                    # Получаем клавиатуру
                    keyboard = await get_main_menu_inline_keyboard(
                        user_id=user_id,
                        course_id=course_id,
                        lesson_num=current_lesson,
                        user_tariff=version_id,
                        conn=conn
                    )

                    # Пытаемся обновить сообщение
                    if menu_message_id:
                        try:
                            await bot.edit_message_text(
                                chat_id=user_id,
                                message_id=menu_message_id,
                                text=full_message,
                                reply_markup=keyboard
                            )
                            logger.info(f"Тихо обновили сообщение для {user_id}")

                        except TelegramBadRequest as e:
                            logger.warning(f"Не удалось обновить сообщение: {e}")
                            # Если сообщение не найдено, сбрасываем ID
                            await conn.execute("""
                                UPDATE user_courses 
                                SET last_menu_message_id = NULL 
                                WHERE user_id = ?
                            """, (user_id,))
                            await conn.commit()
                    return  # ВЫХОДИМ ИЗ ФУНКЦИИ

            except ValueError as e:
                logger.error(f"⚠️ Ошибка преобразования времени: {e}")
                await bot.send_message(user_id, "📛 Ошибка времени урока!")
                return

        # Шаг 4: Отправка следующего урока
        logger.info(f"✅ Пора отправлять следующий урок для пользователя {user_id}")
        await send_lesson_to_user(user_id, course_id, current_lesson)
        await conn.execute("""
            UPDATE user_courses 
            SET last_lesson_sent_time = CURRENT_TIMESTAMP 
            WHERE user_id = ? AND course_id = ?
        """, (user_id, course_id))
        await conn.commit()



async def scheduled_lesson_check(user_id: int):
    """Запускает проверку расписания уроков для пользователя каждые 7 минут."""
    while True:
        await check_lesson_schedule(user_id)
        await asyncio.sleep(7 * 60)  # Каждые 7 минут

async def send_admin_stats():
    """Отправляет статистику администраторам каждые 5 часов."""
    global last_stats_sent
    while True:
        now = datetime.datetime.now()
        if last_stats_sent is None or now - last_stats_sent >= timedelta(hours=5):
            stats = await gather_course_statistics()
            await bot.send_message(ADMIN_GROUP_ID, stats)
            last_stats_sent = now
        await asyncio.sleep(500)  # Каждую (минуту) не, пореже раз в 8,5 (проверяем, не пора ли отправлять статистику)

async def gather_course_statistics():
    """Собирает статистику о пользователях и их прогрессе по курсам."""
    stats = "Статистика по курсам:\n"
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT uc.course_id, uc.current_lesson, COUNT(uc.user_id)
                FROM user_courses uc
                GROUP BY uc.course_id, uc.current_lesson
            """)
            course_data = await cursor.fetchall()

            for course_id, current_lesson, user_count in course_data:
                stats += f"Курс: {course_id}, Урок: {current_lesson}, Пользователей: {user_count}\n"
    except Exception as e:
        logger.error(f"Ошибка при сборе статистики: {e}")
        stats = "Ошибка при сборе статистики."
    return stats

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
            await conn.execute("""
                INSERT OR REPLACE INTO courses (course_id, group_id, title, description)
                VALUES (?, ?, ?, ?)
            """, (course_id, group_id, f"{course_id} basic", f"Описание для {course_id}"))

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
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
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
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT COLLATE NOCASE,
                    first_name TEXT COLLATE NOCASE,
                    last_name TEXT COLLATE NOCASE,
                    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await conn.commit()

            # Создаем таблицу courses  INSERT OR REPLACE INTO courses (course_id, title, group_id)
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS courses (
                    course_id TEXT PRIMARY KEY,
                    group_id TEXT,
                    title TEXT NOT NULL COLLATE NOCASE,
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
                    admin_id INTEGER PRIMARY KEY,
                    context_data TEXT
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
                    current_lesson INTEGER DEFAULT 0,
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
@db_exception_handler
async def log_user_activity(user_id, action, details=""):
    logger.info(f"log_user_activity {user_id=} {action=} {details=}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute(
                "INSERT INTO user_activity (user_id, action, details) VALUES (?, ?, ?)",
                (user_id, action, details)
            )
            await conn.commit()
        logger.info(f"Logged activity for user {user_id}: {action} - {details}")
    except Exception as e:
        logger.error(f"Error logging user activity: {e}")


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
async def send_lesson_to_user(user_id: int, course_id: str, lesson_num: int):
    """Отправляет урок и обновляет таймер. Работает, даже если урок — это мем с котиком 🐈"""
    logger.info(f"58585858585858585858 send_lesson_to_user {user_id=} {course_id=} {lesson_num=}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Шаг 1: Ищем контент урока (текст, видео, фото)
            cursor = await conn.execute("""
                SELECT text, content_type, file_id 
                FROM group_messages 
                WHERE course_id = ? AND lesson_num = ?
            """, (course_id, lesson_num))
            lesson_content = await cursor.fetchall()
            logger.info(f"58585858585 {lesson_content=}")

            if not lesson_content:
                logger.warning(f"Урок {lesson_num} в курсе {course_id} не найден.")
                await bot.send_message(user_id, "Урок не найден.")
                return

            for row in lesson_content:
                text, content_type, file_id = row
                if content_type == "text" and text:
                    await bot.send_message(user_id, text)
                elif content_type == "video" and file_id:
                    await bot.send_video(user_id, video=file_id, caption=text or None)
                elif content_type == "photo" and file_id:
                    await bot.send_photo(user_id, photo=file_id, caption=text or None)
                elif content_type == "document" and file_id:
                    await bot.send_document(user_id, document=file_id, caption=text or None)
            send_time = datetime.datetime.now()
            # Обновляем время отправки урока в таблице user_courses
            await conn.execute("""
                UPDATE user_courses
                SET last_lesson_sent_time = ?
                WHERE user_id = ? AND course_id = ?
            """, (send_time.strftime('%Y-%m-%d %H:%M:%S'), user_id, course_id))
            await conn.commit()

    except Exception as e:
        logger.error(f"Ошибка при отправке урока пользователю {user_id}: {e}")
        await bot.send_message(user_id, "Произошла ошибка при отправке урока.")

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



def generate_progress_bar(percent, length=10):
    """Generate a text progress bar"""
    filled = int(percent / 100 * length)
    bar = "▓" * filled + "░" * (length - filled)
    return bar


# Обновленная функция process_homework_submission
async def process_homework_submission(message: Message):
    """Отправка ДЗ на проверку админам"""
    user_id = message.from_user.id
    logger.info(f"Отправка ДЗ на проверку для {user_id}")

    try:
        # Получаем данные пользователя
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT uc.version_id, uc.course_id, c.title, uc.current_lesson, uc.hw_status, gm.message_id
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                JOIN group_messages gm ON uc.course_id = gm.course_id AND uc.current_lesson = gm.lesson_num
                WHERE uc.user_id = ?
            """, (user_id,))
            user_data = await cursor.fetchone()

            if not user_data:
                return await message.answer("❌ Активный курс не найден")

            version_id, course_id, course_title, current_lesson,hw_status, message_id = user_data

        # Forward homework to admin group
        admin_message = (
            f"📝 *Новое ДЗ*\n"
            f"👤 Пользователь: {message.from_user.full_name}\n"
            f"📚 Курс: {course_title}\n"
            f"⚡ Тариф: {version_id}\n"
            f"📖 Урок: {current_lesson}"
        )

        forwarded_msg = await message.forward(ADMIN_GROUP_ID)
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton(text="✅ Принять", callback_data=f"approve_hw:{user_id}:{course_id}:{current_lesson}:{message_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_hw:{user_id}:{course_id}:{current_lesson}:{message.message_id}")
        )

        await bot.send_message(
            ADMIN_GROUP_ID,
            admin_message,
            parse_mode="MarkdownV2",
            reply_to_message_id=forwarded_msg.message_id,
            reply_markup = keyboard
        )

    except Exception as e:
        logger.error(f"Ошибка обработки ДЗ: {e}")
        await message.answer("❌ Ошибка отправки ДЗ")



def get_admin_keyboard():
    """Формирует клавиатуру для администраторов."""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Экспорт базы данных", callback_data="export_db"),
                InlineKeyboardButton(text="Импорт базы данных", callback_data="import_db")
            ]
        ]
    )
    return keyboard

# 13-04 добавили для времени в меню - убрали. тут обращение к БД, а мы юзаем теперь кэш
async def old_get_main_menu_text(user_id: int, course_id: str, lesson_num: int, version_id: str) -> str:
    """Возвращает основной текст для меню"""
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT c.title, cv.title 
            FROM courses c
            JOIN course_versions cv ON c.course_id = cv.course_id
            WHERE c.course_id = ? AND cv.version_id = ?
        """, (course_id, version_id))
        course_title, tariff_title = await cursor.fetchone()

    return (
        f"🎓 Курс: {course_title}\n"
        f"🔑 Тариф: {tariff_title}\n"
        f"📚 Текущий урок: {lesson_num}"
    )


def get_main_menu_inline_keyboard(
    course_id: str,
    lesson_num: int,
    user_tariff: str,
    homework_pending: bool = False,
    courses_button_text: str = "📚 Мои курсы"  # Новый параметр с значением по умолчанию
) -> InlineKeyboardMarkup:
    """
    Создает inline-клавиатуру для основного меню с динамическим текстом кнопки курсов.

    Args:
        course_id: ID курса
        lesson_num: Номер текущего урока
        user_tariff: Тариф пользователя (v1/v2/v3)
        homework_pending: Флаг наличия ДЗ для проверки
        courses_button_text: Текст кнопки "Мои курсы" с количеством (по умолчанию "📚 Мои курсы")

    Returns:
        InlineKeyboardMarkup: Клавиатура с динамическими кнопками
    """
    builder = InlineKeyboardBuilder()

    # Основная кнопка текущего урока
    builder.row(
        InlineKeyboardButton(
            text="📚 Текущий урок (прислать повторно)",
            callback_data=CourseCallback(
                action="menu_cur",
                course_id=course_id,
                lesson_num=lesson_num
            ).pack()
        )
    )

    # Кнопка самоодобрения для тарифа v1
    if user_tariff == "v1" and homework_pending:
        builder.row(
            InlineKeyboardButton(
                text="✅ СамоОдобрить ДЗ",
                callback_data=CourseCallback(
                    action="self_approve_hw",
                    course_id=course_id,
                    lesson_num=lesson_num
                ).pack()
            )
        )

    # Дополнительные кнопки с динамическим текстом
    builder.row(
        InlineKeyboardButton(text=courses_button_text, callback_data="menu_mycourses"),
        InlineKeyboardButton(text="📈 Прогресс", callback_data="menu_progress"),
        InlineKeyboardButton(text="📞 Поддержка", callback_data="menu_support")
    )

    return builder.as_markup()



# проверим канал на доступ todo: сделать паузу если каналов много чтоб не банили. или запускать на оч тормозном компе
async def check_group_access(bot: Bot, raw_id: str, course_name: str):
    """Проверка доступа с корректным экранированием"""
    logger.info(f"check_group_access {raw_id=} {course_name=}")
    try:
        group_id = int(raw_id)
        chat = await bot.get_chat(group_id)
        # Экранируем title перед использованием в MarkdownV2
        escaped_title = escape_md(chat.title)
        # Генерация ссылки (для каналов с username)
        if chat.username:
            link = f"[{escaped_title}](t.me/{chat.username})"
        else:
            link = f"[{escaped_title}](t.me/c/{str(chat.id).replace('-100', '')})"
        return f"{group_id} {'OK'} {link} "  # убрал эмодзи

    except TelegramBadRequest as e:
        return f"Ошибка: {course_name} | ID: {raw_id}\n   Подробнее: {str(e)}"  # убрал эмодзи


# ============= для взаимодействия с группами уроков. работает при добавлении материала в группу ===========

@db_exception_handler
async def save_message_to_db(group_id: int, message: Message):
    """Сохранение сообщения в базу данных."""
    global lesson_stack, last_message_info
    group_id = str(message.chat.id)
    mes_id = message.message_id
    logger.info(f"Saving message {mes_id=} from group {group_id=}")

    # Шаг 1: Определение course_id для данного group_id
    logger.info(f"777 ищем course_id для group_id {group_id}.")
    course_id = next(
        (course for g, course in settings["groups"].items() if g == str(group_id)),
        None
    )

    if not course_id:
        logger.warning(f"777 Не найден course_id для group_id {group_id}.")
        return
    logger.info(f"777 это {course_id=}.")

    # Определение типа контента и извлечение file_id
    text = message.text or ""
    user_id = message.from_user.id if message.from_user else None
    file_id = message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else None)
    logger.info(f"777!!! это {user_id=} {file_id=} {course_id=}")
    # Extract lesson markers
    start_lesson_match = re.search(r"\*START_LESSON (\d+)", text)
    end_lesson_match = re.search(r"\*END_LESSON (\d+)", text)
    hw_start_match = re.search(r"\*HW_START", text)
    hw_end_match = re.search(r"\*HW_END", text)
    course_end_match = re.search(r"\*COURSE_END", text)
    hw_type_match = re.search(r"\*HW_TYPE\s*(\w+)", text)

    lesson_num = None
    is_homework = False
    hw_type = 'none'  # Значение по умолчанию

    if hw_type_match:
        hw_type = hw_type_match.group(1).lower()  # Получаем тип ДЗ и приводим к нижнему регистру
        logger.info(f"Обнаружен тип домашнего задания: {hw_type}")

    # Remove tags from the text
    cleaned_text = re.sub(r"\*START_LESSON (\d+)", "", text)
    cleaned_text = re.sub(r"\*END_LESSON (\d+)", "", cleaned_text)
    cleaned_text = re.sub(r"\*HW_START", "", cleaned_text)
    cleaned_text = re.sub(r"\*HW_END", "", cleaned_text)
    cleaned_text = re.sub(r"\*COURSE_END", "", cleaned_text)
    cleaned_text = re.sub(r"\*HW_TYPE\s*(\w+)", "", cleaned_text)

    async with aiosqlite.connect(DB_FILE) as conn:
        if start_lesson_match:
            lesson_num = int(start_lesson_match.group(1))
            # Push the new lesson number onto the stack
            if group_id not in lesson_stack:
                lesson_stack[group_id] = []
            lesson_stack[group_id].append(lesson_num)
        elif end_lesson_match:
            lesson_num = int(end_lesson_match.group(1))
            # Pop the lesson number from the stack, but only if it matches
            if group_id in lesson_stack and lesson_stack[group_id]:
                if lesson_stack[group_id][-1] == lesson_num:
                    lesson_stack[group_id].pop()
                else:
                    logger.warning(
                        f"Mismatched END_LESSON tag for group {group_id}. Expected {lesson_stack[group_id][-1]}, got {lesson_num}.")
            else:
                logger.warning(f"Unexpected END_LESSON tag for group {group_id}. Stack is empty.")
        elif hw_start_match:
            # Homework always belongs to the current lesson
            if group_id in lesson_stack and lesson_stack[group_id]:
                lesson_num = lesson_stack[group_id][-1]
            else: # Если нет открытого урока, берем номер из последнего сообщения
                lesson_num = last_message_info.get(group_id, {}).get("lesson_num")
                logger.warning(f"HW_START Using last known lesson: {lesson_num}... without active lesson in group {group_id}.")
            is_homework = True
        elif hw_end_match:
            # Ignore end markers, we only care about start markers
            pass
        elif course_end_match:
            # Course end processing
            await process_course_completion(group_id, conn)
            logger.info(f"Course ended in group {group_id}. Statistics processed.")
            return  # Stop further processing of this message

        # If there are active lessons, take the latest
        if group_id in lesson_stack and lesson_stack[group_id]:
            lesson_num = lesson_stack[group_id][-1]

        # Extract course information from the first message
        course_snippet = None

        # Extract course information from the first message
        if cleaned_text.startswith("*Курс"):
            course_snippet = extract_course_snippet(cleaned_text)
            course_title = extract_course_title(cleaned_text)
            # Update course title and snippet
            await conn.execute("""
                UPDATE courses
                SET title = ?, description = ?
                WHERE course_id = ?
            """, (course_title, course_snippet, group_id))
            await conn.commit()
            logger.info(f"Upd {group_id} type {message.content_type}")

        # Определите тип контента
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
            text = message.caption  # Подпись к фото
        elif message.content_type == "video":
            file_id = message.video.file_id
            text = message.caption  # Подпись к видео
        elif message.content_type == "document":
            file_id = message.document.file_id
            text = message.caption  # Подпись к документу
        elif message.content_type == "audio" and message.audio:
            file_id = message.audio.file_id
        else:
            file_id = None
            text = cleaned_text  # Обычный текст
        logger.info(f"13 {file_id=} {hw_type=}")
        # Save the message to the database
        await conn.execute("""
            INSERT INTO group_messages (
                group_id, message_id, content_type, text, file_id,
                is_forwarded, forwarded_from_chat_id, forwarded_message_id,
                course_id, lesson_num, is_homework, hw_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            group_id, message.message_id, message.content_type, text,
            file_id, message.forward_origin is not None, message.forward_origin.chat.id if message.forward_origin else None,
            message.forward_origin.message_id if message.forward_origin else None,
            course_id, lesson_num, is_homework, hw_type
        ))
        await conn.commit()


        # Обновляем информацию о последнем сообщении
        last_message_info[group_id] = {"lesson_num": lesson_num}
        logger.info(f"last_message_info {group_id=} = {lesson_num=}")

        logger.info(
            f"Сообщение сохранено: {group_id=}, {lesson_num=}, {course_id=}, {message.content_type=}, {is_homework=}, {text=}, {file_id=}"
        )

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
            SELECT text FROM group_messages
            WHERE course_id = ? AND lesson_num = ?
            ORDER BY id ASC
        """, (course_id, lesson_num))
        lesson_content = await cursor.fetchall()

        if not lesson_content:
            logger.warning(f"Не найдено содержимое для урока {lesson_num} курса {course_id}.")
            return

        lesson_text = "\n".join([row[0] for row in lesson_content])

        # Отправляем урок администраторам
        if ADMIN_GROUP_ID:
            course_name = settings["groups"].get(group_id, "Unknown Course")
            await bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Случайный урок курса {course_name} ({course_id}), урок {lesson_num}:\n{lesson_text}")
            logger.info(f"Случайный урок курса {course_name} ({course_id}) отправлен администраторам.")
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
            f"Всего уроков: {total_lessons}\n"
        )
        # Сохраняем group_id в таблицу courses
        await conn.execute("""
            UPDATE courses SET group_id = ? WHERE course_id = ?
        """, (group_id, course_id)) # group_id == course_id
        await conn.commit()
        logger.info(f"5 Сохранили group_id {group_id} для курса {course_id}")

        # Отправка статистики в группу администраторов
        if ADMIN_GROUP_ID:
            await bot.send_message(chat_id=ADMIN_GROUP_ID, text=stats_message)
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
            # Добавляем курсы
            for group_id, course_id in settings["groups"].items():
                await conn.execute("""
                    INSERT OR IGNORE INTO courses 
                    (course_id, group_id, title) 
                    VALUES (?, ?, ?)
                """, (course_id, group_id, course_id))
                logger.info(f"Added course: {course_id}")

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


async def check_groups_access(bot: Bot, raw_id: str, gr_name: str):
    """Проверка доступа с корректным экранированием"""
    logger.info("Внутри функции check_groups_access")
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


async def get_courses_list():
    """Получает список доступных курсов из базы данных."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT course_id, title FROM courses")
            courses = await cursor.fetchall()
            return courses
    except Exception as e:
        logger.error(f"Error fetching available courses: {e}")
        return None


async def send_startup_message(bot: Bot, admin_group_id: int):
    """Отправляет сообщение админам о запуске бота и статусе группов."""
    global settings
    logger.info(f"222 {settings=}")
    channel_reports = []
    kanalz=settings.get("groups", {}).items()
    logger.info(f"555555555555555 Внутри функции send_startup_message {kanalz=}")
    for raw_id, gr_name in kanalz:
        logger.info(f"Внутри функции send_startup_message")
        report = await check_groups_access(bot, raw_id, gr_name)
        channel_reports.append(report)
    # Формирование текста сообщения для администраторов
    message_text = escape_md("Бот запущен\n\nСтатус групп курсов:\n" + "\n".join(channel_reports) + \
                   "\nможно: /add_course <group_id> <course_id> <code1> <code2> <code3>")

    # Отправка сообщения в группу администраторов
    try:
        await bot.send_message(admin_group_id, message_text, parse_mode="MarkdownV2")
    except Exception as e:
        logger.error(f"Ошибка при отправке стартового сообщения в группу администраторов: {e}")
    logger.info("Стартовое сообщение отправлено администраторам")


# 10-04 4. Переход к следующему уроку # После успешной проверки ДЗ (одобрения админом).# ИЛИ по расписанию (если уроки открываются автоматически).
async def move_to_next_lesson(user_id: int):
    async with aiosqlite.connect(DB_FILE) as conn:
        # Получаем текущий урок
        cursor = await conn.execute("""
            SELECT course_id, current_lesson 
            FROM user_courses 
            WHERE user_id = ? AND status = 'active'
        """, (user_id,))
        course_data = await cursor.fetchone()

        if not course_data:
            return

        course_id, current_lesson = course_data

        # Проверяем, есть ли следующий урок
        cursor = await conn.execute("""
            SELECT COUNT(*) 
            FROM group_messages 
            WHERE course_id = ? AND lesson_num > ?
        """, (course_id, current_lesson))
        next_lesson_exists = (await cursor.fetchone())[0]

        if next_lesson_exists:
            # Обновляем текущий урок
            await conn.execute("""
                UPDATE user_courses 
                SET current_lesson = current_lesson + 1 
                WHERE user_id = ?
            """, (user_id,))
            await conn.commit()
            await bot.send_message(user_id, "🎉 Поздравляем! Следующий урок доступен.")
        else:
            await bot.send_message(user_id, "🏆 Вы завершили курс! Молодец!")


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
        await message.answer("Приватные сообщения не обрабатываются.")
        return

    await save_message_to_db(message.chat.id, message)


# Админские команды
#=======================================================================================================================
# Admin command to reply to user

@dp.message(Command("edit_code"), F.chat.id == ADMIN_GROUP_ID)
async def edit_code(message: types.Message):
    """Изменяет кодовое слово для активации курса."""
    logger.info(f"1 edit_code  ")
    try:
        parts = message.text.split()
        if len(parts) != 4:
            await message.answer("Используйте: /edit_code <курс> <версия> <новый_код>")
            return

        course_id = parts[1]
        version = parts[2]
        new_code = parts[3]

        # Проверяем, что курс и версия существуют
        if course_id not in settings["groups"].values():
            await message.answer("Курс не найден.")
            return
        if version not in ["v1", "v2", "v3"]:
            await message.answer("Неверная версия курса.")
            return

        # Ищем старый код и удаляем его
        old_code = next(
            (
                code
                for code, info in settings["activation_codes"].items()
                if info == f"{course_id}:{version}"
            ),
            None,
        )
        if old_code:
            del settings["activation_codes"][old_code]

        # Добавляем новый код
        settings["activation_codes"][new_code] = f"{course_id}:{version}"
        save_settings(settings)

        await message.answer(f"Код для курса {course_id} ({version}) изменен на {new_code}")

    except Exception as e:
        logger.error(f"Ошибка при изменении кода активации: {e}")
        await message.answer("Произошла ошибка при изменении кода активации.")


@dp.message(Command("adm_message_user"), F.chat.id == ADMIN_GROUP_ID)
async def adm_message_user(message: Message):
    """Send a message to a user from admin"""
    command_parts = message.text.split(maxsplit=2)
    logger.info(f" 2 adm_message_user {command_parts=}  ")
    if len(command_parts) < 3:
        await message.answer("Использование: /adm_message_user <user_id|alias> <текст>")
        return

    user_identifier = command_parts[1]
    text = command_parts[2]

    # Resolve user ID from identifier (could be numeric ID or alias)
    user_id = await resolve_user_id(user_identifier)
    if not user_id:
        await message.answer(f"Пользователь с идентификатором '{user_identifier}' не найден.")
        return

    # Send message to user
    try:
        await bot.send_message(
            user_id,
            f"📩 *Сообщение от поддержки:*\n\n{text}",
            parse_mode="MarkdownV2"
        )

        # Log the response
        admin_name = message.from_user.full_name
        await log_user_activity(user_id, "SUPPORT_RESPONSE", f"From: {admin_name}, Message: {text[:100]}...")

        await message.answer(f"✅ Сообщение отправлено пользователю {user_id}.")
    except Exception as e:
        await message.answer(f"❌ Ошибка при отправке сообщения: {str(e)}")


@dp.message(Command("adm_approve_course"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler  # Админ-команда для одобрения курса
async def approve_course(message: Message):
    logger.info(f"5553 approve_course ")
    try:
        user_id, course_id = message.text.split()[1:]
        user_id = int(user_id)
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                UPDATE user_courses SET status = 'active' WHERE user_id = ? AND course_id = ?
            """, (user_id, course_id))
            await conn.commit()
        await bot.send_message(user_id, f"Ваш доступ к курсу '{course_id}' одобрен!")
        await send_lesson_to_user(user_id, course_id, 1)
        # времена запишем чтоб было в базе
        await conn.execute("""
                UPDATE user_courses 
                SET first_lesson_sent_time = CURRENT_TIMESTAMP, 
                    last_lesson_sent_time = CURRENT_TIMESTAMP 
                WHERE user_id = ? AND course_id = ?
            """, (user_id, course_id))
        await conn.commit()
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


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
                      "course_activation_codes"]
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
        await message.answer("❌ Произошла ошибка при экспорте базы данных.")

@dp.message(Command("import_db"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def import_db(message: types.Message):  # types.Message instead of Message
    """Импорт данных из JSON-файла в базу данных. Только для администраторов."""
    logger.info("4 Получена команда /import_db")

    if not message.document:
        await message.answer("❌ Пожалуйста, отправьте JSON-файл с данными.")
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
                      "course_activation_codes"]
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

        await message.answer("✅ База данных успешно импортирована из JSON.")
        logger.info("База данных успешно импортирована.")
    except Exception as e:
        logger.error(f"Ошибка при импорте базы данных: {e}")
        await message.answer("❌ Произошла ошибка при импорте базы данных.")


# 13-04 просмотрено
@dp.callback_query(F.chat.id == ADMIN_GROUP_ID,lambda c: c.data.startswith("approve_hw:") or c.data.startswith("reject_hw:"))
@db_exception_handler
async def handle_homework_decision(callback_query: CallbackQuery):
    """Обрабатывает решение админа о ДЗ"""
    logger.info(f"handle_homework_decision")
    action, user_id, course_id, lesson_num,message_id = callback_query.data.split(":")
    admin_id = callback_query.from_user.id
    user_id = int(user_id)
    lesson_num = int(lesson_num)
    message_id = int(message_id)
    logger.info(f"Решение админа: {action}, {user_id}, {course_id}, {lesson_num}, {admin_id}")

    async with aiosqlite.connect(DB_FILE) as conn:
        if action == "approve_hw":
            # Одобрение ДЗ
            await conn.execute("""
                UPDATE user_courses SET hw_status = 'approved'
                WHERE user_id = ? AND course_id = ? AND current_lesson = ?
            """, (user_id, course_id, lesson_num))
            # Добавляем в галерею - В ЭТОМ месте
            # Добавляем в галерею - В ЭТОМ месте
            # Добавляем в галерею - В ЭТОМ месте
            await conn.execute("""
                INSERT INTO homework_gallery (user_id, course_id, lesson_num, message_id, approved_by)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, course_id, lesson_num, message_id, admin_id))
            # Уведомляем пользователя
            await bot.send_message(
                user_id,
                f"✅ Ваше домашнее задание к уроку {lesson_num} курса '{course_id}' одобрено!"
            )
            #Получаем версию
            cursor = await conn.execute("""
                SELECT version_id  FROM user_courses 
                WHERE user_id = ? AND course_id = ? 
            """, (user_id, course_id))
            user_data = await cursor.fetchone()
            version_id = user_data[0]

            # Запустить логику перехода к следующему уроку:
            new_lesson = lesson_num + 1
            await conn.execute("""
                UPDATE user_courses 
                SET current_lesson = ?
                WHERE user_id = ? AND course_id = ?
            """, (new_lesson, user_id, course_id))

            cursor = await conn.execute("""
                SELECT homework_type FROM group_messages 
                WHERE  course_id = ? AND lesson_num = ?
            """, ( course_id, new_lesson))
            lesson_data = await cursor.fetchone()
            homework_type = lesson_data[0]
            # Установить homework_status для нового урока (в 'required' или 'not_required'):
            if homework_type == 'none':
                await conn.execute("""
                        UPDATE user_courses 
                        SET hw_status = 'not_required'
                        WHERE user_id = ? AND course_id = ? AND current_lesson = ?
                    """, (user_id, course_id, new_lesson))
            else:
                await conn.execute("""
                        UPDATE user_courses 
                        SET hw_status = 'required'
                        WHERE user_id = ? AND course_id = ? AND current_lesson = ?
                    """, (user_id, course_id, new_lesson))

            await conn.commit()
            # Отправить пользователю контент нового урока.
            await send_lesson_to_user(user_id, course_id, new_lesson)
            # Уведомить пользователя
            await bot.send_message(
                user_id,
                f"✅ Ваше домашнее задание к уроку {lesson_num} курса '{course_id}' одобрено!"
            )
            await bot.send_message(user_id, "ДЗ принято! Следующий урок доступен.")
        elif action == "reject_hw":
            # Отклонение ДЗ
            # Запрашиваем комментарий от админа

            await callback_query.message.edit_text(
                "📝 Пожалуйста, отправьте причину отклонения домашнего задания.\n"
                "Для отмены введите /cancel"
            )
            return

        await conn.commit()

        # Перерисовываем меню администратора
        button_back = [[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]]
        keyboard = InlineKeyboardMarkup(inline_keyboard=button_back)
        await callback_query.message.edit_text("Действие выполнено успешно.",
                                               reply_markup=keyboard)


@dp.message(F.reply_to_message, F.chat.id == ADMIN_GROUP_ID)
async def handle_support_reply(message: types.Message):
    """Пересылка ответа от админа пользователю."""
    global user_support_state
    user_id = user_support_state.get(message.reply_to_message.forward_from.id, {}).get("user_id")
    logger.info(f"5 handle_support_reply {user_id=}  ")
    if user_id:
        evaluation_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="😍 Класс!", callback_data=f"support_eval:{user_id}:5"),
                InlineKeyboardButton(text="👍 Норм", callback_data=f"support_eval:{user_id}:4"),
            ],
            [
                InlineKeyboardButton(text="😐 Средне", callback_data=f"support_eval:{user_id}:3"),
                InlineKeyboardButton(text="👎 Фигня", callback_data=f"support_eval:{user_id}:2"),
            ],
            [InlineKeyboardButton(text="😡 Злой", callback_data=f"support_eval:{user_id}:1")]
        ])

        await bot.send_message(
            chat_id=user_id,
            text=f"Ответ от поддержки:\n{message.text}",
            reply_markup=evaluation_keyboard,
        )
    else:
        await message.reply("Не удалось отправить ответ пользователю. Возможно, запрос устарел.")


@dp.message(Command("add_course"), F.chat.id == ADMIN_GROUP_ID)
async def cmd_add_course(message: types.Message):
    """Обработчик команды /add_course для добавления курса."""
    logger.info(f"6 cmd_add_course  ")
    try:
        args = message.text.split()
        if len(args) != 5:
            await message.answer("Неправильное количество аргументов. Используйте: /add_course course_id group_id code1 code2 code3")
            return

        course_id, group_id, code1, code2, code3 = args[1:]
        await process_add_course_to_db(course_id, group_id, code1, code2, code3)

        await message.answer(f"Курс {course_id} успешно добавлен.")
    except Exception as e:
        logger.error(f"Ошибка при добавлении курса: {e}")
        await message.answer("Произошла ошибка при добавлении курса.")



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


@dp.message(F.chat.id == ADMIN_GROUP_ID, Command("admin"))
async def admin_panel(message: types.Message):
    """Админ-панель для управления курсами."""
    logger.info(f"7 admin_panel  ")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="View Courses", callback_data="admin_view_courses")]
    ])
    await message.answer("Admin Panel", reply_markup=keyboard)

@dp.callback_query(F.chat.id == ADMIN_GROUP_ID,F.data == "admin_view_courses")
async def admin_view_courses(query: types.CallbackQuery):
    """Просмотр списка курсов."""
    logger.info(f"8 admin_view_courses  ")
    async with aiosqlite.connect(DB_FILE) as db:
        courses = await db.execute_fetchall("SELECT course_id, title FROM courses")
    if not courses:
        await query.answer("No courses found.", show_alert=True)
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=title, callback_data=f"admin_edit_course:{course_id}")]
        for course_id, title in courses
    ])
    await query.message.edit_text("Select a course to edit:", reply_markup=keyboard)

@dp.callback_query(F.chat.id == ADMIN_GROUP_ID, lambda c: c.data.startswith("admin_edit_course:"))
async def admin_edit_course(query: types.CallbackQuery):
    """Редактирование курса."""
    course_id = query.data.split(":")[1]
    logger.info(f"9 admin_edit_course {course_id=} ")
    async with aiosqlite.connect(DB_FILE) as db:
        cursor = await db.execute("SELECT course_id, title, description FROM courses WHERE course_id = ?", (course_id,))
        course = await cursor.fetchone()
        if not course:
            await query.answer("Course not found.", show_alert=True)
            return
        lessons = await db.execute_fetchall("SELECT lesson_num FROM group_messages WHERE group_id = ? GROUP BY lesson_num ORDER BY lesson_num", (course_id,)) # group_id = course_id
        lesson_buttons = [InlineKeyboardButton(
            text=f"Lesson {lesson_num}",
            callback_data=f"admin_edit_lesson:{course_id}:{lesson_num}"
        ) for lesson_num, in lessons]
        lesson_buttons.append(InlineKeyboardButton(
            text="Add lesson",
            callback_data=f"admin_add_lesson:{course_id}"
        ))
        keyboard = InlineKeyboardMarkup(inline_keyboard=[lesson_buttons])
        await query.message.edit_text(f"Editing course: {course[1]}\nDescription: {course[2]}", reply_markup=keyboard)

@dp.callback_query(F.chat.id == ADMIN_GROUP_ID,lambda c: c.data.startswith("admin_edit_lesson:"))
async def admin_edit_lesson(query: types.CallbackQuery):
    course_id, lesson_num = query.data.split(":")[1], query.data.split(":")[2]
    logger.info(f"10 admin_edit_lesson {course_id=} {lesson_num=}")
    # Fetch all messages from group_messages for this course_id and lesson_num
    async with aiosqlite.connect(DB_FILE) as db:
        messages = await db.execute_fetchall(
            "SELECT message_id, text FROM group_messages WHERE group_id = ? AND lesson_num = ? ORDER BY timestamp ASC",
            (course_id, lesson_num)
        )
        lesson_title = f"Lesson {lesson_num} from {course_id}"
        message_contents = "\n---\n".join([f"Message ID: {msg_id}\n{text}" for msg_id, text in messages])
        await query.message.edit_text(
            f"{lesson_title}\n\nMessages:\n{message_contents}\n\nWhat do you want to do?",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Edit Tags", callback_data=f"admin_edit_tags:{course_id}:{lesson_num}")],
                [InlineKeyboardButton(text="Delete Lesson", callback_data=f"admin_delete_lesson:{course_id}:{lesson_num}")],
            ])
        )

@dp.callback_query(F.chat.id == ADMIN_GROUP_ID,lambda c: c.data.startswith("admin_add_lesson:"))
async def admin_add_lesson(query: types.CallbackQuery):
    """Добавление урока."""
    course_id = query.data.split(":")[1]
    logger.info(f"11 admin_add_lesson {course_id=} ")
    # Get the next available lesson number
    async with aiosqlite.connect(DB_FILE) as db:
        existing_lessons = await db.execute_fetchall(
            "SELECT lesson_num FROM group_messages WHERE group_id = ? GROUP BY lesson_num",
            (course_id,)
        )
        if existing_lessons:
            next_lesson_num = max([lesson[0] for lesson in existing_lessons]) + 1
        else:
            next_lesson_num = 1
        await query.message.answer(
            f"Start sending messages for Lesson {next_lesson_num} in course {course_id}."
            f" Use *START_LESSON {next_lesson_num} and *END_LESSON {next_lesson_num}.",
            parse_mode="Markdown"
        )

@dp.callback_query(F.chat.id == ADMIN_GROUP_ID,lambda c: c.data.startswith("admin_edit_tags:"))
async def admin_edit_tags(query: types.CallbackQuery):
    """Редактирование тегов урока."""
    course_id, lesson_num = query.data.split(":")[1], query.data.split(":")[2]
    logger.info(f"12 admin_edit_tags {course_id=} ")
    # Display current tags, and then ask for new tags
    await query.message.edit_text(
        f"Editing tags for Lesson {lesson_num} of course {course_id}."
        f"\nSend the new tags as a list, separated by commas.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Cancel", callback_data=f"admin_edit_lesson:{course_id}:{lesson_num}")]
        ])
    )

    # Register handler to receive new tags


@dp.callback_query(F.chat.id == ADMIN_GROUP_ID,lambda c: c.data.startswith("admin_delete_lesson:"))
async def admin_delete_lesson(query: types.CallbackQuery):
    """Удаление урока."""
    course_id, lesson_num = query.data.split(":")[1], query.data.split(":")[2]
    logger.info(f"13 admin_delete_lesson {course_id=} ")
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "DELETE FROM group_messages WHERE group_id = ? AND lesson_num = ?",
            (course_id, lesson_num)
        )
        await db.commit()
    await query.message.edit_text(
        f"Lesson {lesson_num} of course {course_id} deleted successfully.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Back to Course", callback_data=f"admin_edit_course:{course_id}")]
        ])
    )


@dp.message(F.text, F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def process_rejection_reason(message: Message):
    """Обрабатывает сообщение с причиной отклонения домашнего задания от админа."""
    admin_id = message.from_user.id
    logger.info(f"5557 process_rejection_reason {admin_id} ")
    rejection_reason = message.text

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем контекст из admin_context
            context_cursor = await conn.execute("""
                SELECT context_data FROM admin_context WHERE admin_id = ?
            """, (admin_id,))
            context_data = await context_cursor.fetchone()

            if not context_data:
                await message.reply("Контекст не найден. Попробуйте отклонить ДЗ заново.")
                return

            context = json.loads(context_data[0])
            user_id = context["user_id"]
            course_id = context["course_id"]
            lesson_num = context["lesson_num"]

            # Обновляем homework и устанавливаем статус "rejected"
            await conn.execute("""
                UPDATE homework SET status = 'rejected', admin_id = ?, 
                decision_date = CURRENT_TIMESTAMP, rejection_reason = ?
                WHERE user_id = ? AND course_id = ? AND lesson_num = ?
            """, (admin_id, rejection_reason, user_id, course_id, lesson_num))
            await conn.commit()

            # Уведомляем пользователя об отклонении
            await bot.send_message(
                user_id,
                f"❌ Ваше домашнее задание к уроку {lesson_num} курса '{course_id}' отклонено.\n"
                f"Причина: {rejection_reason}\n\n"
                "Вы можете отправить новое домашнее задание."
            )

            # Удаляем контекст
            await conn.execute("DELETE FROM admin_context WHERE admin_id = ?", (admin_id,))
            await conn.commit()

            await message.reply("Причина отклонения отправлена пользователю.")

    except Exception as e:
        logger.error(f"Ошибка в process_rejection_reason: {e}", exc_info=True)
        await message.reply("Произошла ошибка при обработке причины отклонения.")




# Команды для взаимодействия с пользователем - в конце, аминь.
#=======================================================================================================================



@db_exception_handler
async def get_user_tariff_from_db(user_id: int) -> str:
    """Получает тариф пользователя из базы данных."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT cv.version_id
                FROM user_courses uc
                JOIN course_activation_codes cac ON uc.course_id = cac.course_id
                JOIN course_versions cv ON cac.version_id = cv.version_id
                WHERE uc.user_id = ? AND uc.status = 'active'
            """, (user_id,))
            tariff_data = await cursor.fetchone()
            if tariff_data:
                return tariff_data[0]  # Возвращаем tariff
            else:
                return "free"  # default tariff
    except Exception as e:
        logger.error(f"Error getting user tariff from db: {e}")
        return "free"  # Default tariff on error

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


@dp.message(CommandStart())
@db_exception_handler
async def cmd_start(message: types.Message):
    """Обработчик команды /start."""
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
                await message.answer("❌ Нет активных курсов. Активируйте курс через код")
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
                    await message.answer("⚠️ Произошла ошибка при отправке фотографии.")
                except Exception as e:
                    logger.error(f"Ошибка при отправке фото: {e}", exc_info=True)
                    await message.answer("⚠️ Произошла ошибка при отправке фотографии.")

                return

            # Распаковываем данные активного курса
            course_id, lesson_num, version_id, course_name, version_name, status, hw_status = current_course
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

            # Генерация клавиатуры
            keyboard = get_main_menu_inline_keyboard(  # await убрали
                course_id=course_id,
                lesson_num=lesson_num,
                user_tariff=version_id,
                homework_pending=True if hw_status != 'approved' and hw_status != 'not_required' else False,
                courses_button_text=courses_button_text
            )

            welcome_message = (
                f"С возвращением, {first_name}!\n\n"
                f"🎓 Курс: {course_name}\n"
                f"🔑 Тариф: {tariff_name}\n"
                f"📚 Текущий урок: {lesson_num}"

            )
            logger.info(f"Старт задачи для шедулера для {user_id=}")
            await start_lesson_schedule_task(user_id)

            await message.answer(welcome_message, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Error in cmd_start: {e}", exc_info=True)
        await message.answer("Произошла ошибка при обработке команды. Пожалуйста, попробуйте позже.")


async def send_course_description(user_id: int, course_id: str):
    """Отправляет описание курса пользователю."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT text
                FROM group_messages
                WHERE course_id = ? AND lesson_num = 0
            """, (course_id,))
            description = await cursor.fetchone()

            if description:
                await bot.send_message(user_id, description[0])
            else:
                await bot.send_message(user_id, "Описание курса не найдено.")

    except Exception as e:
        logger.error(f"Error sending course description: {e}")
        await bot.send_message(user_id, "Ошибка при получении описания курса.")


def get_tariff_name(version_id: str) -> str:
    """Возвращает человекочитаемое название тарифа."""
    TARIFF_NAMES = {
        "v1": "Соло",
        "v2": "Группа",
        "v3": "VIP"
    }
    return TARIFF_NAMES.get(version_id, f"Тариф {version_id}")


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


@dp.callback_query(F.data == "menu_support")
async def cmd_support_callback(query: types.CallbackQuery):
    """Обработчик для кнопки 'Поддержка'."""
    global user_support_state
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    logger.info("10 1 cmd_support_callback  {user_id=}  {chat_id=}  {message_id=} ")
    # Создаем кнопки оценки
    evaluation_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="😍 Класс!", callback_data="support_eval:5"),
            InlineKeyboardButton(text="👍 Норм", callback_data="support_eval:4"),
        ],
        [
            InlineKeyboardButton(text="😐 Средне", callback_data="support_eval:3"),
            InlineKeyboardButton(text="👎 Фигня", callback_data="support_eval:2"),
        ],
        [InlineKeyboardButton(text="😡 Злой", callback_data="support_eval:1")]
    ])

    # Сохраняем текущее состояние для пользователя
    user_support_state[user_id] = {"chat_id": chat_id, "message_id": message_id}

    # Пересылаем сообщение администратору
    if ADMIN_GROUP_ID:
        await bot.forward_message(chat_id=ADMIN_GROUP_ID, from_chat_id=chat_id, message_id=query.message.message_id)

        # Сообщение админу
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Вопрос от пользователя {query.from_user.full_name} (ID: {user_id}). Ответьте на это сообщение, чтобы пользователь получил ваш ответ.",
            reply_to_message_id=query.message.message_id,
        )

        # Сообщение пользователю
        await query.message.edit_text(
            "Ваш запрос отправлен в поддержку. Ожидайте ответа.",
        )
    else:
        await query.message.edit_text("Не удалось отправить запрос в поддержку. Обратитесь к администратору.")


@dp.callback_query(lambda c: c.data.startswith("support_eval:"))
async def process_support_evaluation(query: types.CallbackQuery):
    """Обработка оценки ответа поддержки."""
    #support_eval:user_id:5
    _, user_id, evaluation = query.data.split(":")
    user_id = int(user_id)
    logger.info("13 2 process_support_evaluation  {user_id=}  {evaluation=} ")
    # Закрываем диалог
    await query.message.edit_text(f"Спасибо за вашу оценку! Вы оценили ответ поддержки на {evaluation} из 5.")

    # Удаляем состояние пользователя
    if user_id in user_support_state:
        del user_support_state[user_id]


# Активация курса по кодовому слову. Записывает пользователя на курс
@dp.message(Command("activate"))
async def cmd_activate(message: Message):
    """Handler for the /activate command to activate a course"""
    user_id = message.from_user.id
    logger.info(f"del del del cmd_activate User {user_id} initiated activation process.")

    await message.answer(escape_md(
        "🔑 *Активация курса*\n\n"
        "Введите кодовое слово для активации курса.\n"
        "Для отмены введите /cancel."),
        parse_mode="MarkdownV2"
    )



@dp.callback_query(F.data == "menu_mycourses") #08-04 Предоставляет кнопки для продолжения или повторного просмотра
@db_exception_handler  # Показывает список активных и завершенных курсов # Разделяет курсы на активные и завершенные
async def cmd_mycourses_callback(query: types.CallbackQuery):
    """Показывает список активных и завершенных курсов."""
    user_id = query.from_user.id
    logger.info("12 cmd_mycourses_callback  {user_id=}   ")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Get active courses
            cursor = await conn.execute("""
                SELECT c.title, uc.course_id FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ? AND uc.status = 'active'
            """, (user_id,))
            active_courses = await cursor.fetchall()

            # Get completed courses
            cursor = await conn.execute("""
                SELECT c.title, uc.course_id FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ? AND uc.status = 'completed'
            """, (user_id,))
            completed_courses = await cursor.fetchall()

        # Building text response
        response_text = ""
        if active_courses:
            response_text += "<b>Активные курсы:</b>\n"
            response_text += "\n".join([f"- {title}" for title, course_id in active_courses]) + "\n\n"
        if completed_courses:
            response_text += "<b>Завершенные курсы:</b>\n"
            response_text += "\n".join([f"- {title}" for title, course_id in completed_courses])

        if not active_courses and not completed_courses:
            response_text = "У вас нет активных или завершенных курсов."

        await query.message.edit_text(response_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in cmd_mycourses: {e}")
        await query.answer("Произошла ошибка при обработке запроса.", show_alert=True)


@dp.message(Command("completed_courses"))  # Показывает список завершенных курсов # Реализует пагинацию уроков
@db_exception_handler  # Позволяет просматривать уроки с сниппетами
async def cmd_completed_courses(message: Message):
    user_id = message.from_user.id
    logger.info(f"5560 cmd_completed_courses {user_id=} ")
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT c.course_id, c.title 
            FROM user_courses uc
            JOIN courses c ON uc.course_id = c.course_id
            WHERE uc.user_id = ? AND uc.is_completed = 1
            ORDER BY uc.activation_date DESC
        """, (user_id,))
        courses = await cursor.fetchall()

    if not courses:
        await message.answer("У вас нет завершенных курсов.")
        return

    keyboard = InlineKeyboardMarkup(row_width=1)
    for course_id, title in courses:
        keyboard.add(InlineKeyboardButton(
            text=escape_md(title),
            callback_data=f"view_completed_course:{course_id}"
        ))

    await message.answer(escape_md("📚 *Завершенные курсы:*"),
                         reply_markup=keyboard,
                         parse_mode="MarkdownV2")  # Позволяет просматривать уроки со сниппетами

# 11-04
@dp.callback_query(CourseCallback.filter(F.action == "menu_cur"))
async def show_lesson_content(callback_query: types.CallbackQuery, callback_data: CourseCallback):
    """Отображает текущий урок с динамическим меню"""
    logger.info("show_lesson_content: Callback получен!")
    user = callback_query.from_user
    user_id = user.id
    first_name = user.first_name or "Пользователь"
    logger.info(f"15 show_lesson_content из menu_current_lesson = menu_cur    {first_name} {user_id} ")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            course_id = callback_data.course_id
            # lesson_num = callback_data.lesson_num # удаляем эту строчку

            logger.info(f"77 show_lesson_content {course_id=} ")

            # current_lesson из базы
            cursor = await conn.execute("""
                    SELECT current_lesson 
                    FROM user_courses 
                    WHERE user_id = ? AND course_id = ?
                """, (user_id, course_id))
            current_lesson = (await cursor.fetchone())[0]
            if current_lesson:
                lesson_num = current_lesson
            else:
                logger.error(f"800 Пустой урок: {course_id} урок {current_lesson}")
                lesson_num = 1

            logger.info(f"15 show_lesson_content {course_id=} {lesson_num=} ")
            # Получаем контент урока
            cursor = await conn.execute("""
                SELECT text, content_type, file_id, is_homework, hw_type
                FROM group_messages 
                WHERE course_id = ? AND lesson_num = ?
            """, (course_id, lesson_num))

            lesson_content = await cursor.fetchall()

            if not lesson_content:
                logger.error(f"900 Пустой уоньТент урока: {course_id} урок {lesson_num}")
                await callback_query.answer("📭 Урок пуст")
                return

            ka="домашки нет"
            # Отправка контента с микрозадержкой
            for text, content_type, file_id, is_homework, hw_type in lesson_content:
                logger.info(f"\nrow: {text=} | {content_type=} | {file_id=} {is_homework=}, {hw_type=}")
                if is_homework:
                    if hw_type == "photo":
                        ka= "📸 Отправьте фото для домашнего задания"
                    elif hw_type == "text":
                        ka="📝 Отправьте текст для домашнего задания"
                    elif hw_type == "video":
                        ka= "📹 Отправьте видео для домашнего задания"
                    elif hw_type == "any":
                        ka= "📹 Отправьте для домашнего задания что угодно"
                    ka+=". и ждите следующий урок"
                if content_type == "video" and file_id:
                    await bot.send_video(user_id, video=file_id, caption=text or None)
                elif content_type == "photo" and file_id:
                    await bot.send_photo(user_id, photo=file_id, caption=text or None)
                elif content_type == "document" and file_id:
                    await bot.send_document(user_id, document=file_id, caption=text or None)
                elif content_type == "audio" and file_id:
                    await bot.send_audio(user_id, audio=file_id, caption=text or None)
                elif text:
                    await bot.send_message(user_id, text=text)

                await asyncio.sleep(0.3)  # Антифлуд

            # Получаем данные активного курса пользователя из user_courses
            cursor = await conn.execute("""
                SELECT 
                    uc.version_id,
                    c.title AS course_name,
                     uc.current_lesson
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ? AND uc.course_id = ? AND uc.status = 'active'
            """, (user_id, course_id))
            user_data = await cursor.fetchone()

            if not user_data:
                await callback_query.answer("❌ Активный курс не найден")
                return

            version_id, course_name, current_lesson = user_data

            # Формируем текст кнопки с количеством
            total_courses = len(settings["activation_codes"])  # общее кол-во курсов
            courses_button_text = f"📚 Мои курсы ({total_courses})"

            # Получаем тариф
            tariff_names = settings.get("tariff_names", {"v1": "Соло", "v2": "Группа", "v3": "VIP"})
            user_tariff = tariff_names.get(version_id, "Базовый")

            # Формируем прогресс
            total_lessons_cursor = await conn.execute("""
                SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ?
            """, (course_id,))
            total_lessons = (await total_lessons_cursor.fetchone())[0]

            lesson_progress = (
                f"\n📊 Прогресс: {current_lesson}/{total_lessons} уроков"
                f"\n✅ Последний пройденный: урок {current_lesson}" if current_lesson else ""
            )

            # Генерация клавиатуры
            homework_pending = await check_homework_pending(user_id, course_id, current_lesson)
            keyboard = get_main_menu_inline_keyboard(
                course_id=course_id,
                lesson_num=current_lesson,
                user_tariff=version_id,
                homework_pending=homework_pending,
                courses_button_text=courses_button_text
            )
            logger.info(f"15554 запишем в базу  current_lesson{current_lesson}  ")

            if current_lesson==0:
                current_lesson=1
                logger.info(f"1554 запишем в базу  current_lesson{1}  ")
                await conn.execute("""
                    UPDATE user_courses 
                    SET current_lesson = ?
                    WHERE user_id = ? AND course_id = ?
                    """, (1, user_id, course_id))
                await conn.commit()
            message = (
                f"{ka}, {first_name}!\n\n"
                f"🎓 Курс: {course_name}\n"
                f"🔑 Тариф: {user_tariff}\n"
                f"📚 Текущий урок: {current_lesson}"
                f"{lesson_progress}"
            )
        if current_lesson == total_lessons:
            await bot.send_message(user_id, "🎉 Вы прошли все уроки курса!")
            await callback_query.message.delete() # окончание всего курса todo продумывать

        await bot.send_message(user_id, message, reply_markup=keyboard)
        await callback_query.answer()

    except Exception as e:
        logger.error(f"Ошибка в show_lesson_content: {str(e)}", exc_info=True)
        await callback_query.answer("⚠️ Произошла ошибка. Попробуйте позже")
        await bot.send_message(
            ADMIN_GROUP_ID,
            f"🚨 Ошибка у @{user.username}: {str(e)}"
        )



@dp.callback_query(F.data == "menu_progress")
@db_exception_handler # Обработчик для команды просмотра прогресса по всем курсам
async def cmd_progress_callback(query: types.CallbackQuery):
    """Показывает прогресс пользователя по курсам."""
    user_id = query.from_user.id
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Fetch all courses the user is enrolled in
            cursor = await conn.execute("""
                SELECT uc.course_id, c.title, uc.current_lesson
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ?
            """, (user_id,))
            courses = await cursor.fetchall()

            if not courses:
                await query.answer("Вы не записаны ни на один курс.", show_alert=True)
                return

            progress_text = ""
            for course_id, course_title, current_lesson in courses:
                # Fetch total number of lessons for this course
                cursor = await conn.execute("""
                    SELECT COUNT(DISTINCT lesson_num) 
                    FROM group_messages WHERE group_id = ?
                """, (course_id,))
                total_lessons = (await cursor.fetchone())[0]
                progress_text += f"<b>{course_title}:</b>\n"
                progress_text += f"  Пройдено {current_lesson} из {total_lessons} уроков.\n"

            await query.message.edit_text(progress_text, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Error in cmd_progress: {e}")
        await query.answer("Произошла ошибка при получении прогресса.", show_alert=True)


@dp.message(Command("homework"))
@db_exception_handler  # пользователь домашку сдаёт
async def cmd_homework(message: types.Message):
    """    Allows user to submit homework    """
    user_id = message.from_user.id

    # Получаем данные пользователя и курса из базы данных
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT uc.course_id, uc.version_id, uc.current_lesson
            FROM user_courses uc
            WHERE uc.user_id = ?
        """, (user_id,))
        user_course_data = await cursor.fetchone()

    if not user_course_data:
        await message.answer("У вас нет активных курсов. Активируйте курс с помощью команды /activate")
        return

    course_id, version_id, current_lesson = user_course_data

    # Определяем тип проверки домашки
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT homework_check_type
            FROM course_versions
            WHERE course_id = ? AND version_id = ?
        """, (course_id, version_id))
        homework_check_type = await cursor.fetchone()
    logger.info(f"{homework_check_type=}")

    if homework_check_type is None:
        logger.warning(f"Не найдена информация о версии курса для {user_id=}")
        await message.answer("Произошла ошибка при определении типа проверки домашки. Обратитесь к администратору")
        return

    homework_check_type = homework_check_type[0]
    logger.info(f"{homework_check_type=}")

    # Если homework_check_type == 'admin', то отправляем message админам
    if homework_check_type != 'admin':
        await message.answer(
            "Ваш тариф не предполагает проверку домашних заданий администратором. Вы можете выполнить задание для себя.")
        return
    else:
        # Пересылка сообщения администраторам
        await bot.forward_message(ADMIN_GROUP_ID, message.chat.id, message.message_id)

        await message.answer("Ваше домашнее задание отправлено на проверку администраторам!")


@dp.message(Command("select_course"))
@db_exception_handler
async def select_course(message: Message):
    user_id = message.from_user.id
    args = message.text.split()[1:]  # Получаем аргументы команды
    logger.info(f"select_course {user_id=}")
    if not args:
        return await message.reply("Использование: /select_course <course_id>")

    course_id = args[0]

    async with aiosqlite.connect(DB_FILE) as conn:
        # Проверяем, что курс существует
        cursor = await conn.execute("SELECT title FROM courses WHERE course_id = ?", (course_id,))
        if not (course := await cursor.fetchone()):
            return await message.reply("Курс не найден.")

        # Проверяем регистрацию пользователя на курс
        cursor = await conn.execute(
            "SELECT 1 FROM user_courses WHERE user_id = ? AND course_id = ?", (user_id, course_id)
        )
        if not await cursor.fetchone():
            return await message.reply("Вы не зарегистрированы на этот курс.")

        # Обновляем текущий курс в user_states
        await conn.execute(
            """
            INSERT INTO user_states (user_id, current_course_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET current_course_id = excluded.current_course_id
            """,
            (user_id, course_id),
        )
        await conn.commit()

    await message.reply(f"Вы выбрали курс: {course[0]}")



@dp.callback_query(lambda c: c.data.startswith("submit_homework:"))
@db_exception_handler  # обработка отправки ДЗ
async def submit_homework_callback(callback_query: CallbackQuery, course_id, lesson_num):
    """Handle submit homework button callback"""
    user_id = callback_query.from_user.id
    logger.info(f"submit_homework_callback {user_id=} ")

    # Сохраняем контекст (курс и урок) для последующей обработки
    async with aiosqlite.connect(DB_FILE) as conn:
        # Проверяем статус предыдущего домашнего задания
        cursor = await conn.execute("""
            SELECT status FROM homework 
            WHERE user_id = ? AND course_id = ? AND lesson_num = ?
        """, (user_id, course_id, lesson_num))
        prev_status = await cursor.fetchone()

        if prev_status and prev_status[0] in ['pending', 'rejected']:
            # Если есть предыдущее ДЗ со статусом "pending" или "rejected",
            # обновляем его вместо создания новой записи
            await conn.execute("""
                UPDATE homework SET message_id = NULL, status = 'pending', 
                submission_date = CURRENT_TIMESTAMP, admin_id = NULL, rejection_reason = NULL
                WHERE user_id = ? AND course_id = ? AND lesson_num = ?
            """, (user_id, course_id, lesson_num))
        else:
            # Если нет предыдущего ДЗ, создаем новую запись
            await conn.execute("""
                INSERT INTO homework(user_id, course_id, lesson_num, status)
                VALUES (?, ?, ?, 'pending')
            """, (user_id, course_id, lesson_num))

        await conn.commit()

    # Отправляем сообщение пользователю с инструкцией
    await callback_query.message.edit_text(
        "📝 Пожалуйста, отправьте ваше домашнее задание.\n"
        "Вы можете отправить текст, фото, видео или документ.\n"
        "Для отмены введите /cancel."
    )



@dp.callback_query(lambda c: c.data.startswith("review_prev:") or c.data.startswith("review_next:"))
@db_exception_handler  # пользователь просто лазит по урокам в свободном режиме
async def review_navigation_callback(callback_query: CallbackQuery):
    action, course_id, current_lesson = callback_query.data.split(":")
    user_id = callback_query.from_user.id
    current_lesson = int(current_lesson)
    logger.info(f"review_navigation_callback {user_id=} {current_lesson=}")
    async with aiosqlite.connect(DB_FILE) as conn:
        if action == "review_prev":
            cursor = await conn.execute("""
                SELECT MAX(lesson_num) FROM lesson_content_map
                WHERE course_id = ? AND lesson_num < ?
            """, (course_id, current_lesson))
        else:
            cursor = await conn.execute("""
                SELECT MIN(lesson_num) FROM lesson_content_map
                WHERE course_id = ? AND lesson_num > ?
            """, (course_id, current_lesson))
        new_lesson = await cursor.fetchone()

    if not new_lesson or not new_lesson[0]:
        await callback_query.answer("Вы достигли конца списка уроков.")
        return

    # Перенаправляем на просмотр нового урока
    await review_lesson_callback(
        CallbackQuery(
            id=callback_query.id,
            from_user=callback_query.from_user,
            chat_instance=callback_query.chat_instance,
            data=f"review_lesson:{course_id}:{new_lesson[0]}"
        )
    )


@dp.callback_query(lambda c: c.data.startswith("review_lesson:"))
@db_exception_handler  # пользователь пользователь хочет просмотреть определенный урок
async def review_lesson_callback(callback_query: CallbackQuery):
    _, course_id, lesson_num = callback_query.data.split(":")
    user_id = callback_query.from_user.id
    lesson_num = int(lesson_num)
    logger.info(f"review_lesson_callback {user_id=} {lesson_num=}")

    # Получаем данные урока
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT c.group_id, lcm.start_message_id, lcm.end_message_id
            FROM courses c
            JOIN lesson_content_map lcm ON c.course_id = lcm.course_id
            WHERE lcm.course_id = ? AND lcm.lesson_num = ?
        """, (course_id, lesson_num))
        lesson_data = await cursor.fetchone()

    if not lesson_data:
        await callback_query.answer("Урок не найден.")
        return

    group_id, start_id, end_id = lesson_data

    # Отправляем урок пользователю
    await callback_query.answer("3345 Отправка урока...")
    for msg_id in range(start_id, end_id + 1):
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=group_id,
                message_id=msg_id
            )
            await asyncio.sleep(0.2)
        except Exception as e:
            logger.error(f"3346 Error sending message {msg_id} to user {user_id}: {e}")

    # Создаем клавиатуру для навигации
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="⬅️ Предыдущий урок", callback_data=f"review_prev:{course_id}:{lesson_num}"),
        InlineKeyboardButton(text="Следующий урок ➡️", callback_data=f"review_next:{course_id}:{lesson_num}")
    )
    keyboard.add(InlineKeyboardButton(text="📋 Список уроков", callback_data=f"review_course:{course_id}"))

    await bot.send_message(
        user_id,
        "Вы можете продолжить просмотр других уроков:",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data.startswith("view_completed_course:"))
@db_exception_handler
async def view_completed_course(callback_query: CallbackQuery):
    _, course_id = callback_query.data.split(":")
    user_id = callback_query.from_user.id
    logger.info(f"5561 view_completed_course {user_id=} {course_id=} ")
    page = int(callback_query.data.split(":")[-1]) if ":" in callback_query.data else 1

    async with aiosqlite.connect(DB_FILE) as conn:
        # Get course info
        cursor = await conn.execute("""
            SELECT title FROM courses WHERE course_id = ?
        """, (course_id,))
        course_title = (await cursor.fetchone())[0]

        # Get lessons with snippets
        cursor = await conn.execute("""
            SELECT lesson_num, snippet 
            FROM lesson_content_map 
            WHERE course_id = ?
            ORDER BY lesson_num
            LIMIT ? OFFSET ?
        """, (course_id, MAX_LESSONS_PER_PAGE, (page - 1) * MAX_LESSONS_PER_PAGE))
        lessons = await cursor.fetchall()

        # Count total lessons
        cursor = await conn.execute("""
            SELECT COUNT(*) FROM lesson_content_map WHERE course_id = ?
        """, (course_id,))
        total_lessons = (await cursor.fetchone())[0]

    keyboard = InlineKeyboardMarkup(row_width=2)
    for lesson_num, snippet in lessons:
        keyboard.add(InlineKeyboardButton(
            text=f"Урок {lesson_num}",
            callback_data=f"view_completed_lesson:{course_id}:{lesson_num}"
        ))
        keyboard.add(InlineKeyboardButton(
            text=snippet[:50] + "...",
            callback_data=f"view_completed_lesson:{course_id}:{lesson_num}"
        ))

    # Pagination buttons
    pagination = []
    if page > 1:
        pagination.append(InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"view_completed_course:{course_id}:{page - 1}"
        ))
    if (page * MAX_LESSONS_PER_PAGE) < total_lessons:
        pagination.append(InlineKeyboardButton(
            text="Вперед ➡️",
            callback_data=f"view_completed_course:{course_id}:{page + 1}"
        ))
    if pagination:
        keyboard.row(*pagination)

    keyboard.add(InlineKeyboardButton(
        text="🔙 К списку курсов",
        callback_data="cmd_completed_courses"
    ))

    await callback_query.message.edit_text(
        f"📚 *Курс: {course_title}*\n\nВыберите урок:",
        reply_markup=keyboard,
        parse_mode="MarkdownV2"
    )

@dp.callback_query(lambda c: c.data.startswith("view_completed_lesson:"))
@db_exception_handler
async def view_completed_lesson(callback_query: CallbackQuery):
    _, course_id, lesson_num = callback_query.data.split(":")
    user_id = callback_query.from_user.id
    logger.info(f"view_completed_lesson {user_id=} ")
    async with aiosqlite.connect(DB_FILE) as conn:
        # Get lesson details
        cursor = await conn.execute("""
            SELECT c.group_id, lcm.start_message_id, lcm.snippet
            FROM courses c
            JOIN lesson_content_map lcm ON c.course_id = lcm.course_id
            WHERE c.course_id = ? AND lcm.lesson_num = ?
        """, (course_id, lesson_num))
        lesson_data = await cursor.fetchone()

    if not lesson_data:
        await callback_query.answer("Урок не найден.")
        return

    group_id, start_id, snippet = lesson_data

    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(
            text="Показать весь урок 📚",
            callback_data=f"show_full_lesson:{course_id}:{lesson_num}"
        ),
        InlineKeyboardButton(
            text="🔙 К списку уроков",
            callback_data=f"view_completed_course:{course_id}:1"
        )
    )

    # Send only the first message and snippet
    try:
        await bot.copy_message(
            chat_id=user_id,
            from_chat_id=group_id,
            message_id=start_id
        )
        await asyncio.sleep(0.5)
    except Exception as e:
        logger.error(f"Error sending first message of lesson {lesson_num}: {e}")

    await callback_query.message.edit_text(
        f"📖 *Урок {lesson_num}*\n\n{snippet}",
        reply_markup=keyboard,
        parse_mode="MarkdownV2"
    )

@dp.callback_query(lambda c: c.data.startswith("show_full_lesson:"))
@db_exception_handler
async def show_full_lesson(callback_query: CallbackQuery):
    _, course_id, lesson_num = callback_query.data.split(":")
    user_id = callback_query.from_user.id
    logger.info(f"77777777show_full_lesson {user_id=} ")
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT c.group_id, lcm.start_message_id, lcm.end_message_id
            FROM courses c
            JOIN lesson_content_map lcm ON c.course_id = lcm.course_id
            WHERE c.course_id = ? AND lcm.lesson_num = ?
        """, (course_id, lesson_num))
        lesson_data = await cursor.fetchone()

    if not lesson_data:
        await callback_query.answer("Урок не найден.")
        return

    group_id, start_id, end_id = lesson_data

    for msg_id in range(start_id, end_id + 1):
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=group_id,
                message_id=msg_id
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error sending message {msg_id} of lesson {lesson_num}: {e}")

    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(
            text="🔙 К списку уроков",
            callback_data=f"view_completed_course:{course_id}:1"
        )
    )

    await bot.send_message(
        user_id,
        "✅ Урок полностью показан.",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data.startswith("review_course:"))
@db_exception_handler
async def review_course_callback(callback_query: CallbackQuery):
    _, course_id = callback_query.data.split(":")
    user_id = callback_query.from_user.id
    logger.info(f"review_course_callback {user_id=} ")
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT lcm.lesson_num, c.group_id, lcm.start_message_id, lcm.end_message_id
            FROM lesson_content_map lcm
            JOIN courses c ON lcm.course_id = c.course_id
            WHERE lcm.course_id = ?
            ORDER BY lcm.lesson_num
        """, (course_id,))
        lessons = await cursor.fetchall()

    if not lessons:
        await callback_query.answer("Материалы курса не найдены.")
        return

    # Создаем меню с уроками для повторного просмотра
    keyboard = InlineKeyboardMarkup(row_width=1)
    for lesson_num, group_id, start_id, end_id in lessons:
        keyboard.add(InlineKeyboardButton(
            text=f"Урок {lesson_num}",
            callback_data=f"review_lesson:{course_id}:{lesson_num}"
        ))

    await callback_query.message.edit_text(
        "Выберите урок для повторного просмотра:",
        reply_markup=keyboard
    )

# ==================== это пользователь код вводит=========================================

# функция для активации курса
@dp.message(lambda message: message.text and message.text.lower() in settings["activation_codes"])
@db_exception_handler
async def activate_course(message: types.Message):
    """Активирует курс и отображает динамическое меню с учетом общего количества курсов"""
    code = message.text.lower()
    user_id = message.from_user.id
    logger.info(f"Activation attempt: {code=} by {user_id}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверка кода активации
            cursor = await conn.execute("""
                SELECT cac.course_id, cac.version_id 
                FROM course_activation_codes cac
                WHERE cac.code_word = ?
            """, (code,))
            result = await cursor.fetchone()

            if not result:
                await message.answer("❌ Код активации не найден")
                return

            course_id, version_id = result
            logger.info(f"Found code: {course_id=}, {version_id=}")

            # Получаем общее количество курсов пользователя
            cursor = await conn.execute("""
                SELECT COUNT(*) 
                FROM user_courses 
                WHERE user_id = ? AND status IN ('active', 'completed')
            """, (user_id,))
            total_courses = (await cursor.fetchone())[0]

            # Активация курса
            await conn.execute("""
                INSERT OR REPLACE INTO user_courses 
                (user_id, course_id, version_id, status) 
                VALUES (?, ?, ?, 'active')
            """, (user_id, course_id, version_id))
            await conn.commit()

            # Выводим 0 урок - описание курса
            await send_course_description(user_id, course_id)

            # Получаем информацию о курсе
            cursor = await conn.execute("""
                SELECT title FROM courses WHERE course_id = ?
            """, (course_id,))
            course_name = (await cursor.fetchone())[0]

            # Формируем текст кнопки с количеством курсов
            courses_button_text = f"📚 Мои курсы ({total_courses + 1})"  # +1 для нового курса

            # Генерация клавиатуры
            homework_pending = await check_homework_pending(user_id, course_id, 0)
            keyboard = get_main_menu_inline_keyboard(
                course_id=course_id,
                lesson_num=0,
                user_tariff=version_id,
                homework_pending=homework_pending,
                courses_button_text=courses_button_text  # Динамический текст кнопки
            )

            # Формирование ответа
            tariff_names = settings.get("tariff_names", {
                "v1": "Соло",
                "v2": "Группа",
                "v3": "VIP"
            })
            response = (
                f"✅ Курс активирован!\n\n"
                f"🎓 Курс: {course_name}\n"
                f"🔑 Тариф: {tariff_names.get(version_id, 'Базовый')}\n"
                f"📚 Текущий урок: 1"
            )

            await message.answer(response, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка активации: {str(e)}", exc_info=True)
        await message.answer("⛔ Произошла ошибка при активации курса")


# ==================== домашка фотка==================

@dp.message(F.content_type.in_({'photo', 'document'}))
@db_exception_handler
async def handle_homework(message: types.Message):
    """Обрабатывает отправку домашних заданий (фото/документы)"""
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    logger.info(f"300 handle_homework {user_id=} {user_name=}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем данные курса и тарифа
            cursor = await conn.execute("""
                SELECT 
                    uc.course_id, 
                    uc.current_lesson, 
                    uc.version_id,
                    c.title AS course_name, 
                    cv.title AS version_name,
                    uc.status,
                    uc.hw_status,
                    uc.last_lesson_sent_time
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                JOIN course_versions cv ON uc.course_id = cv.course_id AND uc.version_id = cv.version_id
                WHERE uc.user_id = ? AND uc.status = 'active'
            """, (user_id,))
            user_course_data = await cursor.fetchone()

            if not user_course_data:
                await message.answer("❌ У вас нет активных курсов. Просто введите код активации.")
                return

            course_id, current_lesson, version_id, course_name, version_name, status, hw_status, last_lesson_sent_time = user_course_data
            tariff_names = settings.get("tariff_names", {"v1": "Соло", "v2": "Группа", "v3": "VIP"})
            tariff_name = tariff_names.get(version_id, "Базовый")
            message_interval = settings.get("message_interval", 24)
            if last_lesson_sent_time:
                try:
                    last_sent = datetime.strptime(last_lesson_sent_time, '%Y-%m-%d %H:%M:%S')
                    next_lesson_time = last_sent + timedelta(hours=message_interval)
                    time_left = next_lesson_time - datetime.now()
                    hours = time_left.seconds // 3600
                    minutes = (time_left.seconds % 3600) // 60
                    time_message = f"Следующий урок будет доступен через {hours} ч. {minutes} мин.\n"
                except ValueError as ve:
                    logger.error(f"⚠️ Ошибка преобразования времени: {ve}")
                    time_message = "⚠️ Не удалось определить время следующего урока.\n"
            else:
                time_message = "✅ Это ваш первый урок! Следующий будет доступен через некоторое время.\n"

            if version_id == 'v1':
                # Если тариф "Соло", сразу принимаем ДЗ
                # Обновляем статус ДЗ в базе данных
                await conn.execute("""
                    UPDATE user_courses 
                    SET hw_status = 'approved' 
                    WHERE user_id = ? AND course_id = ? AND version_id = ?
                """, (user_id, course_id, version_id))
                # Добавляем ДЗ в галерею
                if message.content_type == 'photo':
                    file_id = message.photo[-1].file_id
                else:
                    file_id = message.document.file_id
                await conn.execute("""
                    INSERT INTO homework_gallery 
                    (user_id, course_id, lesson_num, message_id, approved_by)
                    VALUES (?, ?, ?, ?, ?)
                """, (user_id, course_id, current_lesson, file_id, user_id))  #  approved_by = user_id - как self-approved
                await conn.commit()

                keyboard = get_main_menu_inline_keyboard( # await убрали
                    course_id=course_id,
                    lesson_num=current_lesson,
                    user_tariff=version_id,
                    homework_pending=False,
                    courses_button_text=f"📚 Мои курсы"  # Убрали счетчик, пока не разберемся
                )

                await message.answer(
                    f"🎉 Отлично! Домашнее задание принято и добавлено в галерею.\n"
                    f"🎓 Курс: {course_name}\n"
                    f"🔑 Тариф: {tariff_name}\n"
                    f"📚 Текущий урок: {current_lesson}\n\n"
                    f"{time_message}",
                    reply_markup=keyboard
                )
                return

            # Обработка для других тарифов
            admin_message = f"📬 Новое ДЗ от @{message.from_user.username} ({user_name})\n..."

            # Отправка контента в админскую группу
            if message.content_type == 'photo':
                sent_message = await bot.send_photo(ADMIN_GROUP_ID, message.photo[-1].file_id, admin_message)
            else:
                sent_message = await bot.send_document(ADMIN_GROUP_ID, message.document.file_id, admin_message)

            await conn.execute("""
                INSERT INTO homework_gallery 
                (user_id, course_id, lesson_num, message_id, approved_by)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, course_id, current_lesson, sent_message.message_id, 0))
            await conn.commit()

            # Обновление клавиатуры
            keyboard =  get_main_menu_inline_keyboard( # и тут убрали await
                course_id=course_id,
                lesson_num=current_lesson,
                user_tariff=version_id,
                homework_pending=True,
                courses_button_text=f"📚 Мои курсы"  # Убрали счетчик, пока не разберемся
            )

            await message.answer(
                f"✅ Домашка на проверке! Спасибо!\n"
                f"🎓 Курс: {course_name}\n"
                f"🔑 Тариф: {tariff_name}\n"
                f"📚 Текущий урок: {current_lesson}",
                f"{time_message}",
                reply_markup=keyboard
            )

    except Exception as e:
        logger.error(f"Ошибка отправки ДЗ: {e}", exc_info=True)
        await message.answer("⚠️ Ошибка при отправке. Попробуйте позже")
        await bot.send_message(ADMIN_GROUP_ID, f"🚨 Ошибка ДЗ от @{message.from_user.username}: {str(e)}")


#======================Конец обработчиков слов и хэндлеров кнопок=========================================

@dp.message(F.text)
@db_exception_handler
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()
    logger.info(f"37 handle_text  {text=} {user_id=}")
    # Проверяем, есть ли у пользователя активный курс
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT course_id FROM user_courses WHERE user_id = ? AND status = 'active'", (user_id,))
        active_course = await cursor.fetchone()

    if active_course:  # Если курс активен → текст считаем ДЗ
        await handle_text_homework(message)
    else:  # Если нет курса → текст считаем кодом активации
        await handle_activation_code(message)


async def handle_text_homework(message: types.Message):
    user_id = message.from_user.id
    text = message.text
    logger.info(f"66 handle_text_homework  {text=}")
    # Получаем данные о курсе
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT course_id, current_lesson, version_id 
            FROM user_courses 
            WHERE user_id = ? AND status = 'active'
        """, (user_id,))
        user_course_data = await cursor.fetchone()

    if not user_course_data:
        return

    course_id, current_lesson, version_id = user_course_data

    # Если тариф v1 → самопроверка
    if version_id == 'v1':
        await message.answer("✅ Текстовая домашка принята для самопроверки!")
        return

    # Отправляем админам
    admin_message = (
        f"📝 Текстовая домашка от @{message.from_user.username}\n"
        f"Курс: {course_id}\nУрок: {current_lesson}\n\n"
        f"{text}"
    )
    await bot.send_message(ADMIN_GROUP_ID, admin_message)
    await message.answer("✅ Текстовая домашка на проверке!")

# Обработчик последний - чтобы не мешал другим обработчикам работать. Порядок имеет значение
@dp.message(F.text)  # Фильтр только для текстовых сообщений
async def handle_activation_code(message: types.Message): # handle_activation_code process_message
    """Проверяет код активации и выдаёт уроки, если всё окей"""
    user_id = message.from_user.id
    code = message.text.strip().lower()  # Приводим к нижнему регистру
    logger.info(f"7 process_message Проверяем код: {code}")
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
            return await message.answer("Неверное кодовое слово. Попробуйте еще раз или свяжитесь с поддержкой.")

        course_id, version_id, course_name = course_data

        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверим, не активирован ли уже этот курс
            cursor = await conn.execute("""
                SELECT 1 FROM user_courses
                WHERE user_id = ? AND course_id = ?
            """, (user_id, course_id))
            existing_enrollment = await cursor.fetchone()

            if existing_enrollment:
                await message.answer("Этот курс уже активирован.")
                # Load 0 lesson
                await send_course_description(user_id, course_id)

                # Generate keyboard
                keyboard = get_main_menu_inline_keyboard(
                    course_id=course_id,
                    lesson_num=0,  # Для описания курса ставим урок 0
                    user_tariff=version_id,
                    homework_pending=False
                )
                await message.answer("Главное меню:", reply_markup=keyboard)
            else:
                # Активируем курс
                await conn.execute("""
                    INSERT OR REPLACE INTO user_courses (user_id, course_id, version_id, status, current_lesson, activation_date)
                    VALUES (?, ?, ?, 'active', 1, CURRENT_TIMESTAMP)
                """, (user_id, course_id, version_id))
                await conn.commit()
                await log_user_activity(user_id, "COURSE_ACTIVATION",
                                        f"Курс {course_id} активирован с кодом {message.text.strip()}")

                # Load 0 lesson
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

            # Генерация клавиатуры
            keyboard = get_main_menu_inline_keyboard(
                course_id=course_id,
                lesson_num=0,  # Для описания курса ставим урок 0
                user_tariff=version_id,
                homework_pending=False,
                courses_button_text=courses_button_text
            )

            # Отправляем сообщение
            tariff_names = settings.get("tariff_names", {"v1": "Соло", "v2": "Группа", "v3": "VIP"})
            message_text = (
                f"Курс успешно активирован!\n"
                f"🎓 Курс: {course_name}\n"
                f"🔑 Тариф: {tariff_names.get(version_id, 'Базовый')}\n"
                f"📚 Текущий урок: 1"
            )
        await message.answer(message_text, reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Общая ошибка в process_message: {e}", exc_info=True)
        await message.answer("Произошла общая ошибка. Пожалуйста, попробуйте позже.")


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
                await message.answer("Текст не относится к текущему уроку, проигнорировано.")
            else:
                # Если статус ДЗ не 'required' или 'rejected', или это не текст - обрабатываем как обычно
                await handle_homework(message)

    except Exception as e:
        logger.error(f"Ошибка при обработке контента: {e}")
        await message.answer("Произошла ошибка при обработке вашего сообщения.")

#=======================Конец обработчиков текстовых сообщений=========================================

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    """Обработчик для фотографий."""
    logger.info(f"88 handle_photo  ")
    try:
        await message.answer("Фотография получена!")
    except Exception as e:
        logger.error(f"Ошибка при обработке фотографии: {e}")

@dp.message(F.video)
async def handle_video(message: types.Message):
    """Обработчик для видео."""
    logger.info(f"89 handle_video  ")
    try:
        await message.answer("Видео получено!")
    except Exception as e:
        logger.error(f"Ошибка при обработке видео: {e}")

@dp.message(F.document)
async def handle_document(message: types.Message):
    """Обработчик для документов."""
    logger.info(f"90 handle_document  ")
    try:
        await message.answer("Документ получен!")
    except Exception as e:
        logger.error(f"Ошибка при обработке документа: {e}")


@dp.message()
async def default_handler(message: types.Message):
    logger.warning(f"Получено необработанное сообщение: {message.text}")

@dp.callback_query()
async def default_callback_handler(query: types.CallbackQuery):
    logger.warning(f"Получен необработанный callback_query: {query.data}")





async def main():
    logger.info("Запуск main()...")
    global settings, COURSE_GROUPS
    # Инициализация базы данных
    await init_db()
    settings = load_settings()  # Загрузка настроек при запуске
    logger.info(f"444 load_settings {settings['groups']=}")

    COURSE_GROUPS = list(map(int, settings.get("groups", {}).keys()))  # load to value too
    logger.info(f"555  {COURSE_GROUPS=}")



    await import_settings_to_db()
    await asyncio.sleep(0.2) # Небольшая задержка перед отправкой сообщения
    await send_startup_message(bot, ADMIN_GROUP_ID)  # Отправка сообщения в группу администраторов
    # asyncio.create_task(check_and_schedule_lessons())

    # Запуск бота
    logger.info(f"Бот успешно запущен.")

    logger.info("пускаем таймеры")
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT user_id FROM users")
        users = await cursor.fetchall()

        for user in users:
            await start_lesson_schedule_task(user[0])

    logger.info("Начинаем dp.start_polling()...")  # Добавлено логирование перед запуском polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())


# Осознание обработчиков:
# @dp.message(Command(...)): Обработчики команд (начинаются с /).
# @dp.message(F.text): Обработчики текстовых сообщений (ловят любой текст).
# @dp.callback_query(lambda c: ...): Обработчики нажатий на кнопки (inline keyboard).
# @dp.message(lambda message: message.text.lower() in settings["activation_codes"]): Обработчик для активации курса по коду.
