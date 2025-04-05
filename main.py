import asyncio,  logging, json, random, string, os, re
import functools
import inspect
from logging.handlers import RotatingFileHandler
#from aiogram.utils.text_decorations import escape_md нет в природе. сами напишем
#from aiogram.utils.markdown import quote  # Для MarkdownV2 - todo попробовать
# Или
#from aiogram.utils.text_decorations import html  # Для HTML


from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.filters.callback_data import CallbackData

from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton, Message, CallbackQuery
)
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
        format='%(asctime)s %(name)s %(lineno)d  %(message)s  %(levelname)s',
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
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла ошибка в работе Telegram API.")
                    break
            return None
        except Exception as e:
            logger.error(f"Unexpected error in ... {func.__name__} {e}")
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла неизвестная ошибка.")
                    break
            return None
    return wrapper

def load_settings():
    """Load settings from file"""
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"channels": {}, "activation_codes": {}}

def save_settings(settings):
    """Save settings to file"""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=4)
        logger.info("Settings saved successfully.")
    except Exception as e:
        logger.error(f"Error saving settings: {e}")

# Загрузка настроек при старте
settings = load_settings()
logger.info("Settings loaded successfully. {settings=}")

# Database initialization
@db_exception_handler
async def init_db():
    """Initialize the database with required tables"""
    logger.info(f"init_db ")
    async with aiosqlite.connect(DB_FILE) as conn:
        # Users table
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            is_active INTEGER DEFAULT 1,
            is_banned INTEGER DEFAULT 0,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # User profiles with additional info
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            alias TEXT,
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
            title TEXT NOT NULL,
            description TEXT,
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
            title TEXT NOT NULL,
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
            snippet TEXT, -- Сниппет урока
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (course_id, lesson_num)
        )
        ''')
        
        # Promo codes
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            course_id TEXT,
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
            reason TEXT,
            transaction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        ''')
        
        # User activity log
        await conn.execute('''
        CREATE TABLE IF NOT EXISTS user_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
        ''')
        
        await conn.commit()
        logger.info("Database initialized successfully")
    #await init_test_data()


# функция для инициализации тестовых данных в БД
@db_exception_handler
async def init_test_data():
    """Initialize test data if database is empty"""
    logger.info(f"init_test_data ")
    async with aiosqlite.connect(DB_FILE) as conn:
        # Check if courses table is empty
        cursor = await conn.execute("SELECT COUNT(*) FROM courses")
        if (await cursor.fetchone())[0] == 0:
            # Insert test courses
            await conn.execute("""
                INSERT INTO courses (course_id, title, is_active)
                VALUES ('femininity', 'Женственность', 1)
            """)

            version_ids = ["v1", "v2", "v3"]
            codes = ["роза", "фиалка", "лепесток"]
            # Insert test versions
            for i, code in enumerate(codes):
                version_id = version_ids[i]
                await conn.execute("""
                    INSERT INTO course_versions (course_id, version_id, title, activation_code)
                    VALUES (?, ?, ?, ?)
                """, ('femininity', version_id, f"Тариф {version_id}", code))

            # Insert test channels
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
            logger.info("Test data initialized successfully.")

# логирование действий пользователя
@db_exception_handler
async def log_user_activity(user_id, action, details=""):
    logger.info(f"log_user_activity {user_id=} {action=} {details=}")
    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute(
            "INSERT INTO user_activity (user_id, action, details) VALUES (?, ?, ?)",
            (user_id, action, details)
        )
        await conn.commit()
    logger.info(f"Logged activity for user {user_id}: {action} - {details}")


def escape_md(text):
    """    Экранирует специальные символы для MarkdownV2.  """
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([{}])'.format(re.escape(escape_chars)), r'\\\1', text)


# функция для проверки email
def is_valid_email(email):
    """Validate email format"""
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(email_pattern, email)


# функция для проверки номера телефона
def is_valid_phone(phone):
    """Validate phone number format"""
    phone_pattern = r"^\+?[0-9]{10,15}$"
    return re.match(phone_pattern, phone)


def generate_random_password(length=5):
    """Generate a random password"""
    characters = string.ascii_letters + string.digits + string.punctuation
    return ''.join(random.choice(characters) for i in range(length))


