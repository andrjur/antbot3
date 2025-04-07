import asyncio,  logging, json, random, string, os, re
import functools
from functools import lru_cache
from logging.handlers import RotatingFileHandler

import aiogram
#from aiogram.utils.text_decorations import escape_md нет в природе. сами напишем
#from aiogram.utils.markdown import quote  # Для MarkdownV2 - todo попробовать
# Или
#from aiogram.utils.text_decorations import html  # Для HTML


from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup,
                           KeyboardButton, Message, CallbackQuery, ChatFullInfo)
# escape_md(your_text). Эта функция экранирует символы <, >, &.
import aiosqlite
from dotenv import load_dotenv

# Загрузка переменных из .env
load_dotenv()

MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 3


def setup_logging():
    """Setup logging with rotation"""
    log_file = 'bot.log'
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(lineno)d - %(message)s  %(levelname)s',
        datefmt='%H:%M:%S',
        handlers=[
            RotatingFileHandler(
                log_file,
                maxBytes=MAX_LOG_SIZE,
                backupCount=LOG_BACKUP_COUNT
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
# Получение LESSONS_CHANNEL_IDS с проверкой
lessons_channel_ids_raw = os.getenv("LESSONS_CHANNEL_IDS", "")
try:
    LESSONS_CHANNEL_IDS = [int(id.strip()) for id in lessons_channel_ids_raw.split(",") if id.strip().isdigit()]
except ValueError:
    logger.critical("LESSONS_CHANNEL_IDS содержит некорректные данные. Убедитесь, что это список чисел, разделенных запятыми.")
    raise ValueError("LESSONS_CHANNEL_IDS содержит некорректные данные.")

SETTINGS_FILE = "settings.json"

DB_FILE = "bot.db"
MAX_LESSONS_PER_PAGE = 7 # пагинация для view_completed_course
DEFAULT_COUNT_MESSAGES = 7 # макс количество сообщений при выводе курсов

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



def load_settings():
    """Загружает настройки из файла settings.json."""
    logger.info(f"333 load_settings ")
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
                logger.info("Настройки успешно загружены.")
                return settings
        except json.JSONDecodeError:
            logger.error("Ошибка при декодировании JSON.")
            return {"channels": {}, "activation_codes": {}}
    else:
        logger.warning("Файл настроек не найден, используются настройки по умолчанию.")
        return {"channels": {}, "activation_codes": {}}

settings = load_settings()  # Загрузка настроек при запуске


def save_settings(settings):
    """Сохраняет настройки в файл settings.json."""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
        logger.info("Настройки успешно сохранены.")
    except Exception as e:
        logger.error(f"Ошибка при сохранении настроек: {e}")



async def import_settings_to_db(settings):
    """Импортирует настройки (каналы и коды активации) из dict в базу данных."""
    logger.info(f"import_settings_to_db with settings from code")

    for channel_id, course_id in settings.get("channels", {}).items():
        # Извлекаем коды для каждой версии курса
        code1 = next((code for code, info in settings["activation_codes"].items() if info == f"{course_id}:v1"), None)
        code2 = next((code for code, info in settings["activation_codes"].items() if info == f"{course_id}:v2"), None)
        code3 = next((code for code, info in settings["activation_codes"].items() if info == f"{course_id}:v3"), None)

        # Вызываем process_add_course_to_db
        await process_add_course_to_db(course_id, channel_id, code1, code2, code3)



# Database initialization
@db_exception_handler
async def init_db():
    """Initialize the database with required tables"""
    logger.info(f"init_db ")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Users table
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT COLLATE NOCASE,
                first_name TEXT COLLATE NOCASE,
                last_name TEXT COLLATE NOCASE,
                is_active INTEGER DEFAULT 1,
                is_banned INTEGER DEFAULT 0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # User profiles with additional info
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id INTEGER PRIMARY KEY,
                username TEXT COLLATE NOCASE,
                first_name TEXT COLLATE NOCASE,
                last_name TEXT COLLATE NOCASE,
                alias TEXT COLLATE NOCASE,
                tokens INTEGER DEFAULT 0,
                referrer_id INTEGER,
                birthday TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''')

            # User states
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_states (
                user_id INTEGER PRIMARY KEY,
                current_course_id TEXT, -- ID текущего курса
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (current_course_id) REFERENCES courses(course_id)
            )
            ''')

            # Courses
            await conn.execute('''
           CREATE TABLE IF NOT EXISTS courses (
                course_id TEXT PRIMARY KEY,
                title TEXT NOT NULL COLLATE NOCASE,
                description TEXT COLLATE NOCASE,
                total_lessons INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                channel_id INTEGER, -- ID Telegram-канала с контентом
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # Course activation codes
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS course_activation_codes (
                code_word TEXT PRIMARY KEY,
                course_id TEXT NOT NULL,
                course_type TEXT NOT NULL,
                price_rub INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (course_id) REFERENCES courses(course_id)
            )
            ''')

            # Course versions (different tiers/packages)
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS course_versions (
                course_id TEXT,
                version_id TEXT,
                title TEXT NOT NULL COLLATE NOCASE,
                price REAL DEFAULT 0,
                activation_code TEXT,
                homework_check_type TEXT DEFAULT 'admin', -- 'admin' или 'self'
                PRIMARY KEY (course_id, version_id),
                FOREIGN KEY (course_id) REFERENCES courses(course_id)
            )
            ''')

            # User courses (enrollments)
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_courses (
                user_id INTEGER,
                course_id TEXT,
                version_id TEXT,
                current_lesson INTEGER DEFAULT 1,
                status TEXT DEFAULT 'pending', -- pending, active, completed
                activation_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expiry_date TIMESTAMP,
                is_completed INTEGER DEFAULT 0,
                next_lesson_date TIMESTAMP,  -- <--- Добавьте эту строку
                last_lesson_date TIMESTAMP,
                PRIMARY KEY (user_id, course_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (course_id, version_id) REFERENCES course_versions(course_id, version_id)
            )
            ''')

            # Homework submissions
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS homework (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                course_id TEXT,
                lesson_num INTEGER,
                message_id INTEGER,
                status TEXT DEFAULT 'pending', -- pending, approved, rejected
                feedback TEXT,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                reviewed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (user_id, course_id) REFERENCES user_courses(user_id, course_id)
            )
            ''')

            # Lesson content mapping
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS lesson_content_map (
                course_id TEXT,
                lesson_num INTEGER,
                start_message_id INTEGER,
                end_message_id INTEGER,
                snippet TEXT COLLATE NOCASE, -- Сниппет урока todo: 
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (course_id, lesson_num)
            )
            ''')

            # Promo codes
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                course_id TEXT COLLATE NOCASE,
                discount_percent INTEGER,
                uses_limit INTEGER,
                uses_count INTEGER DEFAULT 0,
                expiry_date TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (course_id) REFERENCES courses(course_id)
            )
            ''')

            # Advertisements
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS advertisements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')

            # Token transactions
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS token_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                reason TEXT COLLATE NOCASE,
                transaction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''')

            # User activity log
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT COLLATE NOCASE,
                details TEXT COLLATE NOCASE,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            ''')

            await conn.commit()
            logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        raise  # Allows bot to exit on startup if database cannot be initialized

# функция для заполнения таблицы lesson_content_map
@db_exception_handler
async def bug_fill_lesson_content_map(bot: Bot, channel_id, course_id):
    """Автоматически заполняет таблицу lesson_content_map на основе тегов в канале."""
    logger.info(f"Заполнение lesson_content_map для {course_id=} {channel_id=}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # 1. Очищаем таблицу lesson_content_map для данного course_id
            await conn.execute("DELETE FROM lesson_content_map WHERE course_id = ?", (course_id,))
            await conn.commit()
            logger.info(f"Очищена таблица lesson_content_map для курса {course_id}")

            # 2. Получаем chat
            chat = await bot.get_chat(channel_id)

            # 3. Определяем количество сообщений в канале
            total_messages = chat.pinned_message.message_id if chat.pinned_message else 1000  # todo fix

            # 4. Сканируем сообщения в канале в поисках тегов START_LESSON и END_LESSON
            lessons = {}  # Словарь для хранения start и end message_id для каждого урока
            for message_id in range(1, total_messages):
                try:
                    #message = await bot.get_chat_message(channel_id, message_id)
                    message = await bot.session.get().get_message(channel_id, message_id)
                    if message.text:
                        start_match = re.search(r"#START_LESSON (\d+)", message.text)
                        end_match = re.search(r"#END_LESSON (\d+)", message.text)

                        if start_match:
                            lesson_num = int(start_match.group(1))
                            if lesson_num not in lessons:
                                lessons[lesson_num] = {}
                            lessons[lesson_num]['start'] = message_id
                            logger.info(f"lesson_num={lesson_num} Найден START_LESSON тег: {message_id=}")
                        elif end_match:
                            lesson_num = int(end_match.group(1))
                            if lesson_num not in lessons:
                                lessons[lesson_num] = {}
                            lessons[lesson_num]['end'] = message_id
                            logger.info(f"lesson_num={lesson_num} Найден END_LESSON тег: {message_id=}")
                except TelegramAPIError as e:
                    # Игнорируем ошибки, связанные с отсутствием доступа к сообщению
                    if "message not found" in str(e):
                        continue
                    else:
                        logger.error(f"Ошибка при получении message id={message_id}: {e}")
                        continue

            # 5. Заполняем таблицу lesson_content_map на основе найденных тегов
            for lesson_num, data in lessons.items():
                if 'start' in data and 'end' in data:
                    start_message_id = data['start']
                    end_message_id = data['end']
                    await conn.execute("""
                        INSERT OR IGNORE INTO lesson_content_map (course_id, lesson_num, start_message_id, end_message_id)
                        VALUES (?, ?, ?, ?)
                    """, (course_id, lesson_num, start_message_id, end_message_id))
                    logger.info(
                        f"Для курса {course_id} урока {lesson_num} установлены message_id: start={start_message_id}, end={end_message_id}")
                else:
                    logger.warning(
                        f"Для курса {course_id} урока {lesson_num} не найдены все необходимые теги (START или END).")

            # 6. Автоматически заполняем оставшиеся уроки, если они не размечены тегами
            last_lesson_with_tags = max(lessons.keys()) if lessons else 0
            for lesson_num in range(1, last_lesson_with_tags + 2):  # Проходим все уроки, включая следующий после последнего размеченного
                if lesson_num not in lessons:
                    # Урок не размечен тегами, определяем start и end message_id автоматически
                    start_message_id = (lesson_num - 1) * DEFAULT_COUNT_MESSAGES + 1  # Смещение от начала канала
                    end_message_id = lesson_num * DEFAULT_COUNT_MESSAGES  # todo fix min(lesson_num * DEFAULT_COUNT_MESSAGES, total_messages)
                    logger.info(
                        f"Для курса {course_id} урока {lesson_num} автоматически определены message_id: start={start_message_id}, end={end_message_id}")
                    await conn.execute("""
                        INSERT OR IGNORE INTO lesson_content_map (course_id, lesson_num, start_message_id, end_message_id)
                        VALUES (?, ?, ?, ?)
                    """, (course_id, lesson_num, start_message_id, end_message_id))

            await conn.commit()
            logger.info("Заполнение lesson_content_map завершено.")

            # Логируем название курса и количество сообщений в каждом уроке
            for lesson_num in range(1, last_lesson_with_tags + 2):
                cursor = await conn.execute(
                    "SELECT start_message_id, end_message_id FROM lesson_content_map WHERE course_id = ? AND lesson_num = ?",
                    (course_id, lesson_num)
                )
                lesson_data = await cursor.fetchone()
                if lesson_data:
                    start_message_id, end_message_id = lesson_data
                    total_messages_in_lesson = end_message_id - start_message_id + 1
                    logger.info(
                        f"Курс: {course_id}, Урок: {lesson_num}, Сообщений: {total_messages_in_lesson}, start={start_message_id}, end={end_message_id}")
                else:
                    logger.warning(f"Для курса {course_id} урока {lesson_num} не найдены данные в lesson_content_map.")

            # Считываем и логируем содержимое таблицы lesson_content_map
            cursor = await conn.execute("SELECT * FROM lesson_content_map WHERE course_id = ?", (course_id,))
            rows = await cursor.fetchall()
            logger.info(f"Содержимое таблицы lesson_content_map для курса {course_id}: {rows}")

    except Exception as e:
        logger.error(f"Ошибка при заполнении lesson_content_map: {e}")

@db_exception_handler
async def qwen_fill_lesson_content_map(bot: Bot, channel_id: int, course_id: str):
    """Полный анализ канала для формирования карты уроков"""
    logger.info(f"Анализ канала {channel_id} для курса {course_id}")

    # 1. Собираем все сообщения с метками
    lessons = {}
    all_messages = []
    xxx=bot.get_chat_history(chat_id=channel_id, limit=10000)
    logger.info(f"запрос bot.get_chat_history {xxx=}")
    async for message in xxx:
        all_messages.append(message)
        logger.info(f" вот {message}")
        if message.text:
            if start := re.search(r"#START_LESSON (\d+)", message.text):
                lesson_num = int(start.group(1))
                lessons[lesson_num] = {"start": message.message_id}
            elif end := re.search(r"#END_LESSON (\d+)", message.text):
                lesson_num = int(end.group(1))
                if lesson_num in lessons:
                    lessons[lesson_num]["end"] = message.message_id
                else:
                    lessons[lesson_num] = {"end": message.message_id}

    # 2. Обрабатываем сообщения без меток
    message_ids = [msg.message_id for msg in reversed(all_messages)]  # Начинаем с самых старых
    current_lesson = 1
    current_block = []

    for msg_id in message_ids:
        # Пропускаем сообщения, уже вошедшие в размеченные уроки
        if any(msg_id >= data.get('start', 0) and msg_id <= data.get('end', 0)
               for data in lessons.values()):
            continue

        current_block.append(msg_id)
        if len(current_block) == DEFAULT_COUNT_MESSAGES:
            # Формируем новый урок
            while current_lesson in lessons:
                current_lesson += 1  # Ищем первый свободный номер
            lessons[current_lesson] = {
                "start": current_block[0],
                "end": current_block[-1]
            }
            current_block = []
            current_lesson += 1

    # 3. Сохраняем в БД
    async with aiosqlite.connect("bot.db") as conn:
        await conn.execute("DELETE FROM lesson_content_map WHERE course_id = ?", (course_id,))

        for lesson_num in sorted(lessons.keys()):
            data = lessons[lesson_num]
            if "start" not in data or "end" not in data:
                logger.warning(f"Неполные данные для урока {lesson_num}")
                continue

            await conn.execute("""
                INSERT INTO lesson_content_map 
                (course_id, lesson_num, start_message_id, end_message_id) 
                VALUES (?, ?, ?, ?)
            """, (course_id, lesson_num, data["start"], data["end"]))

        await conn.commit()
        logger.info(f"Сохранено {len(lessons)} уроков для курса {course_id}")

    # 4. Логируем результат
    for lesson_num in sorted(lessons.keys()):
        data = lessons[lesson_num]
        logger.info(f"Курс {course_id} Урок {lesson_num}: "
                    f"с {data['start']} по {data['end']} "
                    f"({data['end'] - data['start'] + 1} сообщений)")

# смотри https://core.telegram.org/bots/api#chat
@db_exception_handler
async def old_fill_lesson_content_map(bot: Bot, channel_id: int, course_id: str):
    """Full channel analysis to create a lesson map"""
    logger.info(f"Analyzing channel {channel_id} for course {course_id}")

    try:
        # 1. Fetch all messages and collect labeled messages
        lessons = {}
        all_messages = []
        try:
            chat = await bot.get_chat(channel_id)
            member_count = await bot.get_chat_member_count(channel_id)
            logger.info(f"Channel: {chat.title}, ID: {channel_id}, Members: {member_count}")
            total_messages = chat.pinned_message.message_id if chat.pinned_message else 1000
            logger.info(f"Total messages to analyze: {total_messages}")
        except TelegramAPIError as e:
            logger.error(f"Failed to get chat info or member count for channel {channel_id}: {e}")
            return  # Exit the function if channel info can't be fetched

        for message_id in range(1, total_messages):
            try:
                logger.info(f"try: {bot.session=}")
                logger.info(f"try: {message_id=}") # ==========================================
                message = await bot.session.get().get_message(channel_id, message_id)
                logger.info(f"ok: {message=}")
                if message and message.text:
                    all_messages.append(message)

                    start_match = re.search(r"#START_LESSON (\d+)", message.text)
                    end_match = re.search(r"#END_LESSON (\d+)", message.text)

                    if start_match:
                        lesson_num = int(start_match.group(1))
                        lessons[lesson_num] = {"start": message.message_id}
                        logger.info(f"Found START_LESSON {lesson_num} in message {message_id}")
                    elif end_match:
                        lesson_num = int(end_match.group(1))
                        if lesson_num in lessons:
                            lessons[lesson_num]["end"] = message.message_id
                        else:
                            lessons[lesson_num] = {"end": message.message_id}
                        logger.info(f"Found END_LESSON {lesson_num} in message {message_id}")

            except TelegramAPIError as e:
                if "message not found" in str(e):
                    continue
                else:
                    logger.error(f"Error fetching message id={message_id}: {e}")
                    continue

        # 2. Handle unlabeled messages
        message_ids = [msg.message_id for msg in reversed(all_messages)]  # Start from oldest
        current_lesson = 1
        current_block = []

        for msg_id in message_ids:
            # Skip messages already part of labeled lessons
            if any(msg_id >= data.get('start', 0) and msg_id <= data.get('end', 0) for data in lessons.values()):
                continue

            current_block.append(msg_id)
            if len(current_block) == DEFAULT_COUNT_MESSAGES:
                # Create a new lesson with default count messages
                while current_lesson in lessons:
                    current_lesson += 1  # Find a free number
                lessons[current_lesson] = {
                    "start": current_block[0],
                    "end": current_block[-1]
                }
                logger.info(f"Created new lesson {current_lesson} from unlabeled messages: start={current_block[0]}, end={current_block[-1]}")
                current_block = []
                current_lesson += 1

        # Handle any remaining messages in current_block
        if current_block:
            while current_lesson in lessons:
                current_lesson += 1
            lessons[current_lesson] = {
                "start": current_block[0],
                "end": current_block[-1]
            }
            logger.info(f"Created new lesson {current_lesson} from remaining unlabeled messages: start={current_block[0]}, end={current_block[-1]}")

        # 3. Save to DB
        async with aiosqlite.connect("bot.db") as conn:
            await conn.execute("DELETE FROM lesson_content_map WHERE course_id = ?", (course_id,))

            for lesson_num, data in lessons.items():
                if "start" not in data or "end" not in data:
                    logger.warning(f"Incomplete data for lesson {lesson_num}")
                    continue

                await conn.execute("""
                    INSERT INTO lesson_content_map 
                    (course_id, lesson_num, start_message_id, end_message_id) 
                    VALUES (?, ?, ?, ?)
                """, (course_id, lesson_num, data["start"], data["end"]))

            await conn.commit()
            logger.info(f"Saved {len(lessons)} lessons for course {course_id}")

        # 4. Log result
        for lesson_num, data in lessons.items():
            logger.info(f"Course {course_id} Lesson {lesson_num}: "
                        f"from {data.get('start')} to {data.get('end')} "
                        f"({data.get('end', 0) - data.get('start', 0) + 1} messages)")

    except TelegramAPIError as e:
        logger.error(f"Telegram API Error: {e}")
    except Exception as e:
        logger.error(f"General Error: {e}")


@db_exception_handler
async def old2_fill_lesson_content_map(bot: Bot, channel_id: int, course_id: str):
    """Full channel analysis to create a lesson map, with JSON fallback"""
    logger.info(f"Analyzing channel {channel_id} for course {course_id}")

    try:
        # 1. Try to fetch data from the Telegram channel
        chat = await bot.get_chat(channel_id)
        member_count = await bot.get_chat_member_count(channel_id)
        logger.info(f"Channel: {chat.title}, ID: {channel_id}, Members: {member_count}")

        all_messages = []
        total_messages = chat.pinned_message.message_id if chat.pinned_message else 1000

        async for message in bot.get_chat_history(chat_id=channel_id, limit=total_messages):
            try:
                if message and message.text:
                    all_messages.append({
                        "id": message.message_id,
                        "text": message.text,
                    })
            except Exception as e:  # Catching more general exceptions for robustness
                logger.error(f"Error processing message from chat history: {e}")
                continue # skip to next message


        # 2. Process messages (labeled and unlabeled)
        lessons = {}
        message_ids = [msg["id"] for msg in reversed(all_messages)]  # start from the oldest message

        current_lesson = 1
        current_block = []

        # iterate all existing message
        for msg_id in message_ids:
            # skip if message exists in the labeled lessons, to avoid overlapping
            if any(msg_id >= data.get('start', 0) and msg_id <= data.get('end', 0) for data in lessons.values()):
                continue

            current_block.append(msg_id)  # put the mesage here.

            if len(current_block) == DEFAULT_COUNT_MESSAGES:  # limit size of the all message id array
                # try to fill, if empty
                while current_lesson in lessons:
                    current_lesson += 1

                lessons[current_lesson] = {  # the new content. key lesson_number value first and last
                    "start": current_block[0],
                    "end": current_block[-1]
                }

                logger.info(
                    f"Created new lesson {current_lesson} from unlabeled messages: start={current_block[0]}, end={current_block[-1]}")
                current_block = []  # cleaning content here, to avoid duplication
                current_lesson += 1

        if current_block:  # the current content has something. lets iterate
            while current_lesson in lessons:
                current_lesson += 1
            lessons[current_lesson] = {
                "start": current_block[0],
                "end": current_block[-1]
            }
            logger.info(
                f"Created new lesson {current_lesson} from remaining unlabeled messages: start={current_block[0]}, end={current_block[-1]}")

    # now, the lessons
    except TelegramAPIError as e:
        logger.warning(f"Failed to fetch data from Telegram: {e}, falling back to JSON file.")
        # Attempt to read from a JSON file
        json_file = os.path.join("3ant", str(channel_id) + ".json")
        logger.info(f"Attempting JSON file load from: {json_file}")

        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "messages" in data:  # check exists messages property in json
                    lessons = {} # key lesson_num value message id
                    all_messages = data["messages"]

                    for message in all_messages: # iterate this array
                        try:

                            if message.get("text"):  # exist message value to start
                                # now iterate properties. search what you need to set
                                start_match = re.search(r"#START_LESSON (\d+)", message["text"])
                                end_match = re.search(r"#END_LESSON (\d+)", message["text"]) # the correct check start lesson message

                                # check existing start lessons
                                if start_match:
                                    lesson_num = int(start_match.group(1))
                                    lessons[lesson_num] = {"start": message["id"]}  # value to id

                                    logger.info(
                                        f"Found JSON START_LESSON {lesson_num} in message {message['id']}")

                                # lets check end lesson
                                elif end_match:  # same processing with end

                                    lesson_num = int(end_match.group(1))

                                    if lesson_num in lessons:
                                        lessons[lesson_num]["end"] = message["id"]  # key to message id
                                        logger.info(f"Found JSON END_LESSON {lesson_num} in message {message['id']}")
                                    else:  # if key is not found - log it and continue
                                        logger.warning(f"end key {lesson_num} message is not found")
                                        continue  # skip this iteration, go to next record

                        except Exception as e:
                            logger.error(f"message get content ERROR, {e=}") # log errors

                else:
                    raise ValueError("No 'messages' key in JSON data.")
        except FileNotFoundError:  # if file not found
            logger.error(f"JSON file not found: {json_file}")
            return
        except json.JSONDecodeError:  # problem reading
            logger.error(f"Failed to decode JSON from {json_file}")
            return
        except ValueError as ve:  # value problems - log.
            logger.error(f"Data Error: {ve}")
            return

    except Exception as e:
        logger.error(f"ERROR main, {e=}")  # other error
        return # exit code here

    # 3. Save to DB (no change here. here, the data should be correctly populated)
    async with aiosqlite.connect("bot.db") as conn:
        await conn.execute("DELETE FROM lesson_content_map WHERE course_id = ?", (course_id,))

        for lesson_num, data in lessons.items():
            if "start" not in data or "end" not in data:
                logger.warning(f"Incomplete data for lesson {lesson_num}")
                continue # missing one key - continue

            await conn.execute("""
                INSERT INTO lesson_content_map
                (course_id, lesson_num, start_message_id, end_message_id)
                VALUES (?, ?, ?, ?)
            """, (course_id, lesson_num, data["start"], data["end"]))

        await conn.commit()
        logger.info(f"Saved {len(lessons)} lessons for course {course_id}")

    # 4. Log result (no change here)
    for lesson_num, data in lessons.items():
        logger.info(f"Course {course_id} Lesson {lesson_num}: "
                    f"from {data.get('start')} to {data.get('end')} "
                    f"({data.get('end', 0) - data.get('start', 0) + 1} messages)")


@db_exception_handler
async def old3_fill_lesson_content_map(bot: Bot, channel_id: int, course_id: str):
    """Автоматически заполняет lesson_content_map через анализ канала с использованием методов aiogram 3.19"""
    logger.info(f"Анализ канала {channel_id} для курса {course_id}")

    try:
        # 1. Получаем все сообщения из канала
        all_messages = []
        try:
            async for message in bot.get_chat_history(chat_id=channel_id, limit=10000):
                all_messages.append(message)
                await asyncio.sleep(0.1)  # Задержка для предотвращения рейт-лимита
        except TelegramAPIError as e:
            logger.error(f"Ошибка получения истории чата: {e}")
            return

        # Инвертируем порядок (от старых к новым)
        all_messages = list(reversed(all_messages))
        logger.info(f"Получено {len(all_messages)} сообщений канала")

        # 2. Собираем теги уроков
        lessons = {}
        for message in all_messages:
            if message.text:
                start_match = re.search(r"#START_LESSON (\d+)", message.text)
                end_match = re.search(r"#END_LESSON (\d+)", message.text)

                if start_match:
                    lesson_num = int(start_match.group(1))
                    lessons[lesson_num] = {"start": message.message_id}
                    logger.info(f"Найден START_LESSON {lesson_num}: {message.message_id}")
                elif end_match:
                    lesson_num = int(end_match.group(1))
                    if lesson_num in lessons:
                        lessons[lesson_num]["end"] = message.message_id
                    else:
                        lessons[lesson_num] = {"end": message.message_id}
                    logger.info(f"Найден END_LESSON {lesson_num}: {message.message_id}")

        # 3. Обрабатываем сообщения без тегов
        current_lesson = 1
        current_block = []
        for msg in all_messages:
            # Пропускаем сообщения, уже вошедшие в размеченные уроки
            if any(
                    lesson.get("start", 0) <= msg.message_id <= lesson.get("end", 0)
                    for lesson in lessons.values()
            ):
                continue

            current_block.append(msg.message_id)
            if len(current_block) == DEFAULT_COUNT_MESSAGES:
                # Создаем новый урок
                while current_lesson in lessons:
                    current_lesson += 1
                lessons[current_lesson] = {
                    "start": current_block[0],
                    "end": current_block[-1]
                }
                logger.info(f"Создан урок {current_lesson}: с {current_block[0]} по {current_block[-1]}")
                current_block = []
                current_lesson += 1

        # Добавляем оставшиеся сообщения в последний урок
        if current_block:
            while current_lesson in lessons:
                current_lesson += 1
            lessons[current_lesson] = {
                "start": current_block[0],
                "end": current_block[-1]
            }
            logger.info(
                f"Создан урок {current_lesson} из оставшихся сообщений: с {current_block[0]} по {current_block[-1]}")

        # 4. Сохраняем в БД
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("DELETE FROM lesson_content_map WHERE course_id = ?", (course_id,))

            for lesson_num in sorted(lessons.keys()):
                data = lessons[lesson_num]
                if "start" in data and "end" in data:
                    await conn.execute(
                        """INSERT INTO lesson_content_map 
                        (course_id, lesson_num, start_message_id, end_message_id) 
                        VALUES (?, ?, ?, ?)""",
                        (course_id, lesson_num, data["start"], data["end"])
                    )
                else:
                    logger.warning(f"Неполные данные для урока {lesson_num}")

            await conn.commit()
            logger.info(f"Сохранено {len(lessons)} уроков для курса {course_id}")

    except Exception as e:
        logger.error(f"Ошибка заполнения lesson_content_map: {e}")
        raise


@db_exception_handler
async def old4_fill_lesson_content_map(bot: Bot, channel_id: int, course_id: str):
    """Автоматически заполняет lesson_content_map через бот-сессию Telegram API"""
    logger.info(f"Анализ канала {channel_id} для курса {course_id}")

    try:
        # 1. Получаем все сообщения через сессию бота
        all_messages = []
        offset_message_id = 0
        total_messages = 1000  # Максимальное количество сообщений для анализа

        # Используем get_chat_history из сессии
        while True:
            response = await bot.session.get_chat_history(
                chat_id=channel_id,
                limit=100,  # Максимальный лимит Telegram API
                offset_id=offset_message_id
            )

            if not response.messages:
                break

            all_messages.extend(response.messages)
            offset_message_id = response.messages[-1].message_id
            await asyncio.sleep(0.1)  # Задержка для рейт-лимита

            if len(all_messages) >= total_messages:
                break

        # Инвертируем порядок (от старых к новым)
        all_messages = list(reversed(all_messages))
        logger.info(f"Получено {len(all_messages)} сообщений канала")

        # 2. Собираем теги уроков
        lessons = {}
        for message in all_messages:
            if message.text:
                start_match = re.search(r"#START_LESSON (\d+)", message.text)
                end_match = re.search(r"#END_LESSON (\d+)", message.text)

                if start_match:
                    lesson_num = int(start_match.group(1))
                    lessons[lesson_num] = {"start": message.message_id}
                    logger.info(f"Найден START_LESSON {lesson_num}: {message.message_id}")
                elif end_match:
                    lesson_num = int(end_match.group(1))
                    if lesson_num in lessons:
                        lessons[lesson_num]["end"] = message.message_id
                    else:
                        lessons[lesson_num] = {"end": message.message_id}
                    logger.info(f"Найден END_LESSON {lesson_num}: {message.message_id}")

        # 3. Обрабатываем сообщения без тегов
        current_lesson = 1
        current_block = []
        for msg in all_messages:
            # Пропускаем сообщения, уже вошедшие в размеченные уроки
            if any(
                    lesson.get("start", 0) <= msg.message_id <= lesson.get("end", 0)
                    for lesson in lessons.values()
            ):
                continue

            current_block.append(msg.message_id)
            if len(current_block) == DEFAULT_COUNT_MESSAGES:
                # Создаем новый урок
                while current_lesson in lessons:
                    current_lesson += 1
                lessons[current_lesson] = {
                    "start": current_block[0],
                    "end": current_block[-1]
                }
                logger.info(f"Создан урок {current_lesson}: с {current_block[0]} по {current_block[-1]}")
                current_block = []
                current_lesson += 1

        # Добавляем оставшиеся сообщения в последний урок
        if current_block:
            while current_lesson in lessons:
                current_lesson += 1
            lessons[current_lesson] = {
                "start": current_block[0],
                "end": current_block[-1]
            }

        # 4. Сохраняем в БД
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("DELETE FROM lesson_content_map WHERE course_id = ?", (course_id,))

            for lesson_num in sorted(lessons.keys()):
                data = lessons[lesson_num]
                if "start" in data and "end" in data:
                    await conn.execute(
                        """INSERT INTO lesson_content_map 
                        (course_id, lesson_num, start_message_id, end_message_id) 
                        VALUES (?, ?, ?, ?)""",
                        (course_id, lesson_num, data["start"], data["end"])
                    )
                else:
                    logger.warning(f"Неполные данные для урока {lesson_num}")

            await conn.commit()
            logger.info(f"Сохранено {len(lessons)} уроков для курса {course_id}")

    except Exception as e:
        logger.error(f"Ошибка заполнения lesson_content_map: {e}")
        raise


@db_exception_handler
async def old5_fill_lesson_content_map(bot: Bot, channel_id: int, course_id: str):
    """Полный анализ канала для формирования карты уроков из JSON файла"""
    logger.info(f"Анализ курса {course_id} с использованием JSON файла для канала {channel_id}")

    # Construct the file path relative to the base directory
    json_dir = os.path.join(os.getcwd(), str(channel_id)) # use os.getcwd to be safe. key change.
    json_file = os.path.join(json_dir, "result.json")  # Always named "result.json"

    logger.info(f"Attempting JSON file load from: {json_file}")

    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

            if "messages" in data:
                all_messages = data["messages"]  # key value of messages
                lessons = {}

                for message in all_messages: # iterate
                    try:
                        #if text key, not value exists
                        if message.get("text"):
                            start_match = re.search(r"#START_LESSON (\d+)", message["text"]) #search start by mask
                            end_match = re.search(r"#END_LESSON (\d+)", message["text"])#search end by mask

                            if start_match: # if true, action
                                lesson_num = int(start_match.group(1)) # get value
                                lessons[lesson_num] = {"start": message["id"]} # assing value
                                logger.info(f"JSON START_LESSON {lesson_num} found in message {message['id']}") # log

                            elif end_match: # the processing part
                                lesson_num = int(end_match.group(1)) # get array message id

                                if lesson_num in lessons: # exist key, or not?
                                    lessons[lesson_num]["end"] = message["id"] # if exist - set end
                                    logger.info(f"JSON END_LESSON {lesson_num} found in message {message['id']}") # log value
                                else:
                                    logger.warning(f"Skipping END_LESSON {lesson_num} missing message id from lesson json result") #if fail - show to user
                        else:
                            logger.warning(f"Text property missing in JSON record")# if fail  - msg
                    except Exception as e:
                        logger.error(f"Error with one record in json, code: {e}") #if critical - write to user

                # Save to DB
                async with aiosqlite.connect("bot.db") as conn:
                    await conn.execute("DELETE FROM lesson_content_map WHERE course_id = ?", (course_id,)) # cleanup before action

                    for lesson_num, data in lessons.items():# now - process to the end,
                        if "start" not in data or "end" not in data: # valid data only
                            logger.warning(f"Skipping {lesson_num=} missing values. check your result and lesson") #write to user
                            continue #continue iteration if some data is corrupter

                        await conn.execute("""
                            INSERT INTO lesson_content_map
                            (course_id, lesson_num, start_message_id, end_message_id)
                            VALUES (?, ?, ?, ?)
                        """, (course_id, lesson_num, data["start"], data["end"])) #insert action

                    await conn.commit()
                    logger.info(f"Saved {len(lessons)} lessons for course {course_id}") #log if has some record and iteration is not corrupted. or all data is garbage

                # Log result
                for lesson_num, data in lessons.items():#and now
                    logger.info(f"Course {course_id} Lesson {lesson_num}: "
                                f"from {data.get('start')} to {data.get('end')} "
                                f"({data.get('end', 0) - data.get('start', 0) + 1} messages)") #if true actions

            else:
                raise ValueError("The property: messages value is not loaded to var data") # main error

    except FileNotFoundError:
        logger.error(f"File is broken check: {json_file}")# not found
    except json.JSONDecodeError:
        logger.error(f"Error decode json check: {json_file}")# code in file, check
    except ValueError as ve:
        logger.error(f"Error parsing data {ve}")
    except Exception as e:
        logger.error(f"Error, and its very bad {e=}") # if main error all


@db_exception_handler
async def fill_lesson_content_map(bot: Bot, channel_id: int, course_id: str):
    """Full channel analysis to create a lesson map from JSON file"""
    logger.info(f"Analyzing course {course_id} with JSON from channel {channel_id}")

    # Construct the file path relative to the base directory
    json_dir = os.path.join(os.getcwd(), str(channel_id))
    json_file = os.path.join(json_dir, "result.json")
    logger.info(f"Attempting to load JSON file from: {json_file}")

    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            #logger.info(f"{data['messages']=}") # это тупо все сообщения - ща мы их ниже по одному глянем
            logger.info(f"всего сообщений {len(data['messages'])}")
            if data["messages"] and len(data['messages'])>0:
                logger.info(" сообщения - есть! ")
                all_messages = data["messages"]
                lessons = {}
                lesson_num = None  # Initialize lesson_num here
                logger.info(f"all_messages = data[messages] len= {len(all_messages)}")

                for message in all_messages:
                    try:
                        # Check if message is a dictionary
                        if isinstance(message, dict):
                            logger.info(f" енто словарь {len(message)=}")
                            # Check if text is a list and convert to string
                            if isinstance(message.get("text"), list):
                                text = "".join(str(item) for item in message["text"])
                                logger.info(f" список в словаре сжали {text=}")
                            else:
                                text = str(message.get("text", ""))
                                logger.info(f" просто одиноковый текст {text=}")

                            start_match = re.search(r"#START_LESSON (\d+)", text)
                            end_match = re.search(r"#END_LESSON (\d+)", text)

                            if start_match:
                                logger.info(f"START_LESSON {start_match=} ")
                                lesson_num = int(start_match.group(1))
                                lessons[lesson_num] = {"start": message["id"]}
                                logger.info(f"JSON START_LESSON {lesson_num} found in message {message['id']}")

                            elif end_match:
                                logger.info(f"end_match {end_match=} ")
                                lesson_num = int(end_match.group(1))
                                if lesson_num in lessons:
                                    lessons[lesson_num]["end"] = message["id"]
                                    logger.info(f"JSON END_LESSON {lesson_num} found in message {message['id']}")
                                else:
                                    logger.warning(f"Skipping end: {lesson_num=}")
                        else:
                            logger.warning(f"енто не словарь {message=}")

                    except Exception as e:
                        logger.error(f"Error check this msg record number  {message.get('id')}, value to this code:  {e=}")
                try:
                    async with aiosqlite.connect("bot.db") as conn:
                        # Before do action, run cleanup
                        await conn.execute("DELETE FROM lesson_content_map WHERE course_id = ?", (course_id,))
                        # Check if lesson_num was assigned before using it
                        if lesson_num is not None:
                            for lesson_num, data in lessons.items():
                                if "start" not in data or "end" not in data:
                                    logger.warning(f"value missing value and properties for lesson number {lesson_num=}")
                                    continue
                                logger.info(f"!!! ща запишем в базу данных")
                                await conn.execute("""
                                    INSERT INTO lesson_content_map
                                    (course_id, lesson_num, start_message_id, end_message_id)
                                    VALUES (?, ?, ?, ?)
                                """, (course_id, lesson_num, data["start"], data["end"]))

                            await conn.commit()
                            logger.info(f"Value in action result action to {lesson_num=}")
                        else:
                            logger.warning("No lessons found")
                except Exception as e:
                     logger.error(f"Database action failed, value {e=}")

                # And now, for logging
                for lesson_num, data in lessons.items():
                    logger.info(f"Finished {lesson_num=}: start value = {data.get('start')}, end value = {data.get('end')}")

            else:
                raise ValueError("messages value was not load to this Json from file, check property this value key and value result main action or not")

    except FileNotFoundError:
        logger.error(f"Check this file , value is broken or lost {json_file=}")

    except json.JSONDecodeError:
        logger.error(f"Recommend for you check this json with validator tool for check json ,{json_file=}")

    except Exception as e:
        logger.error(f"Error not work properyly value  , main result check developer  {e=}")

# Обработчик команды /fill_lesson_content_map
@dp.message(Command("fill_lesson_content_map"))
@db_exception_handler
async def cmd_fill_lesson_content_map(message: types.Message):
    """Handles the /fill_lesson_content_map command to automatically fill the lesson_content_map table."""
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.reply("У вас нет прав для выполнения этой команды.")
        return

    args = message.text.split()[1:]
    if len(args) < 2:
        await message.reply(
            "Недостаточно аргументов. Используйте: /fill_lesson_content_map <channel_id> <course_id>")
        return

    channel_id, course_id = args
    channel_id = int(channel_id)

    logger.info(f"1/3 Заполнение таблицы lesson_content_map для  {channel_id=} '{course_id=}'...")
    await fill_lesson_content_map(bot, channel_id, course_id)
    await message.reply(f"Таблица lesson_content_map для курса '{course_id}' успешно заполнена.")


# функция для инициализации тестовых данных в БД
@db_exception_handler
async def old_init_test_data():
    """Initialize test data if database is empty"""
    logger.info(f"init_test_data ")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Check if courses table is empty
            cursor = await conn.execute("SELECT COUNT(*) FROM courses")
            if (await cursor.fetchone())[0] == 0:
                logger.info("Курсы не найдены, добавляем тестовые данные...")
                # Вставляем тестовые курсы
                await conn.execute("""
                    INSERT INTO courses (course_id, title, is_active)
                    VALUES ('femininity', 'Женственность', 1)
                """)

                # Вставляем тестовые версии курсов
                await conn.execute("""
                    INSERT INTO course_versions (course_id, version_id, title, activation_code)
                    VALUES 
                    ('femininity', 'v1', 'Базовый тариф', 'роза'),
                    ('femininity', 'v2', 'Стандартный тариф', 'фиалка'),
                    ('femininity', 'v3', 'Премиум тариф', 'лепесток')
                """)

                # Вставляем тестовые каналы
                settings["activation_codes"] = {
                    "роза": "femininity:v1",
                    "фиалка": "femininity:v2",
                    "лепесток": "femininity:v3"
                }
                settings["channels"] = {
                    "-1001234567890": "femininity"
                }
                save_settings(settings)

                await conn.commit()
                logger.info("Тестовые данные успешно добавлены.")
    except Exception as e:
        logger.error(f"Error initializing test data: {e}")


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

# Отправка урока пользователю
@db_exception_handler
async def send_lesson_to_user(user_id, course_id, lesson_num):
    """Send lesson content to a user from the corresponding channel"""
    logger.info(f"send_lesson_to_user {user_id=} {course_id=} {lesson_num=}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Get lesson content range from the lesson_content_map table
            cursor = await conn.execute("""
                SELECT c.channel_id, lcm.start_message_id, lcm.end_message_id
                FROM lesson_content_map lcm
                JOIN courses c ON c.course_id = lcm.course_id
                WHERE lcm.course_id = ? AND lcm.lesson_num = ?
            """, (course_id, lesson_num))
            lesson_data = await cursor.fetchone()
            logger.info(f"channel_id={lesson_data}")

            if lesson_data:
                channel_id, start_id, end_id = lesson_data
                logger.info(f"{channel_id=}, {start_id=}, {end_id=}")
            else:
                logger.error(f"Урок не найден для курса {course_id} и урока {lesson_num}")
                await bot.send_message(user_id, "Урок не найден. Пожалуйста, обратитесь к администратору.")
                return False

        # Send all messages in the range
        for msg_id in range(start_id, end_id + 1):
            try:
                # Using copy_message to maintain privacy and allow customization
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=channel_id,
                    message_id=msg_id
                )
                logger.info(f"558 {msg_id=}")
                await asyncio.sleep(0.5)  # Prevent flooding
            except Exception as e:
                logger.error(f"Error sending message {msg_id} to user {user_id}")
                await bot.send_message(user_id, "Произошла ошибка при отправке одного из уроков. Пожалуйста, попробуйте позже или обратитесь к администратору.")
                return False

        # Check if user is a student
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute(
                "SELECT version_id FROM user_courses WHERE user_id = ? AND course_id = ?",
                (user_id, course_id)
            )
            student_info = await cursor.fetchone()
            logger.info(f" 777ll {student_info=}")
            if not student_info:
                logger.warning(f"User {user_id} not enrolled in course {course_id}")
                await bot.send_message(user_id, "Вы не записаны на этот курс. Пожалуйста, активируйте его сначала.")
                return False
        logger.info(f"All messages sent for lesson {lesson_num} of course {course_id} to user {user_id}")
        return True

    except Exception as e:
        logger.error(f"General error in send_lesson_to_user: {e}")
        await bot.send_message(user_id, "Произошла общая ошибка при отправке урока. Пожалуйста, попробуйте позже или обратитесь к администратору.")
        return False


# функция для кэширования статуса курса пользователя
@lru_cache(maxsize=100)
async def get_course_status(user_id: int) -> tuple | None:
    """Кэшируем статус курса на 5 минут"""
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT uc.course_id, c.title, uc.version_id, uc.current_lesson 
            FROM user_courses uc
            JOIN courses c ON uc.course_id = c.course_id
            WHERE uc.user_id = ? AND uc.status = 'active'
        """, (user_id,))
        return await cursor.fetchone()



# фоновая задача для проверки и отправки уведомлений о новых уроках.
@db_exception_handler
async def check_and_schedule_lessons():
    """Background task to check and send scheduled lessons"""
    logger.info("Starting check_and_schedule_lessons...")
    while True:
        try:
            async with aiosqlite.connect(DB_FILE) as conn:
                # Fetch all active user courses with scheduled lessons
                cursor = await conn.execute(
                    """
                    SELECT uc.user_id, uc.course_id, uc.current_lesson
                    FROM user_courses uc
                    JOIN courses c ON uc.course_id = c.course_id
                    WHERE uc.next_lesson_date <= CURRENT_TIMESTAMP
                    AND uc.is_completed = 0
                    AND c.is_active = 1
                    """
                )
                user_lessons = await cursor.fetchall()

                logger.info(f"Found {len(user_lessons)} user(s) with due lessons.")

                for user_id, course_id, current_lesson in user_lessons:
                    try:
                        # Create a keyboard to start the lesson
                        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(
                                text="Начать урок",
                                callback_data=f"start_lesson:{course_id}:{current_lesson}"
                            )
                        ]])

                        # Send the notification
                        await bot.send_message(
                            user_id,
                            f"🔔 Доступен новый урок курса! Нажмите кнопку ниже, чтобы начать.",
                            reply_markup=keyboard
                        )

                        # Update next lesson date to NULL to prevent repeated notifications
                        await conn.execute(
                            "UPDATE user_courses SET next_lesson_date = NULL WHERE user_id = ? AND course_id = ?",
                            (user_id, course_id)
                        )
                        logger.info(f"Scheduled lesson notification sent for user {user_id}, course {course_id}, lesson {current_lesson}")

                        # Log user activity
                        await log_user_activity(user_id, "LESSON_AVAILABLE", f"Course: {course_id}, Lesson: {current_lesson}")

                    except Exception as e:
                        logger.error(f"Failed to process or send scheduled lesson to user {user_id}: {e}")
                await conn.commit()

        except Exception as e:
            logger.error(f"General error in check_and_schedule_lessons: {e}")

        logger.info("Background task: check_and_schedule_lessons completed one cycle, sleeping for 60 seconds.")

        # Check every minute
        await asyncio.sleep(60)


async def adm_message_user(message: Message):
    """Send a message to user by admin"""
    try:
        # Ensure the user sending the command is an admin
        if message.from_user.id not in ADMIN_IDS:
            await message.reply("У вас недостаточно прав для выполнения этой команды.")
            return

        # Parse arguments: first argument is user_id, rest is the message
        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            await message.reply("Использование: /send_message <user_id> <текст сообщения>")
            return

        _, user_id_str, text = parts
        user_id = int(user_id_str)
        await bot.send_message(user_id, text)
        logger.info(f"Сообщение пользователю {user_id} отправлено.")
        await message.reply(f"Сообщение пользователю {user_id} отправлено.")

    except ValueError as ve:
        logger.error(f"Неверный формат ID пользователя. ID пользователя должен быть числом. {ve}")
        await message.reply("Неверный формат ID пользователя. ID пользователя должен быть числом.")

    except Exception as e:
        logger.error(f"Произошла ошибка при отправке сообщения: {e}")
        await message.reply("Произошла ошибка при отправке сообщения.")


async def process_add_course_to_db(course_id: str, channel_id: str, code1: str, code2: str, code3: str):
    """  Добавляет информацию о курсе и кодах активации в базу данных. homework_check_type  в таблице course_versions должен быть "self" для первого кодового слова, v1 которое    """
    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute("""
            INSERT OR REPLACE INTO courses
            (course_id, title, channel_id)
            VALUES (?, ?, ?)
        """, (course_id, course_id, channel_id))

        # add activation codes to course_versions table
        await conn.execute("""
            INSERT OR REPLACE INTO course_versions
            (course_id, version_id, title, activation_code, homework_check_type)
            VALUES (?, ?, ?, ?, ?)
        """, (course_id, "v1", f"{course_id} basic", code1, "self")) # homework_check_type = self
        await conn.execute("""
            INSERT OR REPLACE INTO course_versions
            (course_id, version_id, title, activation_code, homework_check_type)
            VALUES (?, ?, ?, ?, ?)
        """, (course_id, "v2", f"{course_id} group", code2, "admin"))
        await conn.execute("""
            INSERT OR REPLACE INTO course_versions
            (course_id, version_id, title, activation_code, homework_check_type)
            VALUES (?, ?, ?, ?, ?)
        """, (course_id, "v3", f"{course_id} vip", code3, "admin"))

        await conn.commit()



@dp.message(Command("add_course"))
@db_exception_handler
async def set_activation_code(message: Message):
    """Добавляет курс с кодами активации."""
    global settings  # Access the global settings variable
    try:
        args = message.text.split()
        if len(args) < 6: # Требуется channel_id, course_id и как минимум 3 кода
            await message.answer("Неверный формат команды. Используйте: /add_course <channel_id> <course_id> <code1> <code2> <code3>")
            return

        channel_id = args[1]
        course_id = args[2]
        code1, code2, code3 = args[3], args[4], args[5]
        activation_codes=[code1, code2, code3]

        # Проверяем, что channel_id - целое число
        try:
            int(channel_id)
        except ValueError:
            await message.answer("channel_id должен быть целым числом")
            return

        # Проверка уникальности кодов
        existing_codes = settings["activation_codes"].keys()
        for code in activation_codes:
            if code in existing_codes:
                if settings["activation_codes"][code].split(":")[0] != course_id:
                    await message.answer(f"Код '{code}' уже используется в другом курсе.")
                    return

        # Обновляем настройки
        settings["channels"][channel_id] = course_id
        settings["activation_codes"][code1] = f"{course_id}:v1"
        settings["activation_codes"][code2] = f"{course_id}:v2"
        settings["activation_codes"][code3] = f"{course_id}:v3"

        save_settings(settings)  # сохраняем

        await process_add_course_to_db(course_id, channel_id, code1, code2, code3)

        # Автоматически заполняем lesson_content_map после добавления курса
        logger.info(f"2/3 Заполнение таблицы lesson_content_map для {channel_id=}  int делаю. '{course_id=}'...")
        await fill_lesson_content_map(bot, int(channel_id), course_id)

        await message.answer(f"Курс {course_id} успешно добавлен/обновлен с channel_id {channel_id} и кодами активации")


    except Exception as e:
        logger.error(f"Ошибка в set_activation_code: {e}")
        await message.answer("Произошла ошибка при добавлении курса.")


# Admin command to reply to user
@dp.message(Command("adm_message_user"), F.chat.id == ADMIN_GROUP_ID)
async def adm_message_user(message: Message):
    """Send a message to a user from admin"""
    command_parts = message.text.split(maxsplit=2)
    logger.info(f"adm_message_user {command_parts=}  ")
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


@dp.message(Command("homework"))
@db_exception_handler
async def cmd_homework(message: types.Message):
    """
    Allows user to submit homework
    """
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
        await message.answer("Ваш тариф не предполагает проверку домашних заданий администратором. Вы можете выполнить задание для себя.")
        return
    else:
        # Пересылка сообщения администраторам
        await bot.forward_message(ADMIN_GROUP_ID, message.chat.id, message.message_id)

        await message.answer("Ваше домашнее задание отправлено на проверку администраторам!")


# функция для активации курса
@db_exception_handler
async def activate_course(user_id, course_id, course_type, price_rub):
    logger.info("activate_course")
    async with aiosqlite.connect(DB_FILE) as conn:
        # Проверяем, не активирован ли уже этот курс
        cursor = await conn.execute(
            "SELECT 1 FROM user_courses WHERE user_id = ? AND course_id = ?",
            (user_id, course_id)
        )
        already_enrolled = await cursor.fetchone()

        if already_enrolled:
            return False

        # Записываем курс в базу данных
        await conn.execute(
            """
            INSERT INTO user_courses 
            (user_id, course_id, version_id, current_lesson, activation_date)
            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            """,
            (user_id, course_id, course_type)
        )

        # Логируем активацию курса
        await log_user_activity(
            user_id,
            "COURSE_ACTIVATION",
            f"Course: {course_id}, Type: {course_type}, Price: {price_rub}"
        )

        await conn.commit()
    return True


async def get_courses_list():
    """Получает список курсов из базы данных."""
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT course_id, title FROM courses")
        courses = await cursor.fetchall()
    return courses

#"Показывает статус курса с маппингом тарифов
async def show_course_status(message: types.Message, course_data: tuple, keyboard: types.InlineKeyboardMarkup):
    """Показывает статус курса с маппингом тарифов"""
    # Маппинг версий на названия тарифов
    TARIFF_NAMES = {
        "v1": "Соло",
        "v2": "Группа",
        "v3": "VIP"
    }

    course_id, title, version_id, current_lesson = course_data

    # Получаем человекочитаемое название тарифа
    tariff = TARIFF_NAMES.get(version_id, f"Тариф {version_id}")

    await message.answer(
        f"С возвращением, {message.from_user.first_name}!\n\n"
        f"🎓 Курс: {title}\n"
        f"🔑 Тариф: {tariff}\n"
        f"📚 Текущий урок: {current_lesson}\n",
        reply_markup=keyboard
    )


# Регистрация нового пользователя или приветствие существующего
@dp.message(CommandStart())
@db_exception_handler
async def cmd_start(message: types.Message):
    """Обработчик команды /start с улучшенной логикой"""
    user = message.from_user
    user_id = user.id

    try:
        # Отправляем базовое приветствие
        await message.answer(
            f"👋 Привет, {user.first_name}!   ID: {user_id}\n"
            "Добро пожаловать в бот обучающих курсов!\n\n"
        )

        # Логирование события
        logger.info(f"Обработка /start для пользователя {user_id}")

        # Регистрация/проверка пользователя
        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверка существования пользователя
            cursor = await conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
            user_exists = await cursor.fetchone()

            if not user_exists:
                # Регистрация нового пользователя
                await conn.execute("""
                    INSERT INTO users (user_id, first_name, last_name, username, registered_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (
                    user_id,
                    user.first_name,
                    user.last_name or "",
                    user.username or ""
                ))
                await conn.commit()
                await log_user_activity(user_id, "REGISTRATION", "New user registered")

            # Вместо прямого запроса к БД
            active_course = await get_course_status(user_id)

        # Генерация клавиатуры
        keyboard = get_main_menu_inline_keyboard()

        if active_course:
            # Используем существующую функцию show_course_status
            await show_course_status(message, active_course, keyboard)
        else:
            # Обработка для пользователей без курсов
            courses = await get_courses_list()
            if courses:
                courses_text = "\n".join([f"- {title} ({course_id})" for course_id, title in courses])
                await message.answer(
                    f"{'Добро пожаловать' if not user_exists else 'С возвращением'}, {user.first_name}!\n"
                    "Доступные курсы:\n"
                    f"{courses_text}\n\n"
                    "Введите кодовое слово для активации курса:"
                )
            else:
                await message.answer("К сожалению, сейчас нет доступных курсов.")

    except Exception as e:
        logger.error(f"Ошибка в cmd_start: {e}")
        await message.answer("Произошла ошибка. Пожалуйста, попробуйте позже.")


#help
@dp.message(Command("help"))
async def cmd_help(message: Message):
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


# Создает тикет в службу поддержки # Пересылает сообщение администраторам
@dp.message(Command("support"))
async def cmd_support(message: Message):
    """Handler for the /support command to initiate and process support requests"""
    user_id = message.from_user.id
    logger.info(f"cmd_support {user_id=}")

    if message.text == '/support':
        # Инициируем запрос в поддержку
        await message.answer(escape_md(
            "📞 *Поддержка*\n\n"
            "Опишите вашу проблему или задайте вопрос. Мы постараемся ответить как можно скорее.\n"
            "Для отмены введите /cancel."),
            parse_mode="MarkdownV2"
        )
    else:
        # Process messages from users for support requests
        logger.info(f"process_support_request {user_id=}")

        # Check for cancel command
        if message.text == '/cancel':
            await message.answer("Запрос в поддержку отменен.")
            return

        # Get user's active course
        active_course_id = None
        try:
            async with aiosqlite.connect(DB_FILE) as conn:
                cursor = await conn.execute(
                    """
                    SELECT course_id FROM user_courses
                    WHERE user_id = ? AND is_completed = 0
                    ORDER BY last_lesson_date DESC LIMIT 1
                    """,
                    (user_id,)
                )
                result = await cursor.fetchone()
                if result:
                    active_course_id = result[0]
        except Exception as e:
            logger.error(f"Database error while getting active course: {e}")
            await message.answer("Произошла ошибка при получении информации о вашем курсе.")
            return

        # Log and forward the support request
        log_details = f"Support request from user {user_id}. Active course: {active_course_id}. Message: {message.text[:100]}..."
        logger.info(log_details)
        await log_user_activity(user_id, "SUPPORT_REQUEST", log_details)

        # Отправляем сообщение в группу поддержки для администраторов
        try:
            forwarded_message = await bot.forward_message(
                chat_id=ADMIN_GROUP_ID,  # Используйте ADMIN_GROUP_ID
                from_chat_id=message.chat.id,
                message_id=message.message_id
            )

            # Добавляем кнопки "Ответить" и "Закрыть" к пересланному сообщению
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="Ответить", callback_data=f"reply_support:{user_id}:{forwarded_message.message_id}"),
                InlineKeyboardButton(text="Закрыть", callback_data=f"close_support:{user_id}:{forwarded_message.message_id}")
            ]])

            # Обновляем сообщение в группе поддержки с добавленными кнопками
            await bot.edit_message_reply_markup(
                chat_id=ADMIN_GROUP_ID,
                message_id=forwarded_message.message_id,
                reply_markup=keyboard
            )

        except Exception as e:
            logger.error(f"Ошибка при пересылке сообщения в поддержку: {e}")
            await message.answer("Произошла ошибка при отправке запроса в поддержку. Пожалуйста, попробуйте позже.")
            return

        await message.answer(escape_md(
            "✅ Ваше сообщение отправлено в поддержку. Мы ответим вам в ближайшее время.\n"
            "Вы можете продолжать пользоваться ботом."
        ))


# Активация курса по кодовому слову. Записывает пользователя на курс
@dp.message(Command("activate"))
async def cmd_activate(message: Message):
    """Handler for the /activate command to activate a course"""
    user_id = message.from_user.id
    logger.info(f"cmd_activate User {user_id} initiated activation process.")

    await message.answer(escape_md(
        "🔑 *Активация курса*\n\n"
        "Введите кодовое слово для активации курса.\n"
        "Для отмены введите /cancel."),
        parse_mode="MarkdownV2"
    )


@dp.message(Command("mycourses")) # Предоставляет кнопки для продолжения или повторного просмотра
@db_exception_handler # Показывает список активных и завершенных курсов # Разделяет курсы на активные и завершенные
async def cmd_mycourses(message: Message):
    """Показывает список активных и завершенных курсов с маппингом тарифов"""
    user_id = message.from_user.id
    logger.info(f"Обработка /mycourses для пользователя {user_id}")

    try:
        # Получаем данные через кэширующую функцию
        active_course = await get_course_status(user_id)

        if not active_course:
            await message.answer(
                "У вас пока нет активированных курсов.\n"
                "Используйте команду /activate для активации курса."
            )
            return

        # Распаковка данных курса
        course_id, title, version_id, current_lesson = active_course

        # Маппинг версий на названия тарифов
        TARIFF_NAMES = {
            "v1": "Соло",
            "v2": "Группа",
            "v3": "VIP"
        }

        # Формирование сообщения
        courses_text = (
            "📚 *Ваши курсы:*\n\n"
            f"*{title}*\n"
            f"🔑 Тариф: {TARIFF_NAMES.get(version_id, 'Базовый')}\n"
            f"📖 Прогресс: Урок {current_lesson}/[общее_количество]\n\n"
            "_Для продолжения нажмите кнопку ниже_"
        )

        # Создаем клавиатуру
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text=f"➡️ Продолжить {title}",
                callback_data=f"start_lesson:{course_id}:{current_lesson}"
            )]
        ])

        await message.answer(
            escape_md(courses_text),
            reply_markup=keyboard,
            parse_mode="MarkdownV2"
        )

    except Exception as e:
        logger.error(f"Ошибка в cmd_mycourses: {e}")
        await message.answer("Произошла ошибка при загрузке курсов. Попробуйте позже.")



@dp.message(Command("adm_approve_course"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler # Админ-команда для одобрения курса
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
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


@dp.message(Command("lesson"))
@db_exception_handler
async def cmd_lesson(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"cmd_lesson {user_id=}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT course_id, current_lesson FROM user_courses
                WHERE user_id = ? AND status = 'active'
            """, (user_id,))
            user_course = await cursor.fetchone()

            if user_course:
                course_id, current_lesson = user_course
                logger.info(f"{user_id=} {course_id=} {current_lesson=}")
                await message.answer(f"Отправляю урок {current_lesson} курса '{course_id}'...")
                success = await send_lesson_to_user(user_id, course_id, current_lesson) # Передаём current_lesson
                if not success:
                    await message.answer("Произошла ошибка при отправке урока. Пожалуйста, попробуйте позже.")
            else:
                await message.answer("У вас нет активных курсов. Пожалуйста, активируйте курс.")

    except Exception as e:
        logger.error(f"Error in cmd_lesson: {e}")
        await message.answer("Произошла ошибка при обработке команды. Пожалуйста, попробуйте позже.")



async def old_cmd_lesson(message: Message):
    """Handler for the /lesson command to get current lesson"""
    user_id = message.from_user.id
    logger.info(f"cmd_lesson {user_id=}")

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            """
            SELECT uc.course_id, c.title, uc.current_lesson, c.total_lessons
            FROM user_courses uc
            JOIN courses c ON uc.course_id = c.course_id
            WHERE uc.user_id = ? AND uc.is_completed = 0
            ORDER BY CASE WHEN uc.last_lesson_date IS NULL THEN 0 ELSE 1 END, uc.last_lesson_date DESC
            LIMIT 1
            """,
            (user_id,)
        )
        course_data = await cursor.fetchone()

    if not course_data:
        await message.answer(
            "У вас нет активных курсов. Используйте команду /activate, чтобы активировать курс."
        )
        return

    course_id, course_title, current_lesson, total_lessons = course_data

    # Send lesson
    await message.answer(f"Отправляю урок {current_lesson} курса '{course_title}'...")
    success = await send_lesson_to_user(message.from_user.id, course_id, current_lesson)

    if success:
        # Log lesson delivery
        await log_user_activity(
            user_id,
            "LESSON_RECEIVED",
            f"Course: {course_id}, Lesson: {current_lesson}"
        )
    else:
        await message.answer("Произошла ошибка при отправке урока. Пожалуйста, попробуйте позже.")



@dp.message(Command("progress"))
@db_exception_handler # Обработчик для команды просмотра прогресса по всем курсам
async def cmd_progress(message: Message):
    """Handler for the /progress command to show user's progress"""
    user_id = message.from_user.id
    logger.info(f"5555 cmd_progress {user_id} ")

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            """
            SELECT c.title, uc.current_lesson, c.total_lessons, 
                   ROUND((uc.current_lesson - 1) * 100.0 / c.total_lessons, 1) as progress_percent
            FROM user_courses uc
            JOIN courses c ON uc.course_id = c.course_id
            WHERE uc.user_id = ?
            ORDER BY uc.is_completed, uc.activation_date DESC
            """,
            (user_id,)
        )
        progress_data = await cursor.fetchall()

    if not progress_data:
        await message.answer(
            "У вас пока нет активированных курсов. Используйте команду /activate, чтобы активировать курс."
        )
        return

    # Create progress message
    progress_text = "📊 *Ваш прогресс обучения:*\n\n"

    for title, current_lesson, total_lessons, progress_percent in progress_data:
        progress_bar = generate_progress_bar(progress_percent)
        progress_text += (
            f"*{title}*\n"
            f"Урок: {current_lesson - 1}/{total_lessons} ({progress_percent}%)\n"
            f"{progress_bar}\n\n"
        )

    await message.answer(progress_text, parse_mode="MarkdownV2")


def generate_progress_bar(percent, length=10):
    """Generate a text progress bar"""
    filled = int(percent / 100 * length)
    bar = "▓" * filled + "░" * (length - filled)
    return bar


@dp.callback_query(lambda c: c.data.startswith("start_lesson:"))
@db_exception_handler # функция для отправки урока пользователю
async def start_lesson_callback(callback: CallbackQuery):
    """Обработка начала урока с проверкой активного курса через кэш"""
    try:
        user_id = callback.from_user.id
        callback_data = callback.data
        _, course_id, lesson_num = callback_data.split(":")

        logger.info(f"Запрос урока: user={user_id} course={course_id} lesson={lesson_num}")

        # Проверяем активный курс через кэширующую функцию
        active_course = await get_course_status(user_id)

        if not active_course or active_course[0] != course_id:
            await callback.answer("❌ Курс не активирован или недоступен")
            return

        # Получаем данные курса
        course_id, title, version_id, current_lesson = active_course

        # Маппинг тарифов
        TARIFF_NAMES = {
            "v1": "Соло",
            "v2": "Группа",
            "v3": "VIP"
        }

        # Подтверждаем обработку callback
        await callback.answer()

        # Обновляем сообщение с новым статусом
        await callback.message.edit_text(
            f"🔄 Подготавливаем урок {lesson_num} курса «{title}»\n"
            f"Тариф: {TARIFF_NAMES.get(version_id, 'Базовый')}"
        )

        # Отправляем урок
        success = await send_lesson_to_user(user_id, course_id, lesson_num)

        if success:
            await log_user_activity(
                user_id,
                "LESSON_STARTED",
                f"{title} (урок {lesson_num})"
            )
        else:
            await callback.message.answer("⛔ Ошибка отправки урока. Попробуйте позже.")

    except Exception as e:
        logger.error(f"Ошибка в start_lesson_callback: {e}")
        await callback.answer("⚠️ Произошла ошибка. Попробуйте снова.")


@dp.callback_query(lambda c: c.data.startswith("lesson_complete:"))
@db_exception_handler # # Обрабатывает нажатие "Урок изучен" Обработчик для колбэков от кнопок Проверяет необходимость домашнего задания
async def complete_lesson_callback(callback_query: CallbackQuery, course_id, lesson_num):
    user_id = callback_query.from_user.id
    logger.info(f"5557 complete_lesson_callback {user_id} ")
    async with aiosqlite.connect(DB_FILE) as conn:
        # Получаем информацию о типе проверки для курса пользователя
        cursor = await conn.execute("""
            SELECT cv.homework_check_type, uc.current_lesson, c.total_lessons, c.title,
                   (SELECT COUNT(*) FROM homework 
                    WHERE user_id = ? AND course_id = ? AND lesson_num = ? AND status = 'pending') as pending_homework
            FROM user_courses uc
            JOIN courses c ON uc.course_id = c.course_id
            JOIN course_versions cv ON uc.course_id = cv.course_id AND uc.version_id = cv.version_id
            WHERE uc.user_id = ? AND uc.course_id = ?
        """, (user_id, course_id, lesson_num, user_id, course_id))
        lesson_data = await cursor.fetchone()

        if not lesson_data or lesson_data[1] != lesson_num:
            await callback_query.answer("Этот урок не является вашим текущим уроком.")
            return

        homework_check_type, current_lesson, total_lessons, course_title, pending_homework = lesson_data

        # Проверяем необходимость домашнего задания
        cursor = await conn.execute(
            "SELECT requires_homework FROM lesson_content_map WHERE course_id = ? AND lesson_num = ?",
            (course_id, lesson_num)
        )
        homework_data = await cursor.fetchone()
        requires_homework = homework_data and homework_data[0] == 1

        if requires_homework:
            if homework_check_type == 'admin':
                if pending_homework == 0:  # Homework required but not submitted
                    await callback_query.message.edit_text(
                        "Урок отмечен как изученный. Теперь отправьте домашнее задание для проверки."
                    )
                    return
            elif homework_check_type == 'self':
                # Для самопроверки сразу считаем урок завершенным
                pass

        # Отмечаем урок как завершенный
        next_lesson = current_lesson + 1

        if next_lesson > total_lessons:
            # Завершение курса
            await conn.execute(
                "UPDATE user_courses SET is_completed = 1 WHERE user_id = ? AND course_id = ?",
                (user_id, course_id)
            )

            await callback_query.message.edit_text(
                f"🎉 Поздравляем! Вы завершили курс '{course_title}'!"
            )
            await log_user_activity(user_id, "COURSE_COMPLETED", f"Course: {course_id}")
        else:
            next_lesson_date = datetime.now() + timedelta(days=1)
            await conn.execute(
                """
                UPDATE user_courses 
                SET current_lesson = ?, next_lesson_date = ? 
                WHERE user_id = ? AND course_id = ?
                """,
                (next_lesson, next_lesson_date, user_id, course_id)
            )

            await callback_query.message.edit_text(
                f"✅ Урок {current_lesson} отмечен как изученный!\n"
                f"Следующий урок будет доступен {next_lesson_date.strftime('%d.%m.%Y')}."
            )
            await log_user_activity(
                user_id,
                "LESSON_COMPLETED",
                f"Course: {course_id}, Lesson: {current_lesson}"
            )
        await conn.commit()


@dp.message(F.text, F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def process_rejection_reason(message: Message):
    admin_id = message.from_user.id
    logger.info(f"5557 process_rejection_reason {admin_id} ")
    async with aiosqlite.connect(DB_FILE) as conn:
        # Получаем контекст администратора
        cursor = await conn.execute("""
            SELECT context_data FROM admin_context WHERE admin_id = ?
        """, (admin_id,))
        context_data = await cursor.fetchone()

        if not context_data:
            return  # Если нет контекста - игнорируем сообщение

        context = json.loads(context_data[0])

        if context.get("action") != "reject_hw":
            return  # Если действие не отклонение ДЗ - игнорируем

        # Сохраняем причину отклонения
        context["reason"] = message.text
        await conn.execute("""
            UPDATE admin_context SET context_data = ? WHERE admin_id = ?
        """, (json.dumps(context), admin_id))
        await conn.commit()

        # Выполняем отклонение
        await handle_homework_decision(
            CallbackQuery(
                id="fake_id",
                from_user=message.from_user,
                chat_instance="admin",
                data=f"reject_hw:{context['user_id']}:{context['course_id']}:{context['lesson_num']}"
            )
        )

@dp.callback_query(lambda c: c.data.startswith("approve_hw:") or c.data.startswith("reject_hw:"))
@db_exception_handler
async def handle_homework_decision(callback_query: CallbackQuery):
    action, user_id, course_id, lesson_num = callback_query.data.split(":")
    admin_id = callback_query.from_user.id
    user_id = int(user_id)
    logger.info(f"5558 handle_homework_decision action, {user_id=}, {course_id=}, {lesson_num=}  {admin_id=} ")

    async with aiosqlite.connect(DB_FILE) as conn:
        # Получаем данные о домашнем задании
        cursor = await conn.execute("""
            SELECT message_id FROM homework 
            WHERE user_id = ? AND course_id = ? AND lesson_num = ?
        """, (user_id, course_id, lesson_num))
        homework_data = await cursor.fetchone()

        if not homework_data:
            await callback_query.answer("Домашнее задание не найдено.")
            return

        message_id = homework_data[0]

        if action == "approve_hw":
            # Одобрение ДЗ
            await conn.execute("""
                UPDATE homework SET status = 'approved', admin_id = ?, decision_date = CURRENT_TIMESTAMP
                WHERE user_id = ? AND course_id = ? AND lesson_num = ?
            """, (admin_id, user_id, course_id, lesson_num))

            # Добавляем в галерею
            await conn.execute("""
                INSERT INTO homework_gallery (user_id, course_id, lesson_num, message_id, approved_by)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, course_id, lesson_num, message_id, admin_id))

            # Уведомляем пользователя
            await bot.send_message(
                user_id,
                f"✅ Ваше домашнее задание к уроку {lesson_num} курса '{course_id}' одобрено!"
            )


        elif action == "reject_hw":
            # Отклонение ДЗ
            # Проверяем есть ли комментарий админа
            context_cursor = await conn.execute("""
                SELECT context_data FROM admin_context WHERE admin_id = ?
            """, (admin_id,))
            context_data = await context_cursor.fetchone()

            if not context_data:
                # Если нет комментария - запрашиваем его
                await conn.execute("""
                    INSERT OR REPLACE INTO admin_context (admin_id, context_data)
                    VALUES (?, ?)
                """, (admin_id, json.dumps({
                    "action": "reject_hw",
                    "user_id": user_id,
                    "course_id": course_id,
                    "lesson_num": lesson_num
                })))
                await conn.commit()

                await callback_query.message.edit_text(
                    "📝 Пожалуйста, отправьте причину отклонения домашнего задания.\n"
                    "Для отмены введите /cancel"
                )
                return

            # Если есть комментарий - обрабатываем отклонение
            rejection_reason = json.loads(context_data[0])["reason"]

            await conn.execute("""
                UPDATE homework SET status = 'rejected', admin_id = ?, 
                decision_date = CURRENT_TIMESTAMP, rejection_reason = ?
                WHERE user_id = ? AND course_id = ? AND lesson_num = ?
            """, (admin_id, rejection_reason, user_id, course_id, lesson_num))

            # Удаляем из галереи если там есть
            await conn.execute("""
                DELETE FROM homework_gallery 
                WHERE user_id = ? AND course_id = ? AND lesson_num = ?
            """, (user_id, course_id, lesson_num))

            # Уведомляем пользователя
            await bot.send_message(
                user_id,
                f"❌ Ваше домашнее задание к уроку {lesson_num} курса '{course_id}' отклонено.\n"
                f"Причина: {rejection_reason}\n\n"
                "Вы можете отправить новое домашнее задание."
            )

        await conn.commit()

        # Перерисовываем меню администратора
        button_back = [ [InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]]
        keyboard = InlineKeyboardMarkup(inline_keyboard=button_back)
        await callback_query.message.edit_text( "Действие выполнено успешно.",
            reply_markup=keyboard   )


@dp.message(Command("mycourses"))
@db_exception_handler
async def cmd_mycourses(message: Message):
    user_id = message.from_user.id
    logger.info(f"5559 cmd_mycourses {user_id=} ")
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            """
            SELECT c.course_id, c.title, uc.current_lesson, c.total_lessons, uc.is_completed
            FROM user_courses uc
            JOIN courses c ON uc.course_id = c.course_id
            WHERE uc.user_id = ?
            ORDER BY uc.activation_date DESC
            """,
            (user_id,)
        )
        courses = await cursor.fetchall()

    if not courses:
        await message.answer(
            "У вас пока нет активированных курсов. Используйте команду /activate, чтобы активировать курс."
        )
        return

    keyboard = InlineKeyboardMarkup(row_width=1)
    active_courses_text = "📚 *Активные курсы:*\n"
    completed_courses_text = "\n🎓 *Завершенные курсы (доступны для повторного просмотра):*\n"
    has_active = False
    has_completed = False

    for course_id, title, current_lesson, total_lessons, is_completed in courses:
        if is_completed:
            status = "✅ Завершен"
            completed_courses_text += f"*{title}*\n{status}\n"
            keyboard.add(InlineKeyboardButton(
                text=f"📚 Повторить материалы '{title}'",
                callback_data=f"review_course:{course_id}"
            ))
            has_completed = True
        else:
            status = f"📝 Урок {current_lesson}/{total_lessons}"
            active_courses_text += f"*{title}*\n{status}\n"
            keyboard.add(InlineKeyboardButton(
                text=f"Продолжить '{title}'",
                callback_data=f"start_lesson:{course_id}:{current_lesson}"
            ))
            has_active = True

    response_text = ""
    if has_active:
        response_text += active_courses_text
    if has_completed:
        response_text += completed_courses_text

    await message.answer(escape_md(response_text),
        reply_markup=keyboard,
        parse_mode="MarkdownV2" )


@dp.message(Command("completed_courses")) # Показывает список завершенных курсов # Реализует пагинацию уроков
@db_exception_handler # Позволяет просматривать уроки с сниппетами
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

    await message.answer(escape_md( "📚 *Завершенные курсы:*"),
        reply_markup=keyboard,
        parse_mode="MarkdownV2" ) # Позволяет просматривать уроки со сниппетами


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
            SELECT c.channel_id, lcm.start_message_id, lcm.snippet
            FROM courses c
            JOIN lesson_content_map lcm ON c.course_id = lcm.course_id
            WHERE c.course_id = ? AND lcm.lesson_num = ?
        """, (course_id, lesson_num))
        lesson_data = await cursor.fetchone()

    if not lesson_data:
        await callback_query.answer("Урок не найден.")
        return

    channel_id, start_id, snippet = lesson_data

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
            from_chat_id=channel_id,
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
    logger.info(f"show_full_lesson {user_id=} ")
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT c.channel_id, lcm.start_message_id, lcm.end_message_id
            FROM courses c
            JOIN lesson_content_map lcm ON c.course_id = lcm.course_id
            WHERE c.course_id = ? AND lcm.lesson_num = ?
        """, (course_id, lesson_num))
        lesson_data = await cursor.fetchone()

    if not lesson_data:
        await callback_query.answer("Урок не найден.")
        return

    channel_id, start_id, end_id = lesson_data

    for msg_id in range(start_id, end_id + 1):
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=channel_id,
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
            SELECT lcm.lesson_num, c.channel_id, lcm.start_message_id, lcm.end_message_id
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
    for lesson_num, channel_id, start_id, end_id in lessons:
        keyboard.add(InlineKeyboardButton(
            text=f"Урок {lesson_num}",
            callback_data=f"review_lesson:{course_id}:{lesson_num}"
        ))

    await callback_query.message.edit_text(
        "Выберите урок для повторного просмотра:",
        reply_markup=keyboard
    )


@dp.callback_query(lambda c: c.data.startswith("review_lesson:"))
@db_exception_handler
async def review_lesson_callback(callback_query: CallbackQuery):
    _, course_id, lesson_num = callback_query.data.split(":")
    user_id = callback_query.from_user.id
    lesson_num = int(lesson_num)
    logger.info(f"review_lesson_callback {user_id=} {lesson_num=}")

    # Получаем данные урока
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT c.channel_id, lcm.start_message_id, lcm.end_message_id
            FROM courses c
            JOIN lesson_content_map lcm ON c.course_id = lcm.course_id
            WHERE lcm.course_id = ? AND lcm.lesson_num = ?
        """, (course_id, lesson_num))
        lesson_data = await cursor.fetchone()

    if not lesson_data:
        await callback_query.answer("Урок не найден.")
        return

    channel_id, start_id, end_id = lesson_data

    # Отправляем урок пользователю
    await callback_query.answer("Отправка урока...")
    for msg_id in range(start_id, end_id + 1):
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=channel_id,
                message_id=msg_id
            )
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Error sending message {msg_id} to user {user_id}: {e}")

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


@dp.callback_query(lambda c: c.data.startswith("review_prev:") or c.data.startswith("review_next:"))
@db_exception_handler
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


@dp.callback_query(lambda c: c.data.startswith("submit_homework:"))
@db_exception_handler # обработка отправки ДЗ
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


# обработка содержимого ДЗ
@db_exception_handler
async def process_homework_submission(message: Message):
    """Process homework submission from users"""
    user_id = message.from_user.id
    logger.info(f"process_homework_submission {user_id=} ")
    # Get course and lesson from context
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            "SELECT context_data FROM user_context WHERE user_id = ?",
            (user_id,)
        )
        context_data = await cursor.fetchone()

        if not context_data:
            await message.answer("Произошла ошибка. Пожалуйста, начните отправку домашнего задания заново.")
            return

        context = json.loads(context_data[0])
        course_id = context.get("course_id")
        lesson_num = context.get("lesson_num")

        if not course_id or not lesson_num:
            await message.answer("Произошла ошибка. Пожалуйста, начните отправку домашнего задания заново.")
            return

        # Get course info
        cursor = await conn.execute(
            "SELECT title FROM courses WHERE course_id = ?",
            (course_id,)
        )
        course_data = await cursor.fetchone()

        if not course_data:
            await message.answer("Курс не найден.")
            return

        course_title = course_data[0]

    # Forward homework to admin group
    try:
        # Create message for admins
        admin_message = (
            f"📝 *Новое домашнее задание*\n"
            f"👤 Пользователь: {message.from_user.full_name} (ID: `{user_id}`)\n"
            f"📚 Курс: {course_title} (ID: `{course_id}`)\n"
            f"📖 Урок: {lesson_num}\n\n"
        )

        # Send admin message
        admin_msg = await bot.send_message(
            ADMIN_GROUP_ID,
            admin_message,
            parse_mode="MarkdownV2"
        )

        # Forward the actual homework content
        forwarded_msg = await message.forward(ADMIN_GROUP_ID)

        # Add approval buttons
        keyboard = InlineKeyboardMarkup(row_width=2)
        keyboard.add(
            InlineKeyboardButton(text="✅ Принять", callback_data=f"approve_hw:{user_id}:{course_id}:{lesson_num}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_hw:{user_id}:{course_id}:{lesson_num}")
        )

        await bot.send_message(
            ADMIN_GROUP_ID,
            "Действия с домашним заданием:",
            reply_markup=keyboard,
            reply_to_message_id=forwarded_msg.message_id
        )

        # Save homework submission to database
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute(
                """
                INSERT INTO homework 
                (user_id, course_id, lesson_num, message_id, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (user_id, course_id, lesson_num, forwarded_msg.message_id)
            )
            await conn.commit()

        # Confirm receipt to user
        await message.answer(escape_md(
            "✅ Ваше домашнее задание отправлено на проверку. Мы уведомим вас о результатах." ))

        # Log homework submission
        await log_user_activity(
            user_id,
            "HOMEWORK_SUBMITTED",
            f"Course: {course_id}, Lesson: {lesson_num}"
        )


    except Exception as e:
        logger.error(f"Error processing homework: {e}")
        await message.answer("Произошла ошибка при отправке домашнего задания. Пожалуйста, попробуйте позже.")




def get_main_menu_keyboard():
    """Создает клавиатуру главного меню"""
    logger.info(f"get_main_menu_keyboard ")
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    keyboard.add(
        KeyboardButton("/mycourses"),  # Мои курсы
        KeyboardButton("/lesson")     # Текущий урок
    )
    keyboard.add(
        KeyboardButton("/progress"),  # Прогресс
        KeyboardButton("/support")    # Поддержка
    )
    keyboard.add(
        KeyboardButton("/help")       # Помощь
    )
    return keyboard


def get_main_menu_inline_keyboard():
    """Создает Inline-клавиатуру главного меню."""
    # Создаем список кнопок
    buttons = [
        [   InlineKeyboardButton(text="📚 Мои курсы", callback_data="menu_mycourses"),
            InlineKeyboardButton(text="📖 Текущий урок", callback_data="menu_current_lesson")  ],
        [  InlineKeyboardButton(text="📊 Прогресс", callback_data="menu_progress"),
           InlineKeyboardButton(text="📞 Поддержка", callback_data="menu_support")   ],
        [ InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help")   ]
    ]
    # Создаем объект клавиатуры с кнопками
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Логируем структуру клавиатуры

    logger.info("Inline keyboard created successfully.")

    return keyboard


@dp.message(Command("help"))
async def help_command(message: types.Message):
    """Handles the /help command."""
    help_text = (
        "Доступные команды:\n"
        "/start - Запуск бота\n"
        "/help - Получение справки\n"
        # Добавьте другие команды и их описания
    )
    await message.answer(help_text)



@dp.message(F.text)
@db_exception_handler
async def process_activation_code(message: Message):
    """Processes the activation code and activates the course if valid."""
    user_id = message.from_user.id
    activation_code = message.text.strip().lower()
    logger.info(f"Попытка активации курса для пользователя {user_id} с кодом: '{activation_code}' ")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT course_id, version_id
                FROM course_versions
                WHERE activation_code = ?
            """, (activation_code,))
            result = await cursor.fetchone()
        logger.info(f"вроде {result=}")
        if result:
            course_id, version_id = result
            logger.info(f"ура {course_id=} {version_id=}")
            # Упростим активацию - не используем "тип" курса, а версию
            try:
                async with aiosqlite.connect(DB_FILE) as conn:
                    # Удаляем старую запись, если она есть
                    await conn.execute("""
                        DELETE FROM user_courses
                        WHERE user_id = ? AND course_id = ?
                    """, (user_id, course_id))

                    # Вставляем новую запись со статусом 'active'
                    await conn.execute("""
                        INSERT INTO user_courses
                        (user_id, course_id, version_id, status, activation_date, current_lesson)
                        VALUES (?, ?, ?, 'active', datetime('now'), 1)
                    """, (user_id, course_id, version_id))

                    await conn.commit()
                    logger.info(f"записали методом INSERT INTO user_courses {user_id=} {course_id=} {version_id=}")
            except Exception as e:
                logger.error(f"Ошибка при вставке user_courses для {user_id=}: {e}")
                await message.answer("Ошибка при активации курса.")
                return

            logger.info(f"process_activation_code")
            logger.info(f"ща активируем всё тут")
            await log_user_activity(user_id, "COURSE_ACTIVATION", f"Course: {course_id}, Version: {version_id}")
            msg = f"Курс успешно активирован!\n📚 {course_id}\n📋 Версия: {version_id}\nИспользуйте команду /lesson, чтобы начать обучение"
            # убрал escape_md
            await message.answer(escape_md(msg), parse_mode='MarkdownV2')

        else:
            await message.answer("❌ Неверный код активации.")
            logger.warning(f"Неверный код активации введен пользователем {user_id}: {activation_code}")

    except Exception as e:
        logger.error(f"Ошибка в process_activation_code: {e}")
        await message.answer("Произошла ошибка при активации курса.")



@dp.message(F.text)  # Обработчик текстовых сообщений
async def check_activation_code(message: types.Message):
    """Checks if the message is an activation code."""
    activation_code = message.text.strip()
    logger.info(f"Попытка активации с кодом: {activation_code}")

    # Поиск кода активации в базе данных
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            "SELECT course_id, version_id FROM course_versions WHERE activation_code = ?",
            (activation_code,)
        )
        result = await cursor.fetchone()

    if result:
        course_id, version_id = result
        user_id = message.from_user.id
        logger.info(f"Код активации найден. {user_id=}, {course_id=}, {version_id=}")

        # Проверяем, есть ли у пользователя уже этот курс
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute(
                "SELECT 1 FROM user_courses WHERE user_id = ? AND course_id = ? AND version_id = ?",
                (user_id, course_id, version_id)
            )
            existing_enrollment = await cursor.fetchone()

        if existing_enrollment:
            await message.answer("У вас уже есть этот курс.")
            return

        # Записываем пользователя на курс
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute(
                "INSERT INTO user_courses (user_id, course_id, version_id) VALUES (?, ?, ?)",
                (user_id, course_id, version_id)
            )
            await conn.commit()

        await message.answer(f"Поздравляем! Вы успешно активировали курс {course_id} (версия {version_id}).")
        await log_user_activity(user_id, "activate_course", f"course_id={course_id}, version_id={version_id}")
    else:
        await message.answer("Неверное кодовое слово. Попробуйте еще раз или свяжитесь с поддержкой.")
        logger.info(f"Неверный код активации: {activation_code}")


async def send_startup_message(bot: Bot, admin_group_id: int):
    """Отправка админ-сообщения без MarkdownV2"""
    logger.info(f"Sending startup message to admin group: {admin_group_id}")
    global settings
    try:
        channel_reports = []

        for raw_id, course_name in settings["channels"].items():
            logger.info(f"14 check_channel_access  raw_id={raw_id}  course_name={course_name}")
            report = await check_channel_access(bot, raw_id, course_name)
            #logger.info(f"16 check_channel_access  report={report}")
            channel_reports.append(report)  # не экранируем report
            #channel_reports.append(escape_md(report))  # Экранируем report


        logger.info(f"17 channel_reports={channel_reports}")
        jjj="\n".join(channel_reports)
        message_text = (f"Бот запущен\n\nСтатус каналов курсов:\n{jjj}\n\nУправление курсами:\n- Добавить курс: `/add_course <channel_id> <course_id> <code1> <code2> <code3>`\n  - Пример: `/add_course -1002014225295 femininity роза фиалка лепесток`" )
        # экранируем минусы в ID канала
        message_text = message_text.replace('-', '\\-')
        logger.info(f" 177 {message_text=}")
        await bot.send_message(admin_group_id, message_text)  # Убрали parse_mode
        logger.info("Стартовое сообщение отправлено администраторам")

    except Exception as e:
        logger.error(f"Ошибка в send_startup_message: {e}") # строка 2142



@dp.callback_query(F.data == "menu_current_lesson")
@db_exception_handler
async def process_current_lesson(callback: CallbackQuery):
    user_id = callback.from_user.id
    logger.info(f"Processing current lesson for {user_id}")

    try:
        # Получаем текущий курс пользователя
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT c.course_id, uc.current_lesson 
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ? AND uc.status = 'active'
                LIMIT 1
            """, (user_id,))
            course_data = await cursor.fetchone()

        logger.info(f"222 текущий урок {course_data=}")
        if not course_data:
            await callback.answer("У вас нет активных курсов!")
            return

        course_id, current_lesson = course_data

        # Получаем channel_id, start_message_id и end_message_id из lesson_content_map
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT c.channel_id, lcm.start_message_id, lcm.end_message_id
                FROM lesson_content_map lcm
                JOIN courses c ON lcm.course_id = c.course_id
                WHERE lcm.course_id = ? AND lcm.lesson_num = ?
            """, (course_id, current_lesson))
            lesson_data = await cursor.fetchone()

        logger.info(f"lesson_data={lesson_data}")

        if not lesson_data:
            await callback.answer("Материалы урока не найдены!")
            return

        channel_id, start_id, end_id = lesson_data
        messages_to_forward = min(DEFAULT_COUNT_MESSAGES, end_id - start_id + 1)

        # Пересылаем сообщения
        for msg_id in range(start_id, start_id + messages_to_forward):
            try:
                await bot.forward_message(
                    chat_id=user_id,
                    from_chat_id=int(channel_id),
                    message_id=msg_id
                )
            except TelegramBadRequest as e:
                logger.error(f"Message forwarding error: {e}")
                await callback.answer(f"Ошибка пересылки сообщения {msg_id}")

        await callback.answer("Материалы урока отправлены!")

    except Exception as e:
        logger.error(f"Error in process_current_lesson: {e}")
        await callback.answer("Ошибка при получении урока!")


# Обработчик последний - чтобы не мешал другим обработчикам работать. Порядок имеет значение
@dp.message(F.text)  # Фильтр только для текстовых сообщений
async def process_message(message: types.Message):
    """Проверяет код активации и выдаёт уроки, если всё окей"""
    user_id = message.from_user.id
    code = message.text.strip().lower()  # Приводим к нижнему регистру
    logger.info(f"7 process_message Проверяем код: {code}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT c.course_id, c.version_id 
                FROM course_activation_codes cac
                JOIN course_versions c ON cac.course_id = c.course_id
                WHERE cac.code_word = ?
            """,(code,))
            course_data = await cursor.fetchone()

            logger.info(f"7 1318 course_data:Найдены данные курса: {course_data}")

        if course_data:
            course_id, version_id = course_data
            try:
                async with aiosqlite.connect(DB_FILE) as conn:
                    # Проверим, не активирован ли уже этот курс
                    cursor = await conn.execute("""
                        SELECT 1 FROM user_courses
                        WHERE user_id = ? AND course_id = ?
                    """, (user_id, course_id))
                    existing_enrollment = await cursor.fetchone()

                    if existing_enrollment:
                        await message.answer("Этот курс уже активирован.", reply_markup=get_main_menu_inline_keyboard())
                    else:
                        await conn.execute("""
                            INSERT INTO user_courses (user_id, course_id, version_id, status, activation_date)
                            VALUES (?, ?, ?, 'active', CURRENT_TIMESTAMP)
                        """, (user_id, course_id, version_id))

                        await conn.commit()
                        await log_user_activity(user_id, "COURSE_ACTIVATION", f"Курс {course_id} активирован с кодом {message.text.strip()}")
                        await message.answer("Курс успешно активирован!\nИспользуйте кнопки ниже для навигации.", reply_markup=get_main_menu_inline_keyboard())
            except Exception as e:
                logger.error(f"Ошибка при активации курса: {e}")
                await message.answer("Произошла ошибка при активации курса.")
        else:
            await message.answer("Неверное 333 кодовое слово. Попробуйте еще раз или свяжитесь с поддержкой.")
    except Exception as e:
        logger.error(f"Общая ошибка в process_message: {e}")
        await message.answer("Произошла общая ошибка. Пожалуйста, попробуйте позже.")

# задержки к проверке пачки каналов
async def check_channel_access(bot: Bot, raw_id: str, course_name: str):
    """Проверка доступа с корректным экранированием"""
    try:
        channel_id = int(raw_id)
        chat = await bot.get_chat(channel_id)

        # Экранируем title перед использованием в MarkdownV2
        escaped_title = escape_md(chat.title)

        # Генерация ссылки (для каналов с username)
        if chat.username:
            link = f"[{escaped_title}](t.me/{chat.username})"
        else:
            link = f"[{escaped_title}](t.me/c/{str(chat.id).replace('-100', '')})"

        return (
            f"{'Проверка пройдена'} {link}\n" # убрал эмодзи
            f"   Full ID: `{raw_id}`"
        )
    except TelegramBadRequest as e:
        return f"Ошибка: {course_name} | ID: {raw_id}\n   Подробнее: {str(e)}" # убрал эмодзи




# Запуск бота
async def main():
    global settings

    # Инициализация базы данных
    await init_db()
    await send_startup_message(bot, ADMIN_GROUP_ID)  # Отправка сообщения в группу администраторов

    settings = load_settings()
    logger.info(f"555 Settings loaded . {settings=}")
    await import_settings_to_db(settings)

    asyncio.create_task(check_and_schedule_lessons())

    # Проверяем доступ к каналам и заполняем lesson_content_map для каждого курса
    for channel_id, course_id in settings['channels'].items():
        try:
            channel_id_int = int(channel_id)  # Преобразуем channel_id в int
            report = await check_channel_access(bot, channel_id, course_id)
            logger.info(report)  # Логируем отчет о проверке канала

            async with aiosqlite.connect(DB_FILE) as conn:
                logger.info(f"Проверяем lesson_content_map для course_id='{course_id}'")
                cursor = await conn.execute("SELECT COUNT(*) FROM lesson_content_map WHERE course_id = ?",
                                            (course_id,))
                count = await cursor.fetchone()
                logger.info(f"Count result: {count}")  # Добавляем этот лог
                if count is not None and count[0] == 0:  # Обратите внимание на эту проверку
                    logger.warning(f"Таблица lesson_content_map пуста для курса {course_id}. Запускаем заполнение.")
                    logger.info(f"3/3 Заполнение таблицы lesson_content_map для  {channel_id=}  {channel_id_int=} '{course_id=}'...")
                    await fill_lesson_content_map(bot, channel_id_int, course_id)  # Используем int и передаём bot
                else:
                    logger.info(f"Таблица lesson_content_map уже заполнена для курса {course_id}.")
        except Exception as e:
            logger.error(f"Ошибка при обработке канала {channel_id}: {e}")


    # Запуск бота
    logger.info(f"Бот успешно запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())

# ЭТО ПАМЯТКА ДЛЯ ПОНИМАНИЯ ТИПОВ
#user = await bot.get_chat(user_id) # types.Chat
#chat = await bot.get_chat(ADMIN_GROUP_ID) # types.Chat
#msg = await bot.send_message(chat.id, f"{user_id=}") # types.Message
#chat_info = await bot.get_chat_member(chat.id, user_id) # types.ChatMember
#photo = await bot.get_chat_photos(chat.id) # types.ChatPhotos
#full_chat = await bot.get_chat_full_info(chat.id) # types.ChatFullInfo
#await bot.ban_chat_member(chat.id, user_id) # bool
#await bot.copy_message(user_id, chat.id, msg.message_id ) # types.MessageId
#webhook = await bot.get_webhook_info() # types.WebhookInfo
#r = await bot.send_photo(ADMIN_GROUP_ID, photo=photo.photos.file_id, caption=f"{photo=}")
#await bot.copy_message(user_id, chat.id, r.message_id )

