import asyncio,  logging, json, random, string, os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, Text, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton, Message, CallbackQuery
)
from aiogram.utils.markdown import escape_md
import aiosqlite

# Configure logging
logging.basicConfig(level=logging.INFO,
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                   handlers=[
                       logging.FileHandler("data/bot.log"),
                       logging.StreamHandler()
                   ])
logger = logging.getLogger(__name__)

# Bot configuration
API_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = list(map(int, os.getenv('ADMIN_IDS', '').split(',')))
ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID', 0))
LESSONS_CHANNEL_ID = int(os.getenv('LESSONS_CHANNEL_ID', 0))

# Initialize bot and dispatcher
bot = Bot(token=API_TOKEN)
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

# Callback data classes
class CourseCallback(CallbackData, prefix="course"):
    action: str
    course_id: str
    lesson_num: int = 0

# Database initialization
async def init_db():
    """Initialize the database with required tables"""
    os.makedirs('data', exist_ok=True)
    
    async with aiosqlite.connect('data/bot.db') as conn:
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
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            last_lesson_date TIMESTAMP,
            next_lesson_date TIMESTAMP,
            activation_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expiry_date TIMESTAMP,
            is_completed INTEGER DEFAULT 0,
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

# Helper functions
async def log_user_activity(user_id, action, details=""):
    """Log user activity to the database"""
    async with aiosqlite.connect('data/bot.db') as conn:
        await conn.execute(
            "INSERT INTO user_activity (user_id, action, details) VALUES (?, ?, ?)",
            (user_id, action, details)
        )
        await conn.commit()

async def get_user_state(user_id):
    """Get the current state of a user"""
    async with aiosqlite.connect('data/bot.db') as conn:
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

async def set_user_state(user_id, state):
    """Set the state of a user"""
    async with aiosqlite.connect('data/bot.db') as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO user_states (user_id, state, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (user_id, state)
        )
        await conn.commit()

async def resolve_user_id(user_identifier):
    """Resolve user_id from alias or numeric ID"""
    if user_identifier.isdigit():
        return int(user_identifier)
    else:
        # Try to find by alias
        async with aiosqlite.connect('data/bot.db') as conn:
            cursor = await conn.execute(
                "SELECT user_id FROM user_profiles WHERE alias = ?", 
                (user_identifier,)
            )
            result = await cursor.fetchone()
            if result:
                return result[0]
    return None

async def send_lesson_to_user(user_id, course_id, lesson_num):
    """Send lesson content to a user from the channel"""
    async with aiosqlite.connect('data/bot.db') as conn:
        # Get message range for the lesson
        cursor = await conn.execute(
            "SELECT start_message_id, end_message_id FROM lesson_content_map WHERE course_id = ? AND lesson_num = ?",
            (course_id, lesson_num)
        )
        lesson_data = await cursor.fetchone()
        
        if not lesson_data:
            await bot.send_message(user_id, "Урок не найден. Пожалуйста, обратитесь к администратору.")
            return False
            
        start_id, end_id = lesson_data
        
        # Update user state
        await set_user_state(user_id, "RECEIVING_LESSON")
        
        # Send all messages in the range
        for msg_id in range(start_id, end_id + 1):
            try:
                # Using copy_message to maintain privacy and allow customization
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=LESSONS_CHANNEL_ID,
                    message_id=msg_id
                )
                await asyncio.sleep(0.5)  # Prevent flooding
            except Exception as e:
                logger.error(f"Error sending message {msg_id} to user {user_id}: {e}")
                
        # Update user progress
        await conn.execute(
            "UPDATE user_courses SET current_lesson = ?, last_lesson_date = CURRENT_TIMESTAMP WHERE user_id = ? AND course_id = ?",
            (lesson_num, user_id, course_id)
        )
        await conn.commit()
        
        # Send completion message with buttons
        await finalize_lesson_delivery(user_id, course_id, lesson_num)
        
        return True

async def finalize_lesson_delivery(user_id, course_id, lesson_num):
    """Send completion message after delivering a lesson"""
    # Create completion keyboard
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="Урок изучен ✅", callback_data=f"lesson_complete:{course_id}:{lesson_num}"),
        InlineKeyboardButton(text="Отправить домашнее задание 📝", callback_data=f"submit_homework:{course_id}:{lesson_num}")
    )
    
    # Send completion message
    await bot.send_message(
        user_id,
        "Вы получили все материалы урока. Пожалуйста, подтвердите изучение или отправьте домашнее задание.",
        reply_markup=keyboard
    )
    
    # Update user state
    await set_user_state(user_id, "LESSON_RECEIVED")