# функция для разрешения ID пользователя по алиасу или ID
@db_exception_handler
async def resolve_user_id(user_identifier):
    """Resolve user_id from alias or ID"""
    async with aiosqlite.connect(DB_FILE) as conn:
        if user_identifier.isdigit():
            cursor = await conn.execute("SELECT user_id FROM users WHERE user_id = ?", (int(user_identifier),))
        else:
            cursor = await conn.execute("SELECT user_id FROM user_profiles WHERE alias = ?", (user_identifier,))
        result = await cursor.fetchone()
        return result[0] if result else None

# Отправка урока пользователю
@db_exception_handler
async def send_lesson_to_user(user_id, course_id, lesson_num):
    """Send lesson content to a user from the corresponding channel"""
    logger.info(f"send_lesson_to_user {user_id=} {course_id=} {lesson_num=}")
    async with aiosqlite.connect(DB_FILE) as conn:
        # Get channel_id and message range for the lesson
        cursor = await conn.execute("""
            SELECT c.channel_id, lcm.start_message_id, lcm.end_message_id
            FROM courses c
            JOIN lesson_content_map lcm ON c.course_id = lcm.course_id
            WHERE c.course_id = ? AND lcm.lesson_num = ?
        """, (course_id, lesson_num))
        lesson_data = await cursor.fetchone()

        if not lesson_data:
            await bot.send_message(user_id, "Урок не найден. Пожалуйста, обратитесь к администратору.")
            return False

        channel_id, start_id, end_id = lesson_data



        # Send all messages in the range
        for msg_id in range(start_id, end_id + 1):
            try:
                # Using copy_message to maintain privacy and allow customization
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=channel_id,
                    message_id=msg_id
                )
                await asyncio.sleep(0.5)  # Prevent flooding
            except Exception as e:
                logger.error(f"Error sending message {msg_id} to user {user_id}: {e}")

        # Update user progress
        await conn.execute("""
            UPDATE user_courses 
            SET current_lesson = ?, last_lesson_date = CURRENT_TIMESTAMP 
            WHERE user_id = ? AND course_id = ?
        """, (lesson_num + 1, user_id, course_id))
        await conn.commit()

        # Send completion message with buttons
        await finalize_lesson_delivery(user_id, course_id, lesson_num)

        return True

# функция для завершения доставки урока
async def finalize_lesson_delivery(user_id, course_id, lesson_num):
    """Send completion message after delivering a lesson"""
    logger.info(f"finalize_lesson_delivery {user_id=} {course_id=} {lesson_num=}")
    # Create completion keyboard
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="Урок изучен ✅", callback_data=f"lesson_complete:{course_id}:{lesson_num}"),
        InlineKeyboardButton(text="Отправить домашнее задание 📝", callback_data=f"submit_homework:{course_id}:{lesson_num}")
    )
    return keyboard
    
    # Send completion message
    await bot.send_message(
        user_id,
        "Вы получили все материалы урока. Пожалуйста, подтвердите изучение или отправьте домашнее задание.",
        reply_markup=keyboard
    )
    
    # Update user state
    await set_user_state(user_id, "LESSON_RECEIVED")

# планировщик уроков. Проверка и отправка уведомлений о новых уроках
@db_exception_handler
async def check_and_schedule_lessons():
    """Background task to check and send scheduled lessons"""
    logger.info("check_and_schedule_lessons")
    while True:
        try:
            async with aiosqlite.connect(DB_FILE) as conn:
                # Find users with lessons due
                cursor = await conn.execute(
                    """
                    SELECT uc.user_id, uc.course_id, uc.current_lesson, c.total_lessons
                    FROM user_courses uc
                    JOIN courses c ON uc.course_id = c.course_id
                    WHERE uc.next_lesson_date <= CURRENT_TIMESTAMP
                    AND uc.is_completed = 0
                    AND c.is_active = 1
                    """
                )
                due_lessons = await cursor.fetchall()

                for user_id, course_id, current_lesson, total_lessons in due_lessons:
                    # Send notification
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                        InlineKeyboardButton(
                            text="Начать урок",
                            callback_data=f"start_lesson:{course_id}:{current_lesson}"
                        )
                    ]])

                    try:
                        await bot.send_message(
                            user_id,
                            f"🔔 Доступен новый урок курса! Нажмите кнопку ниже, чтобы начать.",
                            reply_markup=keyboard
                        )

                        # Update next lesson date to prevent repeated notifications
                        await conn.execute(
                            "UPDATE user_courses SET next_lesson_date = NULL WHERE user_id = ? AND course_id = ?",
                            (user_id, course_id)
                        )

                        # Log activity
                        await log_user_activity(user_id, "LESSON_AVAILABLE",
                                                f"Course: {course_id}, Lesson: {current_lesson}")

                    except Exception as e:
                        logger.error(f"Failed to send message to user {user_id}: {e}")

                await conn.commit()

        except Exception as e:
            logger.error(f"Error in lesson scheduler: {e}")

        # Check every minute
        await asyncio.sleep(60)


