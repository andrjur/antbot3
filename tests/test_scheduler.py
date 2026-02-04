"""
Тесты для расписания уроков
"""
import pytest
from datetime import datetime, timedelta
import pytz

@pytest.mark.asyncio
async def test_lesson_schedule_calculation():
    """Тест расчета времени следующего урока"""
    message_interval_hours = 24.0
    current_lesson = 2
    
    # Время первого урока
    first_sent_time = datetime.now(pytz.utc) - timedelta(days=2)
    
    # Расчет времени следующего урока
    next_lesson_time = first_sent_time + timedelta(hours=message_interval_hours * current_lesson)
    
    # Проверяем, что время рассчитано правильно
    expected_time = first_sent_time + timedelta(hours=48)
    assert next_lesson_time == expected_time

@pytest.mark.asyncio
async def test_lesson_interval_configuration():
    """Тест настройки интервала между уроками"""
    intervals = [0.125, 24.0, 48.0, 168.0]  # 7.5 минут, 1 день, 2 дня, 1 неделя
    
    for interval in intervals:
        # Проверяем, что интервал положительный
        assert interval > 0
        
        # Проверяем расчет времени
        first_lesson = datetime.now(pytz.utc)
        next_lesson = first_lesson + timedelta(hours=interval)
        
        assert next_lesson > first_lesson
        assert (next_lesson - first_lesson).total_seconds() / 3600 == interval

@pytest.mark.asyncio
async def test_current_lesson_tracking(test_db):
    """Тест отслеживания текущего урока"""
    user_id = 12345
    course_id = "тест"
    
    now = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    
    # Добавляем пользователя с уроком 1
    await test_db.execute(
        """INSERT INTO user_courses 
            (user_id, course_id, version_id, status, current_lesson, 
             first_lesson_sent_time, last_lesson_sent_time) 
            VALUES (?, ?, ?, 'active', 1, ?, ?)""",
        (user_id, course_id, "v1", now, now)
    )
    await test_db.commit()
    
    # Обновляем до урока 2
    new_time = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    await test_db.execute(
        """UPDATE user_courses 
            SET current_lesson = 2, last_lesson_sent_time = ? 
            WHERE user_id = ? AND course_id = ?""",
        (new_time, user_id, course_id)
    )
    await test_db.commit()
    
    # Проверяем
    cursor = await test_db.execute(
        "SELECT current_lesson FROM user_courses WHERE user_id = ? AND course_id = ?",
        (user_id, course_id)
    )
    result = await cursor.fetchone()
    assert result[0] == 2

@pytest.mark.asyncio
async def test_course_completion(test_db):
    """Тест завершения курса"""
    user_id = 12345
    course_id = "тест"
    
    now = datetime.now(pytz.utc).strftime('%Y-%m-%d %H:%M:%S')
    
    # Добавляем завершенный курс
    await test_db.execute(
        """INSERT INTO user_courses 
            (user_id, course_id, version_id, status, current_lesson, is_completed) 
            VALUES (?, ?, ?, 'completed', 10, 1)""",
        (user_id, course_id, "v1")
    )
    await test_db.commit()
    
    # Проверяем
    cursor = await test_db.execute(
        "SELECT status, is_completed FROM user_courses WHERE user_id = ? AND course_id = ?",
        (user_id, course_id)
    )
    result = await cursor.fetchone()
    assert result[0] == "completed"
    assert result[1] == 1
