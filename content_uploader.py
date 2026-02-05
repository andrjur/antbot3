"""
Дополнительные обработчики для загрузки контента напрямую через Telegram (без группы)
"""
from aiogram import types, F, Bot
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
import aiosqlite
import logging

logger = logging.getLogger(__name__)

# FSM для загрузки урока
class UploadLesson(StatesGroup):
    waiting_course = State()
    waiting_lesson_num = State()
    waiting_level = State()
    waiting_content = State()
    waiting_hw_flag = State()
    waiting_hw_type = State()
    confirm = State()

DB_FILE = "bot.db"

async def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь админом"""
    # Импортируем из main.py
    from main import ADMIN_IDS_CONF
    return user_id in ADMIN_IDS_CONF

@dp.message(Command("upload_lesson"))
async def cmd_upload_lesson(message: types.Message, state: FSMContext):
    """Начало загрузки урока"""
    if not await is_admin(message.from_user.id):
        await message.answer("❌ Эта команда только для администраторов.")
        return
    
    await message.answer(
        "📚 Загрузка урока\n\n"
        "Выберите курс:\n"
        "1. женственность15\n"
        "2. база\n\n"
        "Или введите ID курса:"
    )
    await state.set_state(UploadLesson.waiting_course)

@dp.message(UploadLesson.waiting_course)
async def process_course(message: types.Message, state: FSMContext):
    """Обработка выбора курса"""
    course_map = {
        "1": "женственность15",
        "2": "база",
        "женственность15": "женственность15",
        "база": "база"
    }
    
    course_id = course_map.get(message.text.lower().strip())
    if not course_id:
        await message.answer("❌ Неизвестный курс. Введите 1, 2 или ID курса:")
        return
    
    await state.update_data(course_id=course_id)
    await message.answer("🔢 Введите номер урока (например: 1, 2, 3...):")
    await state.set_state(UploadLesson.waiting_lesson_num)

@dp.message(UploadLesson.waiting_lesson_num)
async def process_lesson_num(message: types.Message, state: FSMContext):
    """Обработка номера урока"""
    try:
        lesson_num = int(message.text.strip())
        if lesson_num < 1:
            await message.answer("❌ Номер урока должен быть больше 0.")
            return
    except ValueError:
        await message.answer("❌ Введите число.")
        return
    
    await state.update_data(lesson_num=lesson_num)
    await message.answer(
        "🎯 Введите уровень сложности:\n"
        "1 - Базовый\n"
        "2 - Средний\n"
        "3 - Продвинутый"
    )
    await state.set_state(UploadLesson.waiting_level)

@dp.message(UploadLesson.waiting_level)
async def process_level(message: types.Message, state: FSMContext):
    """Обработка уровня"""
    try:
        level = int(message.text.strip())
        if level not in [1, 2, 3]:
            await message.answer("❌ Уровень должен быть 1, 2 или 3.")
            return
    except ValueError:
        await message.answer("❌ Введите число 1, 2 или 3.")
        return
    
    await state.update_data(level=level)
    await message.answer(
        "📝 Отправьте контент урока:\n\n"
        "Можно отправить:\n"
        "• Текст\n"
        "• Фото (с подписью)\n"
        "• Видео (с подписью)\n"
        "• Документ\n\n"
        "Для домашнего задания добавьте #hw в начале подписи."
    )
    await state.set_state(UploadLesson.waiting_content)

@dp.message(UploadLesson.waiting_content, F.content_type.in_({'text', 'photo', 'video', 'document'}))
async def process_content(message: types.Message, state: FSMContext, bot: Bot):
    """Обработка контента урока"""
    data = await state.get_data()
    course_id = data['course_id']
    lesson_num = data['lesson_num']
    level = data['level']
    
    # Определяем тип контента и получаем file_id
    content_type = message.content_type
    text = message.caption or message.text or ""
    file_id = None
    
    is_homework = text.startswith('#hw') or '#hw' in text
    hw_type = None
    
    if is_homework:
        # Определяем тип ДЗ
        if '#type_text' in text:
            hw_type = 'text'
        elif '#type_photo' in text:
            hw_type = 'photo'
        elif '#type_video' in text:
            hw_type = 'video'
        elif '#type_file' in text:
            hw_type = 'file'
        else:
            hw_type = 'text'  # По умолчанию
        
        # Убираем теги из текста
        import re
        text = re.sub(r'#hw|#type_\w+', '', text).strip()
    
    if content_type == 'photo':
        file_id = message.photo[-1].file_id
        content_type = 'photo'
    elif content_type == 'video':
        file_id = message.video.file_id
        content_type = 'video'
    elif content_type == 'document':
        file_id = message.document.file_id
        content_type = 'document'
    elif content_type == 'text':
        content_type = 'text'
    
    # Сохраняем в БД
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute('''
                INSERT INTO group_messages 
                (group_id, lesson_num, course_id, content_type, is_homework, hw_type, text, file_id, level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                f'direct_upload_{message.from_user.id}',  # Группа-заглушка
                lesson_num,
                course_id,
                content_type,
                is_homework,
                hw_type,
                text,
                file_id,
                level
            ))
            await conn.commit()
        
        hw_status = "✅ Да" if is_homework else "❌ Нет"
        await message.answer(
            f"✅ Урок успешно загружен!\n\n"
            f"📚 Курс: {course_id}\n"
            f"🔢 Урок: {lesson_num}\n"
            f"🎯 Уровень: {level}\n"
            f"📝 Тип: {content_type}\n"
            f"🏠 ДЗ: {hw_status}\n"
            f"📎 File ID: {file_id[:20] if file_id else 'N/A'}...\n\n"
            f"Отправьте ещё контент для этого урока или /cancel для выхода."
        )
        
        # Сохраняем данные для возможного добавления ещё контента
        await state.update_data(
            course_id=course_id,
            lesson_num=lesson_num,
            level=level
        )
        
    except Exception as e:
        logger.error(f"Ошибка загрузки урока: {e}")
        await message.answer(f"❌ Ошибка при сохранении: {e}")

@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message, state: FSMContext):
    """Отмена загрузки"""
    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        await message.answer("❌ Загрузка отменена.")
    else:
        await message.answer("Нет активной загрузки.")

@dp.message(Command("list_lessons"))
async def cmd_list_lessons(message: types.Message):
    """Показать список загруженных уроков"""
    if not await is_admin(message.from_user.id):
        await message.answer("❌ Только для администраторов.")
        return
    
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute('''
                SELECT course_id, lesson_num, content_type, is_homework, level 
                FROM group_messages 
                WHERE group_id LIKE 'direct_upload_%'
                ORDER BY course_id, lesson_num
            ''')
            rows = await cursor.fetchall()
            
            if not rows:
                await message.answer("📭 Пока нет загруженных уроков.")
                return
            
            result = "📚 Загруженные уроки:\n\n"
            for row in rows:
                course_id, lesson_num, content_type, is_homework, level = row
                hw_marker = " 🏠" if is_homework else ""
                result += f"• {course_id} - Урок {lesson_num} ({content_type}){hw_marker}\n"
            
            await message.answer(result)
            
    except Exception as e:
        logger.error(f"Ошибка получения списка: {e}")
        await message.answer(f"❌ Ошибка: {e}")

print("✅ Модуль загрузки контента загружен")
