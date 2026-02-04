"""
Тесты для работы с базой данных
"""
import pytest
import aiosqlite
from datetime import datetime
import pytz

@pytest.mark.asyncio
async def test_database_connection(test_db):
    """Тест подключения к базе данных"""
    cursor = await test_db.execute("SELECT 1")
    result = await cursor.fetchone()
    assert result[0] == 1

@pytest.mark.asyncio
async def test_users_table_creation(test_db):
    """Тест создания таблицы пользователей"""
    # Вставляем тестового пользователя
    await test_db.execute(
        "INSERT INTO users (user_id, username, first_name, timezone) VALUES (?, ?, ?, ?)",
        (12345, "testuser", "Test User", "Europe/Moscow")
    )
    await test_db.commit()
    
    # Проверяем, что пользователь добавлен
    cursor = await test_db.execute("SELECT * FROM users WHERE user_id = ?", (12345,))
    user = await cursor.fetchone()
    
    assert user is not None
    assert user[0] == 12345
    assert user[1] == "testuser"
    assert user[3] == "Europe/Moscow"

@pytest.mark.asyncio
async def test_courses_table_creation(test_db):
    """Тест создания таблицы курсов"""
    await test_db.execute(
        "INSERT INTO courses (course_id, group_id, title, course_type) VALUES (?, ?, ?, ?)",
        ("test_course", "-1001234567890", "Test Course", "LESSON_BASED")
    )
    await test_db.commit()
    
    cursor = await test_db.execute("SELECT * FROM courses WHERE course_id = ?", ("test_course",))
    course = await cursor.fetchone()
    
    assert course is not None
    assert course[0] == "test_course"
    assert course[2] == "Test Course"

@pytest.mark.asyncio
async def test_activation_codes_table(test_db):
    """Тест таблицы кодов активации"""
    await test_db.execute(
        "INSERT INTO course_activation_codes (code_word, course_id, version_id, price_rub) VALUES (?, ?, ?, ?)",
        ("TESTCODE", "test_course", "v1", 1000)
    )
    await test_db.commit()
    
    cursor = await test_db.execute("SELECT * FROM course_activation_codes WHERE code_word = ?", ("TESTCODE",))
    code = await cursor.fetchone()
    
    assert code is not None
    assert code[0] == "TESTCODE"
    assert code[2] == "v1"
    assert code[3] == 1000

@pytest.mark.asyncio
async def test_user_courses_table(test_db):
    """Тест таблицы курсов пользователей"""
    now = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    
    await test_db.execute(
        """INSERT INTO user_courses 
            (user_id, course_id, version_id, status, current_lesson, activation_date) 
            VALUES (?, ?, ?, ?, ?, ?)""",
        (12345, "test_course", "v1", "active", 1, now)
    )
    await test_db.commit()
    
    cursor = await test_db.execute(
        "SELECT * FROM user_courses WHERE user_id = ? AND course_id = ?",
        (12345, "test_course")
    )
    user_course = await cursor.fetchone()
    
    assert user_course is not None
    assert user_course[0] == 12345
    assert user_course[1] == "test_course"
    assert user_course[3] == "active"

@pytest.mark.asyncio
async def test_user_actions_log(test_db):
    """Тест таблицы логирования действий"""
    await test_db.execute(
        """INSERT INTO user_actions_log 
            (user_id, action_type, course_id, details) 
            VALUES (?, ?, ?, ?)""",
        (12345, "TEST_ACTION", "test_course", "Test details")
    )
    await test_db.commit()
    
    cursor = await test_db.execute(
        "SELECT * FROM user_actions_log WHERE user_id = ?",
        (12345,)
    )
    log_entry = await cursor.fetchone()
    
    assert log_entry is not None
    assert log_entry[1] == 12345
    assert log_entry[2] == "TEST_ACTION"