async def check_and_schedule_lessons():
    """Background task to check and send scheduled lessons"""
    while True:
        try:
            async with aiosqlite.connect('data/bot.db') as conn:
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

async def forward_to_ai_agent(user_id, user_message, course_id=None):
    """Forward user message to AI agent in admin chat for assistance"""
    async with aiosqlite.connect('data/bot.db') as conn:
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

# Command handlers
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    # Check if user exists
    async with aiosqlite.connect('data/bot.db') as conn:
        cursor = await conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
        user_exists = await cursor.fetchone()
        
        if not user_exists:
            # Register new user
            await conn.execute(
                "INSERT INTO users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                (user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
            )
            
            # Create profile
            await conn.execute(
                "INSERT INTO user_profiles (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                (user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name)
            )
            
            # Set initial state
            await conn.execute(
                "INSERT INTO user_states (user_id, state) VALUES (?, ?)",
                (user_id, "MAIN_MENU")
            )
            
            await conn.commit()
            
            # Welcome message for new users
            await message.answer(
                f"Добро пожаловать, {message.from_user.first_name}! 👋\n\n"
                f"Я бот для доступа к обучающим курсам. Чтобы начать обучение, активируйте курс с помощью кодового слова.\n\n"
                f"Используйте команду /activate для активации курса."
            )
            
            # Log registration
            await log_user_activity(user_id, "REGISTRATION", "New user registered")
            
            # Notify admins about new user
            admin_notification = (
                f"🆕 Новый пользователь зарегистрирован!\n"
                f"ID: {user_id}\n"
                f"Имя: {message.from_user.first_name} {message.from_user.last_name or ''}\n"
                f"Username: @{message.from_user.username or 'отсутствует'}"
            )
            await bot.send_message(ADMIN_GROUP_ID, admin_notification)
        else:
            # Welcome back message
            await message.answer(
                f"С возвращением, {message.from_user.first_name}! 👋\n\n"
                f"Используйте /help, чтобы увидеть список доступных команд."
            )
            
            # Log return
            await log_user_activity(user_id, "RETURN", "User returned to bot")

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
    
    await message.answer(help_text, parse_mode="MarkdownV2")

# Support command handler
@dp.message(Command("support"))
async def cmd_support(message: Message):
    """Handler for the /support command to initiate support requests"""
    user_id = message.from_user.id
    
    # Set user state to SUPPORT
    await set_user_state(user_id, "SUPPORT")
    
    await message.answer(
        "📞 *Поддержка*\n\n"
        "Опишите вашу проблему или задайте вопрос. Мы постараемся ответить как можно скорее.\n"
        "Для отмены введите /cancel.",
        parse_mode="MarkdownV2"
    )

# Support message handler
@dp.message(F.text)
async def process_support_request(message: Message):
    """Process messages from users in SUPPORT state"""
    user_id = message.from_user.id
    user_state = await get_user_state(user_id)
    
    if user_state != "SUPPORT":
        return
    
    # Check for cancel command
    if message.text == '/cancel':
        await set_user_state(user_id, "MAIN_MENU")
        await message.answer("Запрос в поддержку отменен.")
        return
    
    # Get user's active course if any
    active_course_id = None
    async with aiosqlite.connect('data/bot.db') as conn:
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
    await message.answer(
        "✅ Ваше сообщение отправлено в поддержку. Мы ответим вам в ближайшее время.\n"
        "Вы можете продолжать пользоваться ботом."
    )
    
    # Reset user state to main menu
    await set_user_state(user_id, "MAIN_MENU")

# Admin command to reply to user
@dp.message(Command("adm_message_user"), F.chat.id == ADMIN_GROUP_ID)
async def adm_message_user(message: Message):
    """Send a message to a user from admin"""
    command_parts = message.text.split(maxsplit=2)
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

# Activation command
@dp.message(Command("activate"))
async def cmd_activate(message: Message):
    """Handler for the /activate command to activate a course"""
    user_id = message.from_user.id
    
    # Set user state to ACTIVATION
    await set_user_state(user_id, "ACTIVATION")
    
    await message.answer(
        "🔑 *Активация курса*\n\n"
        "Введите кодовое слово для активации курса.\n"
        "Для отмены введите /cancel.",
        parse_mode="MarkdownV2"
    )

