"""
Тесты для обработки домашних заданий
"""
import pytest
from datetime import datetime
import pytz

@pytest.mark.asyncio
async def test_homework_submission(test_db):
    """Тест отправки домашнего задания"""
    user_id = 12345
    course_id = "тест"
    lesson_num = 1
    
    # Устанавливаем статус ДЗ как ожидающий
    await test_db.execute(
        """INSERT INTO user_courses 
            (user_id, course_id, version_id, status, current_lesson, hw_status) 
            VALUES (?, ?, ?, 'active', ?, 'pending')""",
        (user_id, course_id, "v1", lesson_num)
    )
    await test_db.commit()
    
    # Проверяем статус
    cursor = await test_db.execute(
        "SELECT hw_status FROM user_courses WHERE user_id = ? AND course_id = ?",
        (user_id, course_id)
    )
    result = await cursor.fetchone()
    assert result[0] == "pending"

@pytest.mark.asyncio
async def test_homework_approval(test_db):
    """Тест одобрения домашнего задания"""
    user_id = 12345
    course_id = "тест"
    
    # Добавляем запись с pending ДЗ
    await test_db.execute(
        """INSERT INTO user_courses 
            (user_id, course_id, version_id, status, current_lesson, hw_status) 
            VALUES (?, ?, ?, 'active', 1, 'pending')""",
        (user_id, course_id, "v1")
    )
    await test_db.commit()
    
    # Одобряем ДЗ
    await test_db.execute(
        """UPDATE user_courses 
            SET hw_status = 'approved' 
            WHERE user_id = ? AND course_id = ?""",
        (user_id, course_id)
    )
    await test_db.commit()
    
    # Проверяем
    cursor = await test_db.execute(
        "SELECT hw_status FROM user_courses WHERE user_id = ? AND course_id = ?",
        (user_id, course_id)
    )
    result = await cursor.fetchone()
    assert result[0] == "approved"

@pytest.mark.asyncio
async def test_homework_rejection(test_db):
    """Тест отклонения домашнего задания"""
    user_id = 12345
    course_id = "тест"
    
    # Добавляем запись с pending ДЗ
    await test_db.execute(
        """INSERT INTO user_courses 
            (user_id, course_id, version_id, status, current_lesson, hw_status) 
            VALUES (?, ?, ?, 'active', 1, 'pending')""",
        (user_id, course_id, "v1")
    )
    await test_db.commit()
    
    # Отклоняем ДЗ
    await test_db.execute(
        """UPDATE user_courses 
            SET hw_status = 'rejected' 
            WHERE user_id = ? AND course_id = ?""",
        (user_id, course_id)
    )
    await test_db.commit()
    
    # Проверяем
    cursor = await test_db.execute(
        "SELECT hw_status FROM user_courses WHERE user_id = ? AND course_id = ?",
        (user_id, course_id)
    )
    result = await cursor.fetchone()
    assert result[0] == "rejected"

@pytest.mark.asyncio
async def test_pending_admin_homework_queue(test_db):
    """Тест очереди ДЗ в админ-группе"""
    # Добавляем ДЗ в очередь
    await test_db.execute(
        """INSERT INTO pending_admin_homework 
            (admin_message_id, student_user_id, course_numeric_id, lesson_num, student_message_id) 
            VALUES (?, ?, ?, ?, ?)""",
        (100, 12345, 1, 1, 200)
    )
    await test_db.commit()
    
    # Проверяем
    cursor = await test_db.execute(
        "SELECT * FROM pending_admin_homework WHERE admin_message_id = ?",
        (100,)
    )
    result = await cursor.fetchone()
    
    assert result is not None
    assert result[1] == 12345  # student_user_id
    assert result[2] == 1  # course_numeric_id
    assert result[3] == 1  # lesson_num
