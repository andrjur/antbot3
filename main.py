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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
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
        format='%(asctime)s[18:] %(name)s - %(lineno)d - %(message)s  %(levelname)s',
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
dp = Dispatcher(storage=MemoryStorage())

# User states
class UserStates(StatesGroup):
    REGISTRATION = State()
    MAIN_MENU = State()
    ACTIVATION = State()
    RECEIVING_LESSON = State()
    LESSON_RECEIVED = State()
    LESSON_COMPLETED = State()
    HOMEWORK_PENDING = State()
    WAITING_APPROVAL = State()
    SUPPORT = State()
    GET_USERNAME = State()
    GET_PASSWORD = State()

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
            state TEXT DEFAULT 'MAIN_MENU',
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
    await load

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

            # Insert test versions
            await conn.execute("""
                INSERT INTO course_versions (course_id, version_id, title, activation_code)
                VALUES 
                ('femininity', 'v1', 'Базовый тариф', 'роза'),
                ('femininity', 'v2', 'Стандартный тариф', 'фиалка'),
                ('femininity', 'v3', 'Премиум тариф', 'лепесток')
            """)

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

# функция для получения текущего состояния пользователя
@db_exception_handler
async def get_user_state(user_id):
    """Get the current state of a user"""
    logger.info(f"get_user_state {user_id=} ")
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            "SELECT state FROM user_states WHERE user_id = ?",
            (user_id,)
        )
        result = await cursor.fetchone()
        
        if result:
            return result[0]
        else:
            # Create default state if not exists
            await conn.execute(
                "INSERT INTO user_states (user_id, state) VALUES (?, ?)",
                (user_id, "MAIN_MENU")
            )
            await conn.commit()
            return "MAIN_MENU"


# функция для установки состояний пользователя
@db_exception_handler
async def set_user_state(user_id, state):
    """Set the state of a user"""
    async with aiosqlite.connect(DB_FILE) as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO user_states (user_id, state, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (user_id, state)
        )
        await conn.commit()
    logger.debug(f"Set user {user_id} state to {state}")

def escape_md(text):
    """    Экранирует специальные символы для MarkdownV2.  """
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(r'([{}])'.format(re.escape(escape_chars)), r'\\\1', text)


# функция для разрешения ID пользователя по алиасу или ID
@db_exception_handler
async def resolve_user_id(user_identifier):
    """Resolve user_id from alias or numeric ID"""
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

        # Update user state
        await set_user_state(user_id, "RECEIVING_LESSON")

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
                    # Check user state
                    user_state = await get_user_state(user_id)
                    
                    # Only send if user is in a state ready for a new lesson
                    if user_state in ["MAIN_MENU", "LESSON_COMPLETED"]:
                        # Send notification
                        keyboard = InlineKeyboardMarkup()
                        keyboard.add(InlineKeyboardButton(
                            text="Начать урок", 
                            callback_data=f"start_lesson:{course_id}:{current_lesson}"
                        ))
                        
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
                        await log_user_activity(user_id, "LESSON_AVAILABLE", f"Course: {course_id}, Lesson: {current_lesson}")
                
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

# функция для регистрации пользователя - имя пользователя
@dp.message(UserStates.GET_USERNAME)
@db_exception_handler
async def process_username(message: Message, state: FSMContext):
    username = message.text.strip()
    logger.info(f"process_username {username=}")
    # Проверка на уникальность имени пользователя
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT 1 FROM users WHERE username = ?", (username,))
        exists = await cursor.fetchone()

    if exists:
        await message.answer("Это имя пользователя уже занято. Пожалуйста, выберите другое:")
        return

    await state.update_data(username=username)
    await state.set_state(UserStates.GET_PASSWORD)
    await message.answer("Отлично! Теперь введите пароль:")

