import asyncio, logging, json, random, string, os, re, aiosqlite, datetime, shutil
import functools
from functools import lru_cache
from logging.handlers import RotatingFileHandler
#from aiogram.utils.text_decorations import escape_md нет в природе. сами напишем
#from aiogram.utils.markdown import quote  # Для MarkdownV2 - todo попробовать
# Или
#from aiogram.utils.text_decorations import html  # Для HTML
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.filters import Command, CommandStart, BaseFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup,
                           KeyboardButton, Message, CallbackQuery, ChatFullInfo)
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


def load_settings():
    """Загружает настройки из файла settings.json."""
    logger.info(f"333 load_settings ")
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
                logger.info(f"Настройки успешно загружены. {settings['groups']=}")
                return settings
        except json.JSONDecodeError:
            logger.error("Ошибка при декодировании JSON.")
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

            # Insert or replace into course_versions table
            await conn.execute("""
                INSERT OR REPLACE INTO course_versions (course_id, version_id, title, price, description)
                VALUES (?, ?, ?, ?, ?)
            """, (course_id, "v1", f"{course_id} basic", 0, f"Описание basic версии для {course_id}"))

            await conn.execute("""
                INSERT OR REPLACE INTO course_versions (course_id, version_id, title, price, description)
                VALUES (?, ?, ?, ?, ?)
            """, (course_id, "v2", f"{course_id} group", 1000, f"Описание group версии для {course_id}"))

            await conn.execute("""
                INSERT OR REPLACE INTO course_versions (course_id, version_id, title, price, description)
                VALUES (?, ?, ?, ?, ?)
            """, (course_id, "v3", f"{course_id} vip", 5000, f"Описание vip версии для {course_id}"))

            # Insert or ignore into course_activation_codes table
            await conn.execute("""
                INSERT OR IGNORE INTO course_activation_codes (code_word, course_id, course_type, price_rub)
                VALUES (?, ?, ?, ?)
            """, (code1, course_id, "v1", 0))

            await conn.execute("""
                INSERT OR IGNORE INTO course_activation_codes (code_word, course_id, course_type, price_rub)
                VALUES (?, ?, ?, ?)
            """, (code2, course_id, "v2", 1000))

            await conn.execute("""
                INSERT OR IGNORE INTO course_activation_codes (code_word, course_id, course_type, price_rub)
                VALUES (?, ?, ?, ?)
            """, (code3, course_id, "v3", 5000))

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
    """Initialize the database with required tables"""
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
                    status TEXT DEFAULT 'active', -- pending, active, completed
                    current_lesson INTEGER DEFAULT 1,
                    is_completed INTEGER DEFAULT 0,
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
                    text TEXT,
                    file_id TEXT,
                    level integer DEFAULT 1,
                    message_id INTEGER NOT NULL,
                    is_forwarded BOOLEAN DEFAULT FALSE,
                    forwarded_from_chat_id INTEGER,
                    forwarded_message_id INTEGER,
                    snippet TEXT COLLATE NOCASE, -- Сниппет урока todo: 
                    is_bouns BOOLEAN DEFAULT FALSE,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (course_id) REFERENCES courses(course_id)
                )
            ''')
            await conn.commit()

            # Создаем таблицу activation_codes
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS course_activation_codes (
                    code_word TEXT PRIMARY KEY,
                    course_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    course_type TEXT, 
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



@db_exception_handler # Отправка урока пользователю не используется же? удалить
async def send_lesson_to_user(user_id, course_id, lesson_num):
    """Send lesson content to a user from the group chat"""
    logger.info(f"58585858585858585858 send_lesson_to_user {user_id=} {course_id=} {lesson_num=}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Get lesson content range from the lesson_content_map table
            cursor = await conn.execute("""
                SELECT c.group_id, lcm.start_message_id, lcm.end_message_id
                FROM lesson_content_map lcm
                JOIN courses c ON c.course_id = lcm.course_id
                WHERE lcm.course_id = ? AND lcm.lesson_num = ?
            """, (course_id, lesson_num))
            lesson_data = await cursor.fetchone()
            logger.info(f"xZDfgszgd {lesson_data=}")

            if lesson_data:
                group_id, start_id, end_id = lesson_data
                logger.info(f"5557 {group_id=}, {start_id=}, {end_id=}")
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
                    from_chat_id=group_id,
                    message_id=msg_id
                )
                logger.info(f"558 {msg_id=}")
                await asyncio.sleep(0.5)  # Prevent flooding
            except Exception as e:
                logger.error(f"Error sending message {msg_id} to user {user_id}")
                await bot.send_message(user_id,
                                       "Произошла ошибка при отправке одного из уроков. Пожалуйста, попробуйте позже или обратитесь к администратору.")
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
        await bot.send_message(user_id,
                               "Произошла общая ошибка при отправке урока. Пожалуйста, попробуйте позже или обратитесь к администратору.")
        return False


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


# фоновая задача для проверки и отправки уведомлений о новых уроках.
@db_exception_handler
async def old_check_and_schedule_lessons():
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
                        logger.info(
                            f"Scheduled lesson notification sent for user {user_id}, course {course_id}, lesson {current_lesson}")

                        # Log user activity
                        await log_user_activity(user_id, "LESSON_AVAILABLE",
                                                f"Course: {course_id}, Lesson: {current_lesson}")

                    except Exception as e:
                        logger.error(f"Failed to process or send scheduled lesson to user {user_id}: {e}")
                await conn.commit()

        except Exception as e:
            logger.error(f"General error in check_and_schedule_lessons: {e}")

        logger.info("Background task: check_and_schedule_lessons completed one cycle, sleeping for 60 seconds.")

        # Check every minute
        await asyncio.sleep(60)


async def get_courses_list():
    """Получает список курсов из базы данных."""
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT course_id, title FROM courses")
        courses = await cursor.fetchall()
    return courses



def generate_progress_bar(percent, length=10):
    """Generate a text progress bar"""
    filled = int(percent / 100 * length)
    bar = "▓" * filled + "░" * (length - filled)
    return bar


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
            "✅ Ваше домашнее задание отправлено на проверку. Мы уведомим вас о результатах."))

        # Log homework submission
        await log_user_activity(
            user_id,
            "HOMEWORK_SUBMITTED",
            f"Course: {course_id}, Lesson: {lesson_num}"
        )


    except Exception as e:
        logger.error(f"Error processing homework: {e}")
        await message.answer("Произошла ошибка при отправке домашнего задания. Пожалуйста, попробуйте позже.")


from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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


def get_main_menu_keyboard():
    """Создает клавиатуру главного меню"""
    logger.info(f"get_main_menu_keyboard ")
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=False)
    keyboard.add(
        KeyboardButton("/mycourses"),  # Мои курсы
        KeyboardButton("/lesson")  # Текущий урок
    )
    keyboard.add(
        KeyboardButton("/progress"),  # Прогресс
        KeyboardButton("/support")  # Поддержка
    )
    keyboard.add(
        KeyboardButton("/help")  # Помощь
    )
    return keyboard


@db_exception_handler # 08-04
async def get_main_menu_inline_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Создает Inline-клавиатуру главного меню."""
    logger.info("get_main_menu_inline_keyboard новая")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT course_id FROM user_courses WHERE user_id = ? AND status = 'active'
            """, (user_id,))
            user_course = await cursor.fetchone()

            if not user_course:
                # User has no active courses
                buttons = [
                    [InlineKeyboardButton(text="📚 Мои курсы", callback_data="menu_mycourses")],
                    [InlineKeyboardButton(text="📞 Поддержка", callback_data="menu_support")],
                    [InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help")]
                ]

            else:
                course_id = user_course[0]
                # Create a list of buttons
                buttons = [
                    [InlineKeyboardButton(text="📚 Мои курсы", callback_data="menu_mycourses"),
                     InlineKeyboardButton(text="📖 Текущий урок", callback_data=f"menu_current_lesson:{course_id}")],
                    [InlineKeyboardButton(text="📊 Прогресс", callback_data="menu_progress"),
                     InlineKeyboardButton(text="📞 Поддержка", callback_data="menu_support")],
                    [InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help")]
                ]

            # Create a keyboard object with buttons
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

            # Log keyboard structure
            logger.info("Inline keyboard created successfully.")

            return keyboard

    except Exception as e:
        logger.error(f"Error creating inline keyboard: {e}")
        return None



# todo убрали send_startup_message
async def old_send_startup_message(bot: Bot, admin_group_id: int):
    """Отправка админ-сообщения без MarkdownV2"""
    logger.info(f"Sending startup message to admin group: {admin_group_id}")
    try:
        group_reports = []
        kolhoz=settings["groups"].items()
        logger.info(f"kolhoz={kolhoz}")
        for raw_id, group_name in kolhoz:
            logger.info(f"14 check_groups_access  raw_id={raw_id}  gr.name={group_name}")
            report = await check_group_access(bot, raw_id, group_name)
            group_reports.append(report)  # не экранируем report

        logger.info(f"17 group_reports={group_reports}")
        jjj = "\n".join(group_reports)
        message_text = (
            f"Бот запущен\n\nСтатус групп курсов:\n{jjj}\n\nможно: /add_course <group_id> <course_id> <code1> <code2> <code3>")
        # экранируем минусы в ID канала
        #message_text = message_text.replace('-', '\\-')
        logger.info(f" 177 {message_text=}")
        await bot.send_message(admin_group_id, message_text)  # Убрали parse_mode
        logger.info("Стартовое сообщение отправлено администраторам")

    except Exception as e:
        logger.error(f"Ошибка в send_startup_message: {e}")  # строка 2142


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
    logger.info(f"Saving message {message.message_id} from group {group_id}")
    text = message.text or ""
    user_id = message.from_user.id if message.from_user else None
    file_id = message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else None)

    # Extract lesson markers
    start_lesson_match = re.search(r"\*START_LESSON (\d+)", text)
    end_lesson_match = re.search(r"\*END_LESSON (\d+)", text)
    hw_start_match = re.search(r"\*HW_START", text)
    hw_end_match = re.search(r"\*HW_END", text)
    course_end_match = re.search(r"\*COURSE_END", text)

    lesson_num = None
    is_homework = False

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
        if text.startswith("*Курс"):
            course_snippet = extract_course_snippet(text)
            course_title = extract_course_title(text)
            # Update course title and snippet
            await conn.execute("""
                UPDATE courses
                SET title = ?, description = ?
                WHERE course_id = ?
            """, (course_title, course_snippet, group_id))
            await conn.commit()
            logger.info(f"Updated course title and snippet for course {group_id}")

        # Save the message to the database
        await conn.execute("""
            INSERT INTO group_messages (
                group_id, message_id, content_type, text, file_id,
                is_forwarded, forwarded_from_chat_id, forwarded_message_id,
                course_id, lesson_num, is_homework
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            group_id, message.message_id, message.content_type, text,
            file_id, message.forward_origin is not None, message.forward_origin.chat.id if message.forward_origin else None,
            message.forward_origin.message_id if message.forward_origin else None,
            group_id, lesson_num, is_homework
        ))
        await conn.commit()

    # Store information about the last message
    last_message_info[group_id] = {"lesson_num": lesson_num}

    logger.info(
        f"Message saved: group_id={group_id}, message_id={message.message_id}, lesson={lesson_num}, is_homework={is_homework} {file_id=}")


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
    """Импортирует настройки (каналы и коды активации) из dict в базу данных, если их там нет."""
    logger.info("import_settings_to_db with settings from code")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            for group_id, course_id in settings.get("groups", {}).items():
                # Добавляем курс в базу данных, если его нет
                cursor = await conn.execute("SELECT 1 FROM courses WHERE course_id = ?", (course_id,))
                if not await cursor.fetchone():
                    await conn.execute("""
                        INSERT INTO courses (course_id, group_id, title)
                        VALUES (?, ?, ?)
                    """, (course_id, group_id, course_id))
                    logger.info(f"Курс {course_id} добавлен в базу данных.")

                # Добавляем коды активации для курса
                for version in ["v1", "v2", "v3"]:
                    code = next(
                        (
                            code
                            for code, info in settings["activation_codes"].items()
                            if info == f"{course_id}:{version}"
                        ),
                        None,
                    )

                    if code:
                        # Проверяем, есть ли уже этот код в базе
                        cursor = await conn.execute(
                            "SELECT 1 FROM course_activation_codes WHERE code_word = ?", (code,)
                        )
                        existing_code = await cursor.fetchone()

                        if not existing_code:
                            # Получаем цену курса для этой версии
                            price = 0  # По умолчанию
                            if version == "v2":
                                price = 1000
                            elif version == "v3":
                                price = 5000

                            # Добавляем код активации в базу
                            await conn.execute(
                                """
                                INSERT INTO course_activation_codes (code_word, course_id, course_type, price_rub)
                                VALUES (?, ?, ?, ?)
                                """,
                                (code, course_id, version, price),
                            )
                            logger.info(
                                f"Добавлен код активации {code} для курса {course_id}, версия {version}"
                            )
                        else:
                            logger.info(
                                f"Код активации {code} для курса {course_id}, версия {version} уже существует в базе"
                            )
                await conn.commit()  # Не забудьте сделать commit для сохранения изменений
            logger.info("Импорт настроек в базу данных завершен")

    except Exception as e:
        logger.error(f"Ошибка при импорте настроек в базу данных: {e}")


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






# Пользовательский фильтр для проверки ID группы
class IsCourseGroupFilter(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.chat.id in COURSE_GROUPS

#=================================================   обработчики сообщений   ====================================================


#@dp.message(F.chat.id.in_(COURSE_GROUPS))
@dp.message(IsCourseGroupFilter())
@db_exception_handler # Обработчик новых сообщений в группах курсов
async def handle_group_message(message: Message):
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
    except Exception as e:
        await message.answer(f"Ошибка: {e}")


@dp.callback_query(lambda c: c.data in ["export_db", "import_db"])
async def handle_admin_actions(callback: CallbackQuery):
    if callback.data == "export_db":
        await export_db(callback.message)
    elif callback.data == "import_db":
        await import_db(callback.message)

@dp.message(Command("export_db"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def export_db(message: types.Message):  # types.Message instead of Message
    """Экспорт данных из базы данных в JSON-файл. Только для администраторов."""
    logger.info("Получена команда /export_db")

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
    logger.info("Получена команда /import_db")

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


@dp.message(F.text, F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def process_rejection_reason(message: Message):
    admin_id = message.from_user.id
    logger.info(f"5557 process_rejection_reason {admin_id} ")
    async with aiosqlite.connect(DB_FILE) as conn:
        # Получаем контекст администратора todo: вспомнить бы что это
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
        button_back = [[InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")]]
        keyboard = InlineKeyboardMarkup(inline_keyboard=button_back)
        await callback_query.message.edit_text("Действие выполнено успешно.",
                                               reply_markup=keyboard)


@dp.message(F.reply_to_message, F.chat.id == ADMIN_GROUP_ID)
async def handle_support_reply(message: types.Message):
    """Пересылка ответа от админа пользователю."""
    global user_support_state
    user_id = user_support_state.get(message.reply_to_message.forward_from.id, {}).get("user_id")
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
                "message_interval": 8,
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






@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    """Админ-панель для управления курсами."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="View Courses", callback_data="admin_view_courses")]
    ])
    await message.answer("Admin Panel", reply_markup=keyboard)

@dp.callback_query(F.data == "admin_view_courses")
async def admin_view_courses(query: types.CallbackQuery):
    """Просмотр списка курсов."""
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

@dp.callback_query(lambda c: c.data.startswith("admin_edit_course:"))
async def admin_edit_course(query: types.CallbackQuery):
    """Редактирование курса."""
    course_id = query.data.split(":")[1]
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

@dp.callback_query(lambda c: c.data.startswith("admin_edit_lesson:"))
async def admin_edit_lesson(query: types.CallbackQuery):
    course_id, lesson_num = query.data.split(":")[1], query.data.split(":")[2]
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

@dp.callback_query(lambda c: c.data.startswith("admin_add_lesson:"))
async def admin_add_lesson(query: types.CallbackQuery):
    """Добавление урока."""
    course_id = query.data.split(":")[1]
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

@dp.callback_query(lambda c: c.data.startswith("admin_edit_tags:"))
async def admin_edit_tags(query: types.CallbackQuery):
    """Редактирование тегов урока."""
    course_id, lesson_num = query.data.split(":")[1], query.data.split(":")[2]
    # Display current tags, and then ask for new tags
    await query.message.edit_text(
        f"Editing tags for Lesson {lesson_num} of course {course_id}."
        f"\nSend the new tags as a list, separated by commas.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Cancel", callback_data=f"admin_edit_lesson:{course_id}:{lesson_num}")]
        ])
    )

    # Register handler to receive new tags



@dp.callback_query(lambda c: c.data.startswith("admin_delete_lesson:"))
async def admin_delete_lesson(query: types.CallbackQuery):
    """Удаление урока."""
    course_id, lesson_num = query.data.split(":")[1], query.data.split(":")[2]
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



# Команды для взаимодействия с пользователем - в конце, аминь.
#=======================================================================================================================

# Регистрация нового пользователя или приветствие существующего
@dp.message(CommandStart())
@db_exception_handler
async def cmd_start(message: types.Message):
    """Обработчик команды /start."""
    user_id = message.from_user.id
    first_name = message.from_user.first_name

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Check if the user exists
            cursor = await conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            user_exists = await cursor.fetchone()

            if not user_exists:
                # Add new user
                await conn.execute(
                    "INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                    (user_id, message.from_user.username, first_name, message.from_user.last_name)
                )
                await conn.commit()
                logger.info(f"New user added: {user_id}")

            # Get active course
            cursor = await conn.execute("""
                SELECT c.title, uc.course_id, uc.version_id, uc.current_lesson
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ? AND uc.status = 'active'
            """, (user_id,))
            active_course = await cursor.fetchone()

            # Generate keyboard
            keyboard = await get_main_menu_inline_keyboard(user_id=user_id)

            if active_course:
                # Show course status
                course_data = (active_course[1], active_course[0], active_course[2], active_course[3])  # course_id, title, version_id, current_lesson
                welcome_message = f"С возвращением, {first_name}!\n\n" \
                                  f"🎓 Курс: {course_data[1]}\n" \
                                  f"🔑 Тариф: {get_tariff_name(course_data[2])}\n" \
                                  f"📚 Текущий урок: {course_data[3]}\n"
                logger.info(f"333 Active course found for user {user_id}: {course_data=}")
                if keyboard:
                    await message.answer(welcome_message, reply_markup=keyboard)
                else:
                    await message.answer("Произошла ошибка при создании меню.")
            else:
                # User has no active courses
                courses = await get_courses_list()
                if courses:
                    courses_text = "\n".join([f"- {title} ({course_id})" for course_id, title in courses])
                    welcome_message = f"{'Добро пожаловать' if not user_exists else 'С возвращением'}, {first_name}!\n\n" \
                                      "Доступные курсы:\n" \
                                      f"{courses_text}\n\n" \
                                      "Введите кодовое слово для активации курса:"
                    await message.answer(welcome_message)
                else:
                    await message.answer("К сожалению, сейчас нет доступных курсов.")

    except Exception as e:
        logger.error(f"Error in cmd_start: {e}")
        await message.answer("Произошла ошибка при обработке команды. Пожалуйста, попробуйте позже.")

def get_tariff_name(version_id: str) -> str:
    """Возвращает человекочитаемое название тарифа."""
    TARIFF_NAMES = {
        "v1": "Соло",
        "v2": "Группа",
        "v3": "VIP"
    }
    return TARIFF_NAMES.get(version_id, f"Тариф {version_id}")

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


# help
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


# Создает тикет в службу поддержки # Пересылает сообщение администраторам
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", 0))  # ID админского чата

@dp.callback_query(F.data == "menu_support")
async def cmd_support_callback(query: types.CallbackQuery):
    """Обработчик для кнопки 'Поддержка'."""
    global user_support_state
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    message_id = query.message.message_id

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
    if ADMIN_CHAT_ID:
        await bot.forward_message(chat_id=ADMIN_CHAT_ID, from_chat_id=chat_id, message_id=query.message.message_id)

        # Сообщение админу
        await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
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
    logger.info(f"cmd_activate User {user_id} initiated activation process.")

    await message.answer(escape_md(
        "🔑 *Активация курса*\n\n"
        "Введите кодовое слово для активации курса.\n"
        "Для отмены введите /cancel."),
        parse_mode="MarkdownV2"
    )


@dp.message(Command("mycourses"))
@db_exception_handler  # todo потестить
async def old_cmd_mycourses(message: Message):
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



@dp.callback_query(F.data == "menu_mycourses") #08-04 Предоставляет кнопки для продолжения или повторного просмотра
@db_exception_handler  # Показывает список активных и завершенных курсов # Разделяет курсы на активные и завершенные
async def cmd_mycourses_callback(query: types.CallbackQuery):
    """Показывает список активных и завершенных курсов."""
    user_id = query.from_user.id
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


@dp.callback_query(lambda c: c.data.startswith("menu_current_lesson"))
async def show_lesson_content(query: types.CallbackQuery):
    """Отображает текущий урок пользователя с задержкой."""
    course_id = query.data.split(":")[1]
    user_id = query.from_user.id
    logger.info(f"show_lesson_content: {course_id=} {user_id=}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем текущий урок пользователя
            cursor = await conn.execute("""
                SELECT current_lesson FROM user_courses WHERE user_id = ? AND course_id = ?
            """, (user_id, course_id))
            current_lesson_record = await cursor.fetchone()

            if not current_lesson_record:
                await query.answer("У вас нет активного урока.", show_alert=True)
                return

            lesson_num = current_lesson_record[0]
            logger.info(f"lesson_num={lesson_num}")

            # Получаем group_id (чат ID) для этого курса
            cursor = await conn.execute("""
                SELECT group_id FROM courses 
                WHERE course_id = ?
            """, (course_id,))
            group_record = await cursor.fetchone()
            logger.info(f"224 show_lesson_content {group_record=}")

            if not group_record:
                await query.answer("Информация о курсе не найдена.", show_alert=True)
                return

            group_id = group_record[0]
            logger.info(f"group_id для курса {course_id}: {group_id}")

            # Получаем содержимое текущего урока из group_messages
            cursor = await conn.execute("""
                SELECT text FROM group_messages
                WHERE group_id = ? AND lesson_num = ? AND text IS NOT NULL
                ORDER BY id ASC
            """, (group_id, lesson_num))
            lesson_content = await cursor.fetchall()

            logger.info(f"225lesson_content={lesson_content}")

            if not lesson_content:
                await query.answer("Содержимое урока не найдено.", show_alert=True)
                return

            # Получаем задержку из settings.json
            global settings
            message_interval = settings.get("message_interval", 8)  # Default to 8 seconds

            # Отправляем сообщения по одному с задержкой
            await query.message.edit_text("Начинаю отправку урока...")
            for row in lesson_content:
                text = row[0]
                if text and not text.startswith("*"):  # Пропускаем служебные метки
                    await bot.send_message(chat_id=query.message.chat.id, text=text)
                    await asyncio.sleep(message_interval)

            await bot.send_message(chat_id=query.message.chat.id, text="Урок завершен.")

    except Exception as e:
        logger.error(f"Ошибка при получении текущего урока: {e}")
        await query.answer("Произошла ошибка при получении текущего урока.", show_alert=True)


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
    await callback_query.answer("Отправка урока...")
    for msg_id in range(start_id, end_id + 1):
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=group_id,
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
@dp.message(lambda message: message.text.lower() in settings["activation_codes"])
@db_exception_handler
async def activate_course(message: types.Message):
    """Активирует курс для пользователя и показывает меню."""
    code = message.text.lower()
    user_id = message.from_user.id
    global settings

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверяем код активации
            cursor = await conn.execute("""
                SELECT course_id, course_type FROM course_activation_codes WHERE code_word = ?
            """, (code,))
            course_details = await cursor.fetchone()

            if not course_details:
                await message.answer("Неверный код активации.")
                return

            course_id, course_type = course_details

            # Активируем курс для пользователя
            await conn.execute("""
                INSERT OR IGNORE INTO user_courses (user_id, course_id, version_id, status)
                VALUES (?, ?, ?, 'active')
            """, (user_id, course_id, course_type))

            await conn.commit()

        # Отображаем меню
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Текущий урок", callback_data=f"current_lesson:{course_id}")],
            [InlineKeyboardButton(text="Все уроки", callback_data=f"all_lessons:{course_id}")],
        ])
        await message.answer("Курс успешно активирован! Выберите действие:", reply_markup=keyboard)

    except Exception as e:
        logger.error(f"Ошибка при активации курса: {e}")
        await message.answer("Произошла ошибка при активации курса.")



#======================Конец обработчиков слов и хэндлеров кнопок=========================================

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
            """, (code,))
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
                        await log_user_activity(user_id, "COURSE_ACTIVATION",
                                                f"Курс {course_id} активирован с кодом {message.text.strip()}")
                        await message.answer("Курс успешно активирован!\nИспользуйте кнопки ниже для навигации.",
                                             reply_markup=get_main_menu_inline_keyboard())
            except Exception as e:
                logger.error(f"Ошибка при активации курса: {e}")
                await message.answer("Произошла ошибка при активации курса.")
        else:
            await message.answer("Неверное 333 кодовое слово. Попробуйте еще раз или свяжитесь с поддержкой.")
    except Exception as e:
        logger.error(f"Общая ошибка в process_message: {e}")
        await message.answer("Произошла общая ошибка. Пожалуйста, попробуйте позже.")

#=======================Конец обработчиков текстовых сообщений=========================================

# Запуск бота
async def main():
    global settings, COURSE_GROUPS
    # Инициализация базы данных
    await init_db()
    settings = load_settings()  # Загрузка настроек при запуске
    logger.info(f"444 load_settings {len(settings['groups'])=}")

    COURSE_GROUPS = list(map(int, settings.get("groups", {}).keys()))  # load to value too
    logger.info(f"555  {COURSE_GROUPS=}")
    await import_settings_to_db()
    await asyncio.sleep(0.2) # Небольшая задержка перед отправкой сообщения
    await send_startup_message(bot, ADMIN_GROUP_ID)  # Отправка сообщения в группу администраторов
    # asyncio.create_task(check_and_schedule_lessons())

    # Запуск бота
    logger.info(f"Бот успешно запущен.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())


# Осознание обработчиков:
# @dp.message(Command(...)): Обработчики команд (начинаются с /).
# @dp.message(F.text): Обработчики текстовых сообщений (ловят любой текст).
# @dp.callback_query(lambda c: ...): Обработчики нажатий на кнопки (inline keyboard).
# @dp.message(lambda message: message.text.lower() in settings["activation_codes"]): Обработчик для активации курса по коду.