# Activation code handler
@dp.message(F.text)
async def process_activation_code(message: Message):
    """Process activation codes from users in ACTIVATION state"""
    user_id = message.from_user.id
    user_state = await get_user_state(user_id)
    
    if user_state != "ACTIVATION":
        return
    
    # Check for cancel command
    if message.text == '/cancel':
        await set_user_state(user_id, "MAIN_MENU")
        await message.answer("Активация курса отменена.")
        return
    
    activation_code = message.text.strip()
    
    # Check if code is valid
    async with aiosqlite.connect('data/bot.db') as conn:
        cursor = await conn.execute(
            """
            SELECT cv.course_id, cv.version_id, c.title, cv.title
            FROM course_versions cv
            JOIN courses c ON cv.course_id = c.course_id
            WHERE cv.activation_code = ? AND c.is_active = 1
            """,
            (activation_code,)
        )
        course_data = await cursor.fetchone()
        
        if not course_data:
            await message.answer("❌ Неверный код активации. Пожалуйста, проверьте код и попробуйте снова.")
            return
        
        course_id, version_id, course_title, version_title = course_data
        
        # Check if user already has this course
        cursor = await conn.execute(
            "SELECT 1 FROM user_courses WHERE user_id = ? AND course_id = ?",
            (user_id, course_id)
        )
        already_enrolled = await cursor.fetchone()
        
        if already_enrolled:
            await message.answer(f"❌ Вы уже активировали курс '{course_title}'.")
            await set_user_state(user_id, "MAIN_MENU")
            return
        
        # Enroll user in the course
        await conn.execute(
            """
            INSERT INTO user_courses 
            (user_id, course_id, version_id, current_lesson, activation_date)
            VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)
            """,
            (user_id, course_id, version_id)
        )
        await conn.commit()
        
        # Log activation
        await log_user_activity(
            user_id, 
            "COURSE_ACTIVATION", 
            f"Course: {course_id}, Version: {version_id}, Code: {activation_code}"
        )
        
        # Send confirmation
        await message.answer(
            f"✅ Курс успешно активирован!\n\n"
            f"📚 *{course_title}*\n"
            f"📋 Версия: {version_title}\n\n"
            f"Используйте команду /lesson, чтобы начать обучение.",
            parse_mode="MarkdownV2"
        )
        
        # Reset state
        await set_user_state(user_id, "MAIN_MENU")

# My courses command
@dp.message(Command("mycourses"))
async def cmd_mycourses(message: Message):
    """Handler for the /mycourses command to show user's courses"""
    user_id = message.from_user.id
    
    async with aiosqlite.connect('data/bot.db') as conn:
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
    
    await message.answer(
        courses_text,
        reply_markup=keyboard,
        parse_mode="MarkdownV2"
    )

# Lesson command
@dp.message(Command("lesson"))
async def cmd_lesson(message: Message):
    """Handler for the /lesson command to get current lesson"""
    user_id = message.from_user.id
    
    async with aiosqlite.connect('data/bot.db') as conn:
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

# Обработчик для команды просмотра прогресса
@dp.message(Command("progress"))
async def cmd_progress(message: Message):
    """Handler for the /progress command to show user's progress"""
    user_id = message.from_user.id
    
    async with aiosqlite.connect('data/bot.db') as conn:
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

# Обработчик для колбэков от кнопок
@dp.callback_query()
async def process_callback(callback_query: CallbackQuery):
    """Process callback queries from inline buttons"""
    user_id = callback_query.from_user.id
    data = callback_query.data
    
    # Parse callback data
    if data.startswith("start_lesson:"):
        _, course_id, lesson_num = data.split(":")
        await start_lesson_callback(callback_query, course_id, int(lesson_num))
    
    elif data.startswith("lesson_complete:"):
        _, course_id, lesson_num = data.split(":")
        await complete_lesson_callback(callback_query, course_id, int(lesson_num))
    
    elif data.startswith("submit_homework:"):
        _, course_id, lesson_num = data.split(":")
        await submit_homework_callback(callback_query, course_id, int(lesson_num))
    
    # Другие типы колбэков можно добавить по мере необходимости

async def start_lesson_callback(callback_query: CallbackQuery, course_id, lesson_num):
    """Handle start lesson button callback"""
    user_id = callback_query.from_user.id
    
    # Check if user is in a state where they can receive a lesson
    user_state = await get_user_state(user_id)
    if user_state not in ["MAIN_MENU", "LESSON_COMPLETED"]:
        await callback_query.answer("Вы не можете получить урок в данный момент. Пожалуйста, завершите текущее действие.")
        return
    
    # Get course info
    async with aiosqlite.connect('data/bot.db') as conn:
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