# функция для регистрации пользователя - пароль
@dp.message(UserStates.GET_PASSWORD)
@db_exception_handler
async def process_password(message: Message, state: FSMContext):
    password = message.text.strip()
    user_data = await state.get_data()
    username = user_data.get('username')
    user_id = message.from_user.id
    logger.info(f"process_password {user_data=}")
    async with aiosqlite.connect(DB_FILE) as conn:
        # Создаем запись в таблице пользователей
        await conn.execute(
            """
            INSERT INTO users (user_id, username, password_hash, first_name, last_name)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, username, message.from_user.first_name, message.from_user.last_name)
        )

        # Создаем профиль пользователя
        await conn.execute(
            """
            INSERT INTO user_profiles (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, username, message.from_user.first_name, message.from_user.last_name)
        )

        # Устанавливаем начальное состояние
        await conn.execute(
            "INSERT INTO user_states (user_id, state) VALUES (?, ?)",
            (user_id, "MAIN_MENU")
        )

        await conn.commit()

    await state.clear()
    await message.answer(escape_md(
        f"🎉 Регистрация успешно завершена!\n"
        f"Имя пользователя: {username}\n\n"
        f"Теперь вы можете использовать бота.\n"
        f"Используйте /help, чтобы увидеть список доступных команд."
    ))

    # Уведомляем администраторов о новом пользователе
    admin_notification = (
        f"🆕 Новый пользователь зарегистрирован!\n"
        f"ID: {user_id}\n"
        f"Имя: {message.from_user.full_name}\n"
        f"Username: @{username}"
    )
    await bot.send_message(ADMIN_GROUP_ID, admin_notification)