# функция для отправки сообщения ИИ
@db_exception_handler
async def forward_to_ai_agent(user_id, user_message, course_id=None):
    """Forward user message to AI agent in admin chat for assistance"""
    logger.info("forward_to_ai_agent")
    async with aiosqlite.connect(DB_FILE) as conn:
        # Get user info
        cursor = await conn.execute(
            "SELECT username, first_name, last_name, alias FROM user_profiles WHERE user_id = ?",
            (user_id,)
        )
        user_info = await cursor.fetchone()
        
        if not user_info:
            username, first_name, last_name, alias = "Unknown", "Unknown", "", "Unknown"
        else:
            username, first_name, last_name, alias = user_info
        
        # Format user display name
        display_name = alias or (first_name + (" " + last_name if last_name else ""))
        
        # Get course info if provided
        course_info = ""
        if course_id:
            cursor = await conn.execute(
                """
                SELECT c.title, uc.current_lesson 
                FROM courses c
                JOIN user_courses uc ON c.course_id = uc.course_id
                WHERE uc.user_id = ? AND uc.course_id = ?
                """,
                (user_id, course_id)
            )
            course_data = await cursor.fetchone()
            if course_data:
                course_title, current_lesson = course_data
                course_info = f"\nКурс: {course_title} (ID: {course_id})\nТекущий урок: {current_lesson}"
    
    # Format message for AI agent
    ai_message = (
        f"📩 *Сообщение от пользователя*\n"
        f"👤 Пользователь: {display_name} (ID: `{user_id}`)\n"
        f"Username: @{username if username else 'отсутствует'}{course_info}\n\n"
        f"💬 Сообщение:\n{user_message}\n\n"
        f"Для ответа используйте команду:\n"
        f"`/adm_message_user {user_id} Ваш ответ`"
    )
    
    # Send to admin group
    await bot.send_message(
        ADMIN_GROUP_ID,
        ai_message,
        parse_mode="MarkdownV2"
    )

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