async def complete_lesson_callback(callback_query: CallbackQuery, course_id, lesson_num):
    """Handle lesson complete button callback"""
    user_id = callback_query.from_user.id
    
    # Check if this is the current lesson
    async with aiosqlite.connect('data/bot.db') as conn:
        cursor = await conn.execute(
            """
            SELECT uc.current_lesson, c.total_lessons, c.title,
                   (SELECT COUNT(*) FROM homework 
                    WHERE user_id = ? AND course_id = ? AND lesson_num = ? AND status = 'pending') as pending_homework
            FROM user_courses uc
            JOIN courses c ON uc.course_id = c.course_id
            WHERE uc.user_id = ? AND uc.course_id = ?
            """,
            (user_id, course_id, lesson_num, user_id, course_id)
        )
        lesson_data = await cursor.fetchone()
        
        if not lesson_data or lesson_data[0] != lesson_num:
            await callback_query.answer("Этот урок не является вашим текущим уроком.")
            return
        
        current_lesson, total_lessons, course_title, pending_homework = lesson_data
        
        # Check if homework is required for this lesson
        cursor = await conn.execute(
            "SELECT requires_homework FROM lesson_content_map WHERE course_id = ? AND lesson_num = ?",
            (course_id, lesson_num)
        )
        homework_data = await cursor.fetchone()
        requires_homework = homework_data and homework_data[0] == 1
        
        if requires_homework and pending_homework == 0:  # Homework required but not submitted
            # Update state to waiting for homework
            await set_user_state(user_id, "HOMEWORK_PENDING")
            await callback_query.message.edit_text(
                "Урок отмечен как изученный. Теперь отправьте домашнее задание для проверки."
            )
            return
        
        # Mark lesson as completed
        next_lesson = current_lesson + 1
        
        # Check if this was the last lesson
        if next_lesson > total_lessons:
            # Mark course as completed
            await conn.execute(
                "UPDATE user_courses SET is_completed = 1 WHERE user_id = ? AND course_id = ?",
                (user_id, course_id)
            )
            await conn.commit()
            
            # Update state
            await set_user_state(user_id, "MAIN_MENU")
            
            # Send completion message
            await callback_query.message.edit_text(
                f"🎉 Поздравляем! Вы завершили курс '{course_title}'!"
            )
            
            # Log course completion
            await log_user_activity(user_id, "COURSE_COMPLETED", f"Course: {course_id}")
        else:
            # Schedule next lesson
            next_lesson_date = datetime.now() + timedelta(days=1)  # Default: next day
            
            await conn.execute(
                """
                UPDATE user_courses 
                SET current_lesson = ?, next_lesson_date = ? 
                WHERE user_id = ? AND course_id = ?
                """,
                (next_lesson, next_lesson_date, user_id, course_id)
            )
            await conn.commit()
            
            # Update state
            await set_user_state(user_id, "LESSON_COMPLETED")
            
            # Send completion message
            await callback_query.message.edit_text(
                f"✅ Урок {current_lesson} отмечен как изученный!\n"
                f"Следующий урок будет доступен {next_lesson_date.strftime('%d.%m.%Y')}."
            )
            
            # Log lesson completion
            await log_user_activity(
                user_id, 
                "LESSON_COMPLETED", 
                f"Course: {course_id}, Lesson: {current_lesson}"
            )

async def submit_homework_callback(callback_query: CallbackQuery, course_id, lesson_num):
    """Handle submit homework button callback"""
    user_id = callback_query.from_user.id
    
    # Set state to waiting for homework
    await set_user_state(user_id, "HOMEWORK_PENDING")
    
    # Store course and lesson info in context
    async with aiosqlite.connect('data/bot.db') as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO user_context (user_id, context_data)
            VALUES (?, ?)
            """,
            (user_id, json.dumps({"course_id": course_id, "lesson_num": lesson_num}))
        )
        await conn.commit()
    
    # Prompt for homework
    await callback_query.message.edit_text(
        "📝 Пожалуйста, отправьте ваше домашнее задание.\n"
        "Вы можете отправить текст, фото, видео или документ.\n"
        "Для отмены введите /cancel."
    )

async def process_homework_submission(message: Message):
    """Process homework submission from users"""
    user_id = message.from_user.id
    
    # Get course and lesson from context
    async with aiosqlite.connect('data/bot.db') as conn:
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
        async with aiosqlite.connect('data/bot.db') as conn:
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
        await message.answer(
            "✅ Ваше домашнее задание отправлено на проверку. Мы уведомим вас о результатах."
        )
        
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

# Запуск бота
async def main():
    # Инициализация базы данных
    await init_db()
    
    # Запуск фоновых задач
    asyncio.create_task(check_and_schedule_lessons())
    
    # Запуск бота
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