# Регистрация нового пользователя или приветствие существующего
@dp.message(CommandStart())
@db_exception_handler
async def cmd_start(message: Message):
    user_id = message.from_user.id
    logger.info(f"777 Handling /start command for user {user_id}")
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        user_exists = await cursor.fetchone()

    logger.info(f"13 User {user_id} exists in database: {bool(user_exists)}")
    if not user_exists:
        # Регистрация нового пользователя
        try:
            await conn.execute("""
                INSERT INTO users (user_id, first_name, last_name, username, registration_date)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                user_id,
                message.from_user.first_name,
                message.from_user.last_name or "",
                message.from_user.username or ""
            ))
            await conn.commit()

            # Уведомляем администраторов
            admin_notification = (
                f"34 Новый пользователь зарегистрирован!\n"
                f"ID: {user_id}\n"
                f"Имя: {message.from_user.full_name}\n"
                f"Username: @{message.from_user.username or 'отсутствует'}"
            )
            await bot.send_message(ADMIN_GROUP_ID, admin_notification)

            # Логируем регистрацию
            await log_user_activity(user_id, "REGISTRATION", "New user registered")

            # Приветствие нового пользователя
            keyboard=get_main_menu_inline_keyboard()
            if not keyboard.inline_keyboard:
                logger.error(f"ErrornlineKeyboardMarkup is empty")

            logger.error(f"REGISTRATION")
            await message.answer(escape_md(
                f"👋 Добро пожаловать, {message.from_user.first_name}!\n"
                "Я бот для доступа к обучающим курсам.\n"
                "Используйте кнопки ниже для навигации."),
                reply_markup=keyboard
            )
            logger.info(f"Welcome message sent to user {user_id}")
        except Exception as e:
            current_frame = inspect.currentframe()
            line_number = current_frame.f_lineno
            logger.error(f"Error creating inline keyboard at line {line_number}: {e}")
            await message.answer("Произошла ошибка при создании меню.")
    else:
        # Приветствие существующего пользователя
        logger.info(f"777132 else до")
        keybo=get_main_menu_inline_keyboard()
        logger.info(f"777133 else User {user_id} ")
        await message.answer(
            f"С возвращением, {message.from_user.first_name}! \n"
            "Используйте кнопки ниже для навигации.",
            reply_markup=keybo
        )


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
    # Set user state to SUPPORT
    await set_user_state(user_id, "SUPPORT")
    
    await message.answer(escape_md(
        "📞 *Поддержка*\n\n"
        "Опишите вашу проблему или задайте вопрос. Мы постараемся ответить как можно скорее.\n"
        "Для отмены введите /cancel."),
        parse_mode="MarkdownV2"
    )

# Support message handler
@dp.message(F.text)
async def process_support_request(message: Message):
    """Process messages from users in SUPPORT state"""
    user_id = message.from_user.id
    user_state = await get_user_state(user_id)
    logger.info(f"process_support_request {user_id=} {user_state=} . ")
    if user_state != "SUPPORT":
        return
    
    # Check for cancel command
    if message.text == '/cancel':
        await set_user_state(user_id, "MAIN_MENU")
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
    
    # Confirm receipt and reset state
    await message.answer(escape_md(
        "✅ Ваше сообщение отправлено в поддержку. Мы ответим вам в ближайшее время.\n"
        "Вы можете продолжать пользоваться ботом." ))
    
    # Reset user state to main menu
    await set_user_state(user_id, "MAIN_MENU")

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

    # Устанавливаем состояние ACTIVATION
    await set_user_state(user_id, "ACTIVATION")
    logger.info(f"User {user_id} state set to ACTIVATION.")


    await message.answer(escape_md(
        "🔑 *Активация курса*\n\n"
        "Введите кодовое слово для активации курса.\n"
        "Для отмены введите /cancel."),
        parse_mode="MarkdownV2"
    )

# Activation code handler
@dp.message(F.text)
@db_exception_handler
async def process_activation_code(message: Message):
    """Process activation codes from users in ACTIVATION state"""
    user_id = message.from_user.id
    activation_code = message.text.strip().lower()
    logger.info(f"5551 process_activation_code ")
    # Находим курс и версию по коду активации
    course_version = settings["activation_codes"].get(activation_code)
    if not course_version:
        await message.answer(escape_md("❌ Неверный код активации."))
        return

    course_id, version_id = course_version.split(":")

    # Проверяем, не активирован ли уже этот курс
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM user_courses WHERE user_id = ? AND course_id = ?",
            (user_id, course_id)
        )
        if await cursor.fetchone():
            await message.answer(escape_md(f"❌ Вы уже активировали курс '{course_id}'."))
            return

        # Активируем курс
        logger.info(f"5551 ща активируем всё тут ")
        await conn.execute("""
                INSERT INTO user_courses(user_id, course_id, version_id, current_lesson, activation_date)
                VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            """, (user_id, course_id, version_id))
        await conn.commit()

        # Логируем активацию
        await log_user_activity(user_id, "COURSE_ACTIVATION", f"Course: {course_id}, Version: {version_id}")

        # Уведомляем пользователя
        await message.answer(escape_md(
            f"✅ Курс успешно активирован!\n"
            f"📚 *{course_id}*\n"
            f"📋 Версия: {version_id}\n"
            "Используйте команду /lesson, чтобы начать обучение."),
            parse_mode="MarkdownV2"
        )


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
    logger.info(f"5554 cmd_lesson {user_id} ")
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
    
    # Check if user is in a state where they can receive a lesson
    user_state = await get_user_state(user_id)
    if user_state not in ["MAIN_MENU", "LESSON_COMPLETED"]:
        await message.answer(
            "Вы не можете получить урок в данный момент. Пожалуйста, завершите текущее действие."
        )
        return
    
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
async def start_lesson_callback(callback_query: CallbackQuery, course_id, lesson_num):
    """Handle start lesson button callback"""
    user_id = callback_query.from_user.id
    logger.info(f"5556 start_lesson_callback {user_id} ")
    # Check if user is in a state where they can receive a lesson
    user_state = await get_user_state(user_id)
    if user_state not in ["MAIN_MENU", "LESSON_COMPLETED"]:
        await callback_query.answer("Вы не можете получить урок в данный момент. Пожалуйста, завершите текущее действие.")
        return
    
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

# # Обрабатывает нажатие "Урок изучен" Обработчик для колбэков от кнопок Проверяет необходимость домашнего задания
@dp.callback_query(lambda c: c.data.startswith("lesson_complete:"))
@db_exception_handler
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
                    # Update state to waiting for homework
                    await set_user_state(user_id, "HOMEWORK_PENDING")
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
            await set_user_state(user_id, "MAIN_MENU")
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
            await set_user_state(user_id, "LESSON_COMPLETED")
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

            # Обновляем состояние пользователя
            await set_user_state(user_id, "LESSON_COMPLETED")

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

            # Обновляем состояние пользователя
            await set_user_state(user_id, "HOMEWORK_PENDING")

        await conn.commit()

        # Перерисовываем меню администратора
        await callback_query.message.edit_text(
            "Действие выполнено успешно.",
            reply_markup=InlineKeyboardMarkup().add(
                InlineKeyboardButton(text="🔙 Назад", callback_data="admin_panel")
            )
        )


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

# Показывает список завершенных курсов # Реализует пагинацию уроков
@dp.message(Command("completed_courses"))
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

# обработка отправки ДЗ
@dp.callback_query(lambda c: c.data.startswith("submit_homework:"))
@db_exception_handler
async def submit_homework_callback(callback_query: CallbackQuery, course_id, lesson_num):
    """Handle submit homework button callback"""
    user_id = callback_query.from_user.id
    logger.info(f"submit_homework_callback {user_id=} ")
    # Устанавливаем состояние "ожидание домашнего задания"
    await set_user_state(user_id, "HOMEWORK_PENDING")

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
            await set_user_state(user_id, "MAIN_MENU")
            return
        
        context = json.loads(context_data[0])
        course_id = context.get("course_id")
        lesson_num = context.get("lesson_num")
        
        if not course_id or not lesson_num:
            await message.answer("Произошла ошибка. Пожалуйста, начните отправку домашнего задания заново.")
            await set_user_state(user_id, "MAIN_MENU")
            return
        
        # Get course info
        cursor = await conn.execute(
            "SELECT title FROM courses WHERE course_id = ?",
            (course_id,)
        )
        course_data = await cursor.fetchone()
        
        if not course_data:
            await message.answer("Курс не найден.")
            await set_user_state(user_id, "MAIN_MENU")
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
        
        # Update user state
        await set_user_state(user_id, "WAITING_APPROVAL")
        
    except Exception as e:
        logger.error(f"Error processing homework: {e}")
        await message.answer("Произошла ошибка при отправке домашнего задания. Пожалуйста, попробуйте позже.")
        await set_user_state(user_id, "MAIN_MENU")


@dp.message(Command("set_code"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def set_activation_code(message: Message):
    try:
        _, code_word, course_id, course_type, price_rub = message.text.split()
        price_rub = int(price_rub)
        logger.info(f"set_activation_code {price_rub=} {code_word=}")
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO course_activation_codes 
                (code_word, course_id, course_type, price_rub)
                VALUES (?, ?, ?, ?)
                """,
                (code_word.lower(), course_id, course_type, price_rub)
            )
            await conn.commit()

        await message.answer(f"Кодовое слово '{code_word}' установлено для курса {course_id}.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}")

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
        channel_id = next((k for k, v in settings['channels'].items() if v == course_id), None)

        if not channel_id:
            await callback.answer("Ошибка конфигурации канала!")
            return

        # Получаем первые 7 сообщений для текущего урока
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT start_message_id, end_message_id 
                FROM lesson_content_map 
                WHERE course_id = ? AND lesson_num = ?
            """, (course_id, current_lesson))
            lesson_data = await cursor.fetchone()

        if not lesson_data:
            await callback.answer("Материалы урока не найдены!")
            return

        start_id, end_id = lesson_data
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

# В таблице lesson_content_map должны быть записи вида:
# course_id | lesson_num | start_message_id | end_message_id
# femininity | 1 | 123 | 130

# Запуск бота
async def main():
    # Инициализация базы данных
    await init_db()

    # Запуск фоновых задач
    asyncio.create_task(check_and_schedule_lessons())
    
    # Запуск бота
    logger.info("Бот успешно запущен.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())