# Регистрация нового пользователя или приветствие существующего
@dp.message(CommandStart())
@db_exception_handler
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    logger.info(f"Handling /start command for user {user_id}")

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        user_exists = await cursor.fetchone()

    logger.info(f"User {user_id} exists in database: {bool(user_exists)}")

    if not user_exists:
        # Регистрация нового пользователя
        try:
            await conn.execute("""
                INSERT INTO users (user_id, first_name, last_name, username, registered_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                user_id,
                message.from_user.first_name,
                message.from_user.last_name or "",
                message.from_user.username or ""
            ))
            await conn.commit()

            admin_notification = (
                f"Новый пользователь зарегистрирован!\n"
                f"ID: {user_id}\n"
                f"Имя: {message.from_user.full_name}\n"
                f"Username: @{message.from_user.username or 'отсутствует'}"
            )
            await bot.send_message(ADMIN_GROUP_ID, admin_notification)

            await log_user_activity(user_id, "REGISTRATION", "New user registered")

            # Получаем список всех курсов
            async with aiosqlite.connect(DB_FILE) as conn:
                cursor = await conn.execute("SELECT course_id, title FROM courses")
                courses = await cursor.fetchall()

            if courses:
                courses_text = "\n".join([f"- {title} ({course_id})" for course_id, title in courses])
                await message.answer(
                    f"👋 Добро пожаловать, {message.from_user.first_name}!\n"
                    "Я бот для доступа к обучающим курсам.\n\n"
                    "Доступные курсы:\n"
                    f"{courses_text}\n\n"
                    "Пожалуйста, введите кодовое слово для активации курса:"
                )
            else:
                await message.answer("К сожалению, сейчас нет доступных курсов.")
        except Exception as e:
            logger.error(f"Error during registration: {e}")
            await message.answer("Произошла ошибка при регистрации.")
    else:
        # Приветствие существующего пользователя
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT uc.course_id, c.title, uc.version_id, uc.current_lesson
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ? AND uc.status = 'active'
                LIMIT 1
            """, (user_id,))
            active_course = await cursor.fetchone()

        keyboard = get_main_menu_inline_keyboard()

        if active_course:
            course_id, course_title, version_id, current_lesson = active_course
            await message.answer(
                f"С возвращением, {message.from_user.first_name}!\n"
                f"Ваш текущий активный курс: {course_title} ({course_id})\n"
                f"Тариф: {version_id}\n"
                f"Текущий урок: {current_lesson}\n",
                reply_markup=keyboard
            )
        else:
            # Если нет активного курса
            async with aiosqlite.connect(DB_FILE) as conn:
                cursor = await conn.execute("SELECT course_id, title FROM courses")
                courses = await cursor.fetchall()

            if courses:
                courses_text = "\n".join([f"- {title} ({course_id})" for course_id, title in courses])
                await message.answer(
                    f"С возвращением, {message.from_user.first_name}!\n"
                    "У вас нет активных курсов.\n\n"
                    "Доступные курсы:\n"
                    f"{courses_text}\n\n"
                    "Пожалуйста, введите кодовое слово для активации курса:"
                )
            else:
                await message.answer("К сожалению, сейчас нет доступных курсов.")


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
    """Handler for the /support command to initiate support requests"""
    user_id = message.from_user.id
    logger.info(f"cmd_support {user_id=}")

    await message.answer(escape_md(
        "📞 *Поддержка*\n\n"
        "Опишите вашу проблему или задайте вопрос. Мы постараемся ответить как можно скорее.\n"
        "Для отмены введите /cancel."),
        parse_mode="MarkdownV2"
    )


# Support message handler
@dp.message(F.text)
async def process_support_request(message: Message):
    """Process messages from users for support requests"""
    user_id = message.from_user.id
    logger.info(f"process_support_request {user_id=}")

    # Check for cancel command
    if message.text == '/cancel':
        await message.answer("Запрос в поддержку отменен.")
        return

    # Get user's active course if any
    active_course_id = None
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

    # Forward the message to admin group via AI agent
    await forward_to_ai_agent(user_id, message.text, active_course_id)

    # Log the support request
    await log_user_activity(user_id, "SUPPORT_REQUEST", f"Message: {message.text[:100]}...")

    # Confirm receipt
    await message.answer(escape_md(
        "✅ Ваше сообщение отправлено в поддержку. Мы ответим вам в ближайшее время.\n"
        "Вы можете продолжать пользоваться ботом."))


# Admin command to reply to user
@dp.message(Command("adm_message_user"), F.chat.id == ADMIN_GROUP_ID)
async def adm_message_user(message: Message):
    """Send a message to a user from admin"""
    command_parts = message.text.split(maxsplit=2)
    logger.info(f"adm_message_user {command_parts=}  ")
    if len(command_parts) < 3:
        await message.answer(escape_md("Использование: /adm_message_user <user_id|alias> <текст>"))
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
        
        await message.answer(escape_md(f"✅ Сообщение отправлено пользователю {user_id}."))
    except Exception as e:
        await message.answer(escape_md(f"❌ Ошибка при отправке сообщения: {str(e)}"))

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

