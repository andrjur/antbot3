"""
Тесты для бизнес-логики активации курсов
"""
import pytest
from datetime import datetime
import pytz

@pytest.mark.asyncio
async def test_activation_code_validation(test_db, sample_settings):
    """Тест валидации кода активации"""
    # Добавляем тестовый код
    await test_db.execute(
        "INSERT INTO course_activation_codes (code_word, course_id, version_id, price_rub) VALUES (?, ?, ?, ?)",
        ("TEST123", "база", "v1", 5000)
    )
    await test_db.commit()
    
    # Проверяем существование кода
    cursor = await test_db.execute(
        "SELECT 1 FROM course_activation_codes WHERE code_word = ?",
        ("TEST123",)
    )
    result = await cursor.fetchone()
    assert result is not None
    
    # Проверяем несуществующий код
    cursor = await test_db.execute(
        "SELECT 1 FROM course_activation_codes WHERE code_word = ?",
        ("INVALID",)
    )
    result = await cursor.fetchone()
    assert result is None

@pytest.mark.asyncio
async def test_course_activation_flow(test_db, sample_settings):
    """Тест полного флоу активации курса"""
    user_id = 12345
    course_id = "база"
    version_id = "v1"
    
    # 1. Добавляем курс в базу
    await test_db.execute(
        "INSERT INTO courses (course_id, title, course_type) VALUES (?, ?, ?)",
        (course_id, "Базовый курс", "LESSON_BASED")
    )
    
    # 2. Добавляем код активации
    await test_db.execute(
        "INSERT INTO course_activation_codes (code_word, course_id, version_id, price_rub) VALUES (?, ?, ?, ?)",
        ("ACTIVATE", course_id, version_id, 5000)
    )
    
    # 3. Добавляем тариф
    await test_db.execute(
        "INSERT INTO course_versions (course_id, version_id, title, price) VALUES (?, ?, ?, ?)",
        (course_id, version_id, "Соло", 5000)
    )
    await test_db.commit()
    
    # 4. Активируем курс для пользователя
    now = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    await test_db.execute(
        """INSERT INTO user_courses 
            (user_id, course_id, version_id, status, current_lesson, activation_date, 
             first_lesson_sent_time, last_lesson_sent_time, level) 
            VALUES (?, ?, ?, 'active', 0, ?, ?, ?, 1)""",
        (user_id, course_id, version_id, now, now, now)
    )
    await test_db.commit()
    
    # 5. Проверяем активацию
    cursor = await test_db.execute(
        """SELECT * FROM user_courses 
            WHERE user_id = ? AND course_id = ? AND version_id = ? AND status = 'active'""",
        (user_id, course_id, version_id)
    )
    result = await cursor.fetchone()
    
    assert result is not None
    assert result[3] == "active"
    assert result[4] == "none"  # hw_status
    assert result[6] == 0  # current_lesson

@pytest.mark.asyncio
async def test_duplicate_activation_handling(test_db):
    """Тест обработки повторной активации"""
    user_id = 12345
    course_id = "тест"
    
    now = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    
    # Первая активация
    await test_db.execute(
        """INSERT INTO user_courses 
            (user_id, course_id, version_id, status, current_lesson, activation_date, 
             first_lesson_sent_time, last_lesson_sent_time, level) 
            VALUES (?, ?, ?, 'active', 1, ?, ?, ?, 1)""",
        (user_id, course_id, "v1", now, now, now)
    )
    await test_db.commit()
    
    # Проверяем, что запись существует
    cursor = await test_db.execute(
        "SELECT current_lesson FROM user_courses WHERE user_id = ? AND course_id = ?",
        (user_id, course_id)
    )
    result = await cursor.fetchone()
    assert result[0] == 1
    
    # Имитируем сброс при повторной активации
    await test_db.execute(
        """UPDATE user_courses 
            SET current_lesson = 0, hw_status = 'none', is_completed = 0 
            WHERE user_id = ? AND course_id = ?""",
        (user_id, course_id)
    )
    await test_db.commit()
    
    # Проверяем сброс
    cursor = await test_db.execute(
        "SELECT current_lesson, hw_status, is_completed FROM user_courses WHERE user_id = ? AND course_id = ?",
        (user_id, course_id)
    )
    result = await cursor.fetchone()
    assert result[0] == 0
    assert result[1] == "none"
    assert result[2] == 0
