"""
Тестовая конфигурация для pytest
"""
import pytest
import asyncio
import aiosqlite
import os
import tempfile
from unittest.mock import Mock, AsyncMock

# Тестовая база данных
TEST_DB_FILE = "test_bot.db"

@pytest.fixture
def event_loop():
    """Создает event loop для тестов"""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def test_db():
    """Создает временную тестовую базу данных"""
    # Создаем временный файл для БД
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    # Создаем соединение с тестовой БД
    conn = await aiosqlite.connect(db_path)
    
    # Создаем все таблицы (копия структуры из main.py)
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            timezone TEXT DEFAULT 'Europe/Moscow',
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS courses (
            course_id TEXT PRIMARY KEY,
            
            group_id TEXT,
            title TEXT,
            course_type TEXT DEFAULT 'LESSON_BASED',
            message_interval REAL DEFAULT 24,
            description TEXT
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS course_versions (
            course_id TEXT,
            version_id TEXT,
            title TEXT,
            price REAL,
            description TEXT,
            PRIMARY KEY (course_id, version_id)
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS user_courses (
            user_id INTEGER,
            course_id TEXT,
            version_id TEXT,
            status TEXT DEFAULT 'active',
            hw_status TEXT DEFAULT 'none',
            hw_type TEXT,
            current_lesson INTEGER DEFAULT 0,
            level INTEGER DEFAULT 1,
            first_lesson_sent_time DATETIME,
            last_lesson_sent_time DATETIME,
            is_completed INTEGER DEFAULT 0,
            last_menu_message_id INTEGER,
            activation_date TIMESTAMP,
            PRIMARY KEY (user_id, course_id, version_id)
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS group_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT,
            lesson_num INTEGER,
            course_id TEXT,
            content_type TEXT,
            is_homework BOOLEAN,
            hw_type TEXT,
            text TEXT,
            file_id TEXT,
            message_id INTEGER
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS course_activation_codes (
            code_word TEXT PRIMARY KEY,
            course_id TEXT,
            version_id TEXT,
            price_rub INTEGER
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS pending_admin_homework (
            admin_message_id INTEGER PRIMARY KEY,
            student_user_id INTEGER,
            course_numeric_id INTEGER,
            lesson_num INTEGER,
            student_message_id INTEGER
        )
    ''')
    
    await conn.execute('''
        CREATE TABLE IF NOT EXISTS user_actions_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action_type TEXT,
            course_id TEXT,
            lesson_num INTEGER,
            old_value TEXT,
            new_value TEXT,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    await conn.commit()
    
    yield conn
    
    # Очистка после теста
    await conn.close()
    os.unlink(db_path)

@pytest.fixture
def mock_bot():
    """Создает мок-объект бота Telegram"""
    bot = AsyncMock()
    bot.send_message = AsyncMock(return_value=Mock(message_id=123))
    bot.edit_message_text = AsyncMock()
    bot.send_photo = AsyncMock()
    bot.send_video = AsyncMock()
    bot.get_chat = AsyncMock(return_value=Mock(full_name="Test User", username="testuser"))
    return bot

@pytest.fixture
def sample_settings():
    """Тестовые настройки"""
    return {
        "message_interval": 0.125,
        "tariff_names": {
            "v1": "Соло",
            "v2": "с проверкой",
            "v3": "Премиум"
        },
        "groups": {
            "-1002590412715": "женственность15",
            "-1002549199868": "база"
        },
        "activation_codes": {
            "x": {"course": "женственность15", "version": "v1", "price": 3000},
            "y": {"course": "женственность15", "version": "v2", "price": 8000},
            "test": {"course": "база", "version": "v1", "price": 5000}
        }
    }