# Activation code handler
@dp.message(UserStates.ACTIVATION)
@db_exception_handler
async def process_activation_code(message: Message, state: FSMContext):
    user_id = message.from_user.id
    activation_code = message.text.strip()
    logger.info(f"333 Processing activation code {activation_code} for user {user_id}")

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT cv.course_id, cv.version_id
            FROM course_versions cv
            WHERE cv.activation_code = ?
        """, (activation_code,))
        course_data = await cursor.fetchone()

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
                    await message.answer("Этот курс уже активирован.")
                else:
                    await conn.execute("""
                        INSERT INTO user_courses (user_id, course_id, version_id, status, activation_date)
                        VALUES (?, ?, ?, 'active', CURRENT_TIMESTAMP)
                    """, (user_id, course_id, version_id))

                    await conn.commit()
                    await log_user_activity(user_id, "COURSE_ACTIVATION",
                                            f"Course {course_id} activated with code {activation_code}")
                    await message.answer("Курс успешно активирован!\nИспользуйте кнопки ниже для навигации.",
                                         reply_markup=get_main_menu_inline_keyboard())
        except Exception as e:
            logger.error(f"Error activating course: {e}")
            await message.answer("Произошла ошибка при активации курса.")
    else:
        logger.error(f"Неверное кодовое слово. Попробуйте еще ")
        await message.answer("Неверное кодовое слово. Попробуйте еще раз или свяжитесь с поддержкой.")


# Показывает список активных и завершенных курсов # Разделяет курсы на активные и завершенные
@dp.message(Command("mycourses")) # Предоставляет кнопки для продолжения или повторного просмотра
@db_exception_handler
async def cmd_mycourses(message: Message):
    """Handler for the /mycourses command to show user's courses"""
    user_id = message.from_user.id
    logger.info(f"5552 cmd_mycourses ")
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
    
    # Create message with course list
    courses_text = "📚 *Ваши курсы:*\n\n"
    
    for course_id, title, current_lesson, total_lessons, is_completed in courses:
        status = "✅ Завершен" if is_completed else f"📝 Урок {current_lesson}/{total_lessons}"
        courses_text += f"*{title}*\n{status}\n\n"
    
    # Add keyboard with course actions
    keyboard = InlineKeyboardMarkup(row_width=1)
    
    for course_id, title, current_lesson, total_lessons, is_completed in courses:
        if not is_completed:
            keyboard.add(InlineKeyboardButton(
                text=f"Продолжить '{title}'", 
                callback_data=f"start_lesson:{course_id}:{current_lesson}"
            ))
    
    await message.answer(escape_md(
        courses_text),
        reply_markup = keyboard,
        parse_mode="MarkdownV2"
    )

# Админ-команда для одобрения курса
@dp.message(Command("adm_approve_course"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
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


# Lesson command
@dp.message(Command("lesson"))
@db_exception_handler
async def cmd_lesson(message: Message):
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
    success = await send_lesson_to_user(user_id, course_id, current_lesson)

    if success:
        # Log lesson delivery
        await log_user_activity(
            user_id,
            "LESSON_RECEIVED",
            f"Course: {course_id}, Lesson: {current_lesson}"
        )
    else:
        await message.answer("Произошла ошибка при отправке урока. Пожалуйста, попробуйте позже.")

# Обработчик для команды просмотра прогресса по всем курсам
@dp.message(Command("progress"))
@db_exception_handler
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

# функция для отправки урока пользователю
@dp.callback_query(lambda c: c.data.startswith("start_lesson:"))
@db_exception_handler
async def start_lesson_callback(callback_query: CallbackQuery):
    """Handle start lesson button callback"""
    try:
        user_id = callback_query.from_user.id
        callback_data = callback_query.data
        _, course_id, lesson_num = callback_data.split(":")

        logger.info(f"start_lesson_callback {user_id=}, course_id={course_id}, lesson_num={lesson_num}")

        # Get course info
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute(
                "SELECT title FROM courses WHERE course_id = ?",
                (course_id,)
            )
            course_data = await cursor.fetchone()

            if not course_data:
                await callback_query.answer("Курс не найден.")
                return

            course_title = course_data[0]

        # Acknowledge the callback
        await callback_query.answer()

        # Edit message to show loading
        await callback_query.message.edit_text(f"Отправляю урок {lesson_num} курса '{course_title}'...")

        # Send lesson
        success = await send_lesson_to_user(user_id, course_id, lesson_num)

        if success:
            # Log lesson delivery
            await log_user_activity(
                user_id,
                "LESSON_RECEIVED",
                f"Course: {course_id}, Lesson: {lesson_num}"
            )
        else:
            await bot.send_message(user_id, "Произошла ошибка при отправке урока. Пожалуйста, попробуйте позже.")
    except Exception as e:
        logger.error(f"Exception in start_lesson_callback: {e}")
        await callback_query.answer("Произошла ошибка при обработке запроса.")
