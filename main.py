# -*- coding: utf-8 -*-
import asyncio, logging, json, random, os, re, shutil, sys
import functools, sqlite3, aiosqlite
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F, md
from aiogram.filters import Command, CommandStart, BaseFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Фикс для консоли Windows
if sys.stdout.encoding != 'utf-8':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Загрузка переменных из .env
load_dotenv()

MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 3

def setup_logging():
    """Настройка логирования с ротацией и UTF-8"""
    log_file = 'bot.log'
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(lineno)d [%(funcName)s] - %(message)s  %(levelname)s',
        datefmt='%H:%M:%S',
        handlers=[
            RotatingFileHandler(
                log_file,
                maxBytes=MAX_LOG_SIZE,
                backupCount=LOG_BACKUP_COUNT,
                encoding='utf-8'  # Фикс кодировки для Windows
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

ADMIN_GROUP_ID = int(os.getenv('ADMIN_GROUP_ID', 0))

SETTINGS_FILE = "settings.json"

DB_FILE = "bot.db"
MAX_LESSONS_PER_PAGE = 7  # пагинация для view_completed_course
DEFAULT_COUNT_MESSAGES = 7  # макс количество сообщений при выводе курсов

# Initialize bot and dispatcher
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN_V2)
)
dp = Dispatcher()

# Callback data classes
class CourseCallback(CallbackData, prefix="course"):
    action: str
    course_id: int
    lesson_num: int = 0


# Callback data classes
class AdminHomeworkCallback(CallbackData, prefix="admin_hw"):
    action: str  # approve_hw/reject_hw
    user_id: int
    course_id: int
    lesson_num: int
    message_id: int

class HomeworkActionCallback(CallbackData, prefix="hw_action"):
    action: str  # approve/reject
    user_id: int
    course_id: int
    lesson_num: int
    message_id: int

class RejectFinalCallback(CallbackData, prefix="reject_final"):
    user_id: int
    course_id: int
    lesson_num: int
    message_id: int
    admin_id: int

class ApproveWithFeedbackCallback(CallbackData, prefix="approve_feedback"):
    user_id: int
    course_id: int
    lesson_num: int
    message_id: int

class RejectWithFeedbackCallback(CallbackData, prefix="reject_feedback"):
    user_id: int
    course_id: int
    lesson_num: int
    message_id: int

class ApproveFinalCallback(CallbackData, prefix="approve_final"):
    user_id: int
    course_id: int
    lesson_num: int
    message_id: int


# Filters
class IsAdmin(BaseFilter):
    """Custom filter to check if user in admin list"""
    async def __call__(self, message: types.Message) -> bool:
        # Check if the user is a member of the admin group
        try:
            member = await bot.get_chat_member(chat_id=ADMIN_GROUP_ID, user_id=message.from_user.id)
            return member.status in ["creator", "administrator"]
        except TelegramAPIError as e:
            logger.error(f"Failed to check admin status: {e}")
            return False

class Form(StatesGroup):
    """Feedback Form"""
    feedback = State()

# декоратор для обработки ошибок в БД
def db_exception_handler(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except sqlite3.OperationalError as e:
            logger.error(f"Database is locked {func.__name__}: {e}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("База данных заблокирована. Попробуйте позже.")
                    break
            return None
        except aiosqlite.Error as e:
            logger.error(f"Database error in {func.__name__}: {e}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла ошибка при работе с базой данных.")
                    break
            return None
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}")
            # Find the message object to send error response
            for arg in args:
                if isinstance(arg, Message):
                    await arg.answer("Произошла неизвестная ошибка.")
                    break
            return None
    return wrapper
### End filters... # 14-04
async def populate_course_versions(settings):
    """Заполняет таблицу course_versions данными из settings.json."""
    logger.info("Заполнение таблицы course_versions...")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            for code, data in settings["activation_codes"].items():
                course_id = data["course"]
                version_id = data["version"]

                # Check if course_id and version_id already exist in course_versions
                cursor = await conn.execute("SELECT 1 FROM course_versions WHERE course_id = ? AND version_id = ?", (course_id, version_id))
                existing_record = await cursor.fetchone()

                if not existing_record:
                    # Get title and price from settings
                    version_title = settings["tariff_names"].get(version_id, "Базовый")
                    version_price = data["price"]

                    # Insert the record if it doesn't exist
                    await conn.execute("""
                        INSERT INTO course_versions (course_id, version_id, title, price)
                        VALUES (?, ?, ?, ?)
                    """, (course_id, version_id, version_title, version_price))
                    logger.info(f"Добавлена запись в course_versions: {course_id=}, {version_id=}, {version_title=}, {version_price=}")
                else:
                     logger.info(f"Запись уже существует в course_versions: {course_id=}, {version_id=}")
            await conn.commit()
        logger.info("Таблица course_versions успешно заполнена.")
    except Exception as e:
        logger.error(f"Ошибка при заполнении таблицы course_versions: {e}")

async def load_settings():
    """Загружает настройки из файла settings.json и заполняет таблицу course_versions."""
    logger.info(f"333444 load_settings ")
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                logger.info(f"Загрузка настроек из файла: {SETTINGS_FILE}")
                settings = json.load(f)
                logger.info(f"Настройки settings.json {len(settings)=} {settings.keys()=}")
                logger.info(f"Настройки успешно загружены. {settings['groups']=}")

                # Заполнение таблицы course_versions
                asyncio.create_task(populate_course_versions(settings))

                return settings
        except json.JSONDecodeError:
            logger.error("8889 Ошибка при декодировании JSON.")
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

# Переменные для хранения задач и времени последней отправки статистики
lesson_check_tasks = {}
last_stats_sent = None # 14-04 todo нафига

# Создаем кэш для хранения информации о курсе и тарифе
course_info_cache = {}

# 14-04 todo нафига
async def get_all_courses():
    """Получает список всех курсов."""
    logger.info('get_all_courses')
    courses = []
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT course_id, title FROM courses")
            rows = await cursor.fetchall()
            courses = [(row[0], row[1]) for row in rows]
    except Exception as e:
        logger.error(f"Ошибка при получении списка курсов: {e}")
        return []
    return courses


# course_numeric_id = await get_course_id_int(course_id)
async def get_course_id_int(course_id: str) -> int:
    """Получает название курса по ID."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT id FROM courses WHERE course_id = ?", (course_id,))
            result = await cursor.fetchone()
            if result:
                logger.info(f"get_course_id_int {result=} берём return result[0]")
                return result[0]
            else:
                logger.error(f"Курс с ID {course_id=} не найден в базе данных.")
                return 0
    except Exception as e:
        logger.error(f"Ошибка при получении course_id курса: {e}")
        return 0


# course_id = get_course_id_str(course_numeric_id)
async def get_course_id_str(course_numeric_id: int) -> str:
    """Получает название курса по ID."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT course_id FROM courses WHERE id = ?", (course_numeric_id,))
            result = await cursor.fetchone()
            if result:
                logger.info(f"{result=} берём return result[0]")
                return result[0]
            else:
                logger.error(f"Курс с ID {course_numeric_id} не найден в базе данных.")
                return "Неизвестный курс"
    except Exception as e:
        logger.error(f"Ошибка при получении course_id курса: {e}")
        return "Неизвестный курс"

async def get_course_title(course_id: str) -> str:
    """Получает название курса по ID."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT title FROM courses WHERE course_id = ?", (course_id,))
            result = await cursor.fetchone()
            if result:
                return result[0]
            else:
                return "Неизвестный курс"
    except Exception as e:
        logger.error(f"Ошибка при получении названия курса: {e}")
        return "Неизвестный курс"

# 14-04
async def is_valid_activation_code(code: str) -> bool:
    """Проверяет, существует ли код активации в базе данных."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT 1 FROM course_activation_codes WHERE code_word = ?", (code,))
            result = await cursor.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Ошибка при проверке кода активации: {e}")
        return False


# 14-04
async def activate_course(user_id: int, activation_code: str):
    """Активирует курс для пользователя."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Шаг 1: Получаем данные об активационном коде
            cursor = await conn.execute("""
                SELECT course_id, version_id FROM course_activation_codes WHERE code_word = ?
            """, (activation_code,))
            code_data = await cursor.fetchone()

            if not code_data:
                return False, "❌ Неверный код активации."

            course_id, version_id = code_data

            # Шаг 2: Проверяем, есть ли у пользователя уже активный курс
            cursor = await conn.execute("""
                SELECT 1 FROM user_courses WHERE user_id = ? AND course_id = ? AND status = 'active'
            """, (user_id, course_id))
            existing_course = await cursor.fetchone()

            if existing_course:
                return False, "❌ У вас уже активирован этот курс."

            # Шаг 3: Активируем курс для пользователя
            await conn.execute("""
                INSERT INTO user_courses (user_id, course_id, version_id, activation_code, status)
                VALUES (?, ?, ?, ?, 'active')
            """, (user_id, course_id, version_id, activation_code))
            await conn.commit()

            # Запускаем проверку расписания для пользователя
            await start_lesson_schedule_task(user_id)

            return True, "✅ Курс успешно активирован!"
    except Exception as e:
        logger.error(f"Ошибка при активации курса: {e}")
        return False, "⚠️ Произошла ошибка при активации курса. Попробуйте позже."

# 14-04
async def deactivate_course(user_id: int, course_id: str):
    """Деактивирует курс для пользователя."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Шаг 1: Деактивируем курс
            await conn.execute("""
                UPDATE user_courses SET status = 'inactive' WHERE user_id = ? AND course_id = ?
            """, (user_id, course_id))
            await conn.commit()

            # Шаг 2: Останавливаем проверку расписания для пользователя
            await stop_lesson_schedule_task(user_id)

            return True, "✅ Курс успешно деактивирован."
    except Exception as e:
        logger.error(f"Ошибка при деактивации курса: {e}")
        return False, "⚠️ Произошла ошибка при деактивации курса. Попробуйте позже."



@lru_cache(maxsize=128)
async def get_lesson_content(course_id: str, lesson_num: int) -> list:
    """Кэширует контент уроков для быстрой загрузки"""
    logger.info(f"Запрошен контент для {course_id=}, {lesson_num=}")
    lessons = []

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT text, content_type, file_id, is_homework, hw_type
                FROM lessons
                WHERE course_id = ? AND lesson_num = ?
                ORDER BY id
            """, (course_id, lesson_num))
            rows = await cursor.fetchall()
            logger.info(f"Контент урока для {course_id=}, {lesson_num=} успешно получен.")

            for row in rows:
                text, content_type, file_id, is_homework, hw_type = row
                lessons.append((text, content_type, file_id, is_homework, hw_type))
                logger.info(f"Добавлен урок {len(lessons)=} из базы данных")

    except Exception as e:
        logger.error(f"Ошибка при получении контента урока: {e}")
        return []
    return lessons


@db_exception_handler
async def check_lesson_schedule(user_id: int, hours = 24, minutes = 0):
    """Проверяет расписание уроков и отправляет урок, если пришло время."""
    logger.info(f"🔄 Проверка расписания для пользователя {user_id=} {hours=} {minutes=}")

    try:
        # Шаг 1: Получаем данные пользователя из базы данных
        try:
            async with aiosqlite.connect(DB_FILE) as conn:
                logger.info(f"✅ Успешно подключились к базе данных")
                cursor = await conn.execute("""
                    SELECT course_id, current_lesson, version_id, 
                           last_lesson_sent_time, hw_status, last_menu_message_id
                    FROM user_courses 
                    WHERE user_id = ? AND status = 'active'
                """, (user_id,))
                user_data = await cursor.fetchone()
        except aiosqlite.Error as e:
            logger.error(f"❌ Ошибка при подключении к базе данных: {e}")
            return  # Выходим из функции при ошибке подключения к БД

        # Проверяем, что данные пользователя были получены
        if not user_data:
            logger.warning(f"У пользователя {user_id} нет активных курсов.")
            return  # Выходим из функции, если нет данных

        course_id, current_lesson, version_id, last_sent_time, hw_status, menu_message_id = user_data
        logger.info(f"✅ Данные пользователя: {course_id=}, {current_lesson=}, {version_id=}, {last_sent_time=}, {hw_status=}, {menu_message_id=}")

        # Шаг 2: Проверка статуса ДЗ
        if hw_status not in ('approved', 'not_required', "none"):
            logger.info(f"⏳ Ожидаем ДЗ или проверку: {hw_status=}")
            return  # Выходим, если ждем ДЗ

        # Шаг 3: Получение интервала отправки сообщений
        message_interval = settings.get("message_interval", 24) # из настроек берем

        logger.info(f"⏳✅⏳ 13 message_interval={message_interval=}") # выводим
        # Шаг 4: Проверка времени отправки последнего сообщения
        if last_sent_time: # если что-то есть
            logger.info(f"✅ 2 14 last_sent_time={last_sent_time=}")
            try:
                # Преобразуем строку времени в объект datetime
                last_sent = datetime.strptime(last_sent_time, '%Y-%m-%d %H:%M:%S') # преобразуем
                next_time = last_sent + timedelta(hours=message_interval) # считаем следующее время

                time_left = next_time - datetime.now() # считаем разницу
                logger.info(f"✅ 3 15 time_left.total_seconds()={time_left.total_seconds()=}")
                # Если время еще не пришло
                if time_left.total_seconds() > 0 or (hours == 0 and minutes == 0): # 0 0 = принудительно отправить
                    hours = time_left.seconds // 3600 # часы
                    minutes = (time_left.seconds % 3600) // 60 # минуты
                    time_message = f"⏳ Следующий урок через {hours}ч {minutes}мин\n" # формируем сообщение
                    # Используем get_next_lesson_time для получения времени следующего урока
                    next_lesson_time = await get_next_lesson_time(user_id, course_id)
                    time_message = f"⏳ Следующий урок {next_lesson_time}\n" # todo выбрать можно это
                    logger.info(f"✅ 14 {time_message=}")

                    course_numeric_id = await get_course_id_int(course_id) # инт id курса
                    # Получаем клавиатуру
                    keyboard = get_main_menu_inline_keyboard(
                        course_numeric_id=course_numeric_id, #course_numeric_id
                        lesson_num=current_lesson,
                        user_tariff=version_id,
                    )

                    # Пытаемся обновить сообщение
                    if menu_message_id: # если id сообщения есть
                        try:
                            logger.info(f"✅ 15 перед await bot.edit_message_text(")
                            await asyncio.sleep(0.1)  # Дадим базе данных передохнуть!
                            await bot.edit_message_text(
                                chat_id=user_id,
                                message_id=menu_message_id,
                                text=time_message,
                                reply_markup=keyboard
                            )
                            logger.info(f"Тихо обновили сообщение для {user_id}") # Обновили

                        except TelegramBadRequest as e: # если не вышло
                            logger.warning(f"Не удалось обновить сообщение: {e}")

                            # Если сообщение не найдено, сбрасываем ID
                            try:
                                async with aiosqlite.connect(DB_FILE) as conn:

                                    await conn.execute("""
                                        UPDATE user_courses 
                                        SET last_menu_message_id = NULL 
                                        WHERE user_id = ?
                                    """, (user_id,))
                                    await conn.commit()
                            except sqlite3.OperationalError as e:
                                logger.error(f"❌ Ошибка при обновлении menu_message_id: {e}") # если и тут не получилось

                else:  # Если пора отправлять
                    logger.info(f"🔄🔄🔄 время пришло отправляем след урок: {course_id=} {current_lesson=} {user_id=}")
                    await send_lesson_to_user(user_id, course_id, current_lesson+1) # отправляем
                    logger.info(f"✅ 555 после  await send_lesson_to_user(user_id, course_id, current_lesson+1) ")

                    # Обновляем время последнего урока
                    now = datetime.now()
                    now_str = now.strftime('%Y-%m-%d %H:%M:%S')

                    attempts = 3
                    for attempt in range(attempts):
                        try:
                            async with aiosqlite.connect(DB_FILE) as conn:
                                await conn.execute("""
                                    UPDATE user_courses 
                                    SET last_lesson_sent_time = ? 
                                    WHERE user_id = ? AND course_id = ?
                                """, (now_str, user_id, course_id))  # передаем в строке!
                                await conn.commit()

                                logger.info(f"✅ Время для {user_id} успешно обновлено (попытка {attempt + 1}).")
                                break  # Выходим из цикла, если успешно
                        except sqlite3.OperationalError as e:
                            if "locked" in str(e) and attempt < attempts - 1:
                                wait_time = random.uniform(0.1, 0.5)  # Случайная задержка
                                logger.warning(
                                    f"База заблокирована для {user_id}, попытка {attempt + 1}/{attempts}. Повтор через {wait_time:.2f} сек.")
                                await asyncio.sleep(wait_time)
                            else:
                                logger.error(
                                    f"Не удалось обновить время для {user_id} после {attempts} попыток: {e}")
                                #Перевыбрасываем ошибку, чтобы ее обработал внешний except
                                raise
            except ValueError as e:
                logger.error(f"⚠️ Ошибка преобразования времени: {e}")
                await bot.send_message(user_id, "📛 Ошибка времени урока!", parse_mode=None)
                return

        logger.info(f"✅ 166 выходим из функции") # логи
        return  # ВЫХОДИМ ИЗ ФУНКЦИИ

    except Exception as e: # общая ошибка
        logger.error(f"💥 Бот немного захворал: {e}", exc_info=True)
        await bot.send_message(user_id, "📛 Ошибка расписания. Мы уже чиним робота!", parse_mode=None)


async def scheduled_lesson_check(user_id: int):
    """Запускает проверку расписания уроков для пользователя каждые 7 минут."""
    while True:
        await check_lesson_schedule(user_id)
        await asyncio.sleep(2 * 60)  # Каждые 2 минуты

async def send_admin_stats():
    """Отправляет статистику администраторам каждые 5 часов."""
    global last_stats_sent
    while True:
        now = datetime.now()
        # Если статистику еще не отправляли или прошло 5 часов
        if last_stats_sent is None or now - last_stats_sent >= timedelta(hours=5):
            # Собираем статистику (магия данных!)
            stats = await gather_course_statistics()

            # Формируем сообщение с эмодзи для красоты
            stat_message = f"📊 Статистика бота:\n\n{stats}"

            # Отправляем в группу админов (теперь не спам, а групповой чат)
            try:
                await bot.send_message(ADMIN_GROUP_ID, stat_message, parse_mode=ParseMode.MARKDOWN_V2)
            except Exception as e:
                logger.error(f"❌ Не удалось отправить статистику админам: {e}")

            # Запоминаем время последней отправки (чтобы не доставать админов чаще)
            last_stats_sent = now
        await asyncio.sleep(5 * 3600)  # Каждые 5 часов

async def gather_course_statistics():
    """Собирает статистику по курсам."""
    total_users = 0
    active_courses = 0
    solo_tariff_count = 0
    group_tariff_count = 0

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Общее количество пользователей
            cursor = await conn.execute("SELECT COUNT(DISTINCT user_id) FROM user_courses")
            total_users = (await cursor.fetchone())[0]

            # Количество активных курсов
            cursor = await conn.execute("SELECT COUNT(*) FROM user_courses WHERE status = 'active'")
            active_courses = (await cursor.fetchone())[0]

            # Распределение пользователей по тарифам (пример для тарифов "Соло" и "Группа")
            cursor = await conn.execute("SELECT COUNT(*) FROM user_courses WHERE version_id = 'v1' AND status = 'active'")
            solo_tariff_count = (await cursor.fetchone())[0]

            cursor = await conn.execute("SELECT COUNT(*) FROM user_courses WHERE version_id = 'v2' AND status = 'active'")
            group_tariff_count = (await cursor.fetchone())[0]
    except Exception as e:
        logger.error(f"Ошибка при сборе статистики: {e}")

    return (
        f"Всего пользователей: {total_users}\n"
        f"Активных курсов: {active_courses}\n"
        f"Тариф \"Соло\": {solo_tariff_count}\n"
        f"Тариф \"Группа\": {group_tariff_count}\n"
    )

async def start_lesson_schedule_task(user_id: int):
    """Запускает периодическую проверку расписания уроков для пользователя."""
    if user_id not in lesson_check_tasks:
        task = asyncio.create_task(scheduled_lesson_check(user_id))
        lesson_check_tasks[user_id] = task
        logger.info(f" 500 start_lesson_schedule_task Запущена задача проверки расписания уроков для пользователя {user_id}.")

async def stop_lesson_schedule_task(user_id: int):
    """Останавливает периодическую проверку расписания уроков для пользователя."""
    if user_id in lesson_check_tasks:
        task = lesson_check_tasks[user_id]
        task.cancel()
        del lesson_check_tasks[user_id]
        logger.info(f"Остановлена задача проверки расписания уроков для пользователя {user_id}.")


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
            # Получаем максимальный id из таблицы, если таблица пуста — ставим 1000
            cursor = await conn.execute("SELECT MAX(id) FROM courses")
            row = await cursor.fetchone()
            max_id = row[0] if row[0] is not None else 999  # если таблица пуста, начнем с 1000
            new_id = max_id + 1

            await conn.execute("""
                INSERT OR REPLACE INTO courses (id, course_id, group_id, title, description)
                VALUES (?, ?, ?, ?, ?)
            """, (new_id, course_id, group_id, f"{course_id} basic", f"Описание для {course_id}"))
            logger.info(
                f"Добавлена запись в process_add_course_to_db: {new_id=}, {course_id=}, {group_id=}")

            # Обработка кодов активации
            for code in [code1, code2, code3]:
                code_info = settings["activation_codes"].get(code)
                if code_info:
                    await conn.execute("""
                        INSERT OR IGNORE INTO course_activation_codes 
                        (code_word, course_id, version_id, price_rub)
                        VALUES (?, ?, ?, ?)
                    """, (
                        code,
                        code_info["course"],
                        code_info["version"],
                        code_info["price"]
                    ))

            await conn.commit()
            logger.info(f"Курс {course_id} успешно добавлен в базу данных.")

            await update_settings_file()  # Обновляем файл settings.json
            await backup_settings_file()  # Создаем бэкап файла settings.json

    except Exception as e:
        logger.error(f"Ошибка при добавлении курса {course_id} в базу данных: {e}")


async def backup_settings_file():
    """Создает бэкап файла settings.json."""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_file = f"settings_{timestamp}.json"
        shutil.copy("settings.json", backup_file)
        logger.info(f"Создан бэкап файла settings.json: {backup_file}")

    except Exception as e:
        logger.error(f"Ошибка при создании бэкапа файла settings.json: {e}")


@db_exception_handler
async def init_db():
    """Инициализирует базу данных, создавая необходимые таблицы, если они еще не существуют.

        Функция предполагает следующую структуру данных:
        - users: Содержит информацию о пользователях бота (user_id, username, first_name, last_name).
        - courses: Хранит данные о курсах (course_id, group_id, title, description).
        - homework_gallery: Содержит информацию о домашних заданиях, отправленных пользователями (user_id, course_id, lesson_num, message_id, approved_by).
        - admin_context: Используется для хранения контекстных данных администраторов (admin_id, context_data).
        - user_states: Хранит состояние пользователя, включая ID текущего курса (user_id, current_course_id).
        - course_versions: Содержит информацию о версиях курсов (тарифы) (course_id, version_id, title, price, activation_code, description).
        - user_courses: Связывает пользователей с курсами и хранит их прогресс (user_id, course_id, version_id, status, current_lesson, last_lesson_sent_time, is_completed, activation_date).
        - group_messages: Хранит сообщения из групп, используемые в уроках (group_id, lesson_num, course_id, content_type, is_homework, text, file_id).
        - course_activation_codes: Содержит коды активации для курсов (code_word, course_id, version_id, price_rub).
        """
    logger.info(f"Initializing database...")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Создаем таблицу users
            await conn.execute("PRAGMA journal_mode = WAL")
            await conn.execute("PRAGMA busy_timeout = 300")  #
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

            await conn.execute('''
                CREATE TABLE IF NOT EXISTS courses (
                    course_id TEXT PRIMARY KEY,
                    id INTEGER,
                    group_id TEXT,
                    title TEXT NOT NULL COLLATE NOCASE,
                    message_interval REAL NOT NULL DEFAULT 24,
                    description TEXT COLLATE NOCASE,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            await conn.commit()

            # # 09-04 perplexity - галерея домашек
            await conn.execute('''
            CREATE TABLE IF NOT EXISTS homework_gallery (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                course_id TEXT NOT NULL,
                lesson_num INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                approved_by INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (course_id) REFERENCES courses(course_id)
            )
             ''')

            # 09-04 perplexity - для домашек
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS admin_context(
                    user_id INTEGER PRIMARY KEY,
                    course_id TEXT NOT NULL,    
                    lesson_num INTEGER NOT NULL, 
                    text TEXT
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
                    status TEXT DEFAULT 'active',
                    hw_status TEXT DEFAULT 'none',
                    current_lesson INTEGER DEFAULT 0,
                    first_lesson_sent_time DATETIME,
                    last_lesson_sent_time DATETIME,
                    is_completed INTEGER DEFAULT 0,
                    last_menu_message_id INTEGER,
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
                    hw_type TEXT,
                    text TEXT,
                    file_id TEXT,
                    level integer DEFAULT 1,
                    message_id INTEGER NOT NULL,
                    is_forwarded BOOLEAN DEFAULT FALSE,
                    forwarded_from_chat_id INTEGER,
                    forwarded_message_id INTEGER,
                    snippet TEXT COLLATE NOCASE, -- Сниппет урока todo: 
                    is_bouns BOOLEAN DEFAULT FALSE,
                    open_time DATETIME,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (course_id) REFERENCES courses(course_id)
                )
            ''')


            # Создаем таблицу activation_codes
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS course_activation_codes (
                    code_word TEXT PRIMARY KEY,
                    course_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
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


@db_exception_handler
async def send_lesson_to_user(user_id: int, course_id: str, lesson_num: int):
    """Отправляет урок, обновляет время отправки и обрабатывает ДЗ. """
    logger.info(f"🚀 send_lesson_to_user: user_id={user_id}, course_id={course_id}, lesson_num={lesson_num}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:  # Добавляем подключение к БД

            # Шаг 1: Ищем контент урока (текст, видео, фото)
            cursor = await conn.execute("""
                SELECT text, content_type, file_id, is_homework, hw_type
                FROM group_messages
                WHERE course_id = ? AND lesson_num = ?
                ORDER BY id
            """, (course_id, lesson_num))
            lesson_content = await cursor.fetchall()

            # Count total lessons
            cursor = await conn.execute("""
                        SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ?
                    """, (course_id,))
            total_lessons = (await cursor.fetchone())[0]

            logger.info(f"58585858585 {len(lesson_content)=} {total_lessons=}")

            if not lesson_content:
                logger.warning(f"⚠️ Lesson {lesson_num} not found for course {course_id}.")

                if lesson_num >= total_lessons:
                    await bot.send_message(user_id, "🎉 Вы прошли все уроки курса!", parse_mode=None)
                    await deactivate_course (user_id, course_id)
                    logger.warning(f"⚠️ закончили курс.")
                else:
                    await bot.send_message(user_id, "Урок не найден.", parse_mode=None)
                return

            # Инициализируем переменные для отслеживания ДЗ
            is_homework_local = False
            hw_type_local = None

            # Шаг 2: Отправляем контент урока пользователю
            k=0
            for text, content_type, file_id, is_homework, hw_type in lesson_content:
                k+=1
                if text:
                    text = escape_md(text)
                else:
                    text = ""  # Защита от None
                # Проверяем, что текст не пустой для текстовых сообщений
                if content_type == "text":
                    if not text:
                        logger.error(f"Пустой текст в уроке номер материала {k} {course_id=} {lesson_num=}")
                        continue  # Пропускаем отправку

                if content_type == "text":
                    await bot.send_message(user_id, text, parse_mode=None)  # Correct usage for MarkdownV2

                elif content_type == "photo":
                    await bot.send_photo(user_id, file_id, caption=text, parse_mode=None)  # Correct usage for MarkdownV2
                elif content_type == "video":
                    await bot.send_video(user_id, file_id, caption=text, parse_mode=None)  # Correct usage for MarkdownV2
                elif content_type == "document":
                    await bot.send_document(user_id, file_id, caption=text, parse_mode=None)  # Correct usage for MarkdownV2

                # Проверяем, является ли урок домашним заданием
                if is_homework:
                    logger.info(f"💡 Homework found. Type: {hw_type}")
                    is_homework_local = True
                    hw_type_local = hw_type


            # Шаг 3: Определяем статус ДЗ для обновления
            hw_status = 'pending' if is_homework_local else 'not_required'

            # Шаг 4: Обновляем время отправки урока, САМ УРОК и статус ДЗ
            now = datetime.now()
            now_str = now.strftime('%Y-%m-%d %H:%M:%S')

            try:
                await conn.execute("""
                    UPDATE user_courses
                    SET last_lesson_sent_time = ?, hw_status = ?, current_lesson = ?
                    WHERE user_id = ? AND course_id = ?
                """, (now_str, hw_status, lesson_num , user_id, course_id, ))
                await conn.commit()
                logger.info(f"✅ Updated last_lesson_sent_time and set hw_status to {hw_status} for user {user_id}.")
            except Exception as e:
                logger.error(f"❌ Error updating database: {e}")

            # Шаг 5: Отправляем пояснительное сообщение и меню
            if is_homework_local:
                message_text = escape_md(f"📝 Отлично! Урок отправлен. Ожидаем ваше домашнее задание типа '{hw_type_local}'.")
            else:
                message_text = escape_md("✅ Урок отправлен. Домашнее задание не требуется.")

            # Get user data
            cursor = await conn.execute("""
                SELECT version_id
                FROM user_courses
                WHERE user_id = ? AND course_id = ? AND status = 'active'
            """, (user_id, course_id))
            version_id = (await cursor.fetchone())[0]  # Fixed code

            course_numeric_id = await get_course_id_int(course_id)

            keyboard = get_main_menu_inline_keyboard(  # Corrected usage
                course_numeric_id=course_numeric_id,
                lesson_num=lesson_num,
                user_tariff=version_id,
            )

            await bot.send_message(user_id, message_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2)  # Enable MarkdownV2

        logger.info(f"✅ Lesson sent successfully to {user_id}.")

    except Exception as e:
        logger.error(f"💥 General error in send_lesson_to_user: {e}", exc_info=True)
        await bot.send_message(user_id, "📛 Error sending lesson. We're fixing the robot!", parse_mode=None)


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




async def get_next_lesson_time(user_id: int, course_id: str) -> str:
    """Получает время следующего урока для пользователя."""
    logger.info(f"in🚀 get_next_lesson_time: user_id={user_id}, course_id={course_id}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем время последнего отправленного урока
            cursor = await conn.execute("""
                SELECT last_lesson_sent_time
                FROM user_courses
                WHERE user_id = ? AND course_id = ? AND status = 'active'
            """, (user_id, course_id))
            result = await cursor.fetchone()

            if not result or not result[0]:
                return "в ближайшее время (как только появится в расписании)"

            last_lesson_sent_time_str = result[0]
            last_lesson_sent_time = datetime.fromisoformat(last_lesson_sent_time_str)

            # Получаем интервал отправки уроков из настроек
            lesson_interval_hours = settings.get("message_interval", 24)
            # Рассчитываем время следующего урока
            next_lesson_time = last_lesson_sent_time + timedelta(hours=lesson_interval_hours)

            # Форматируем время для отображения пользователю
            formatted_time = next_lesson_time.strftime("%d.%m.%Y в %H:%M")

            return formatted_time

    except Exception as e:
        logger.error(f"Ошибка при получении времени следующего урока: {e}", exc_info=True)
        return "в ближайшее время (произошла ошибка при расчёте)"


@dp.callback_query(F.data == "menu_support")
async def cmd_support_callback(query: types.CallbackQuery):
    """Обработчик для кнопки 'Поддержка'."""
    global user_support_state
    user_id = query.from_user.id
    chat_id = query.message.chat.id
    message_id = query.message.message_id
    logger.info("10 1 cmd_support_callback  {user_id=}  {chat_id=}  {message_id=} ")
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
    if ADMIN_GROUP_ID:
        await bot.forward_message(chat_id=ADMIN_GROUP_ID, from_chat_id=chat_id, message_id=query.message.message_id)

        # Сообщение админу
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Вопрос от пользователя {query.from_user.full_name} (ID: {user_id}). Ответьте на это сообщение, чтобы пользователь получил ваш ответ.",
            reply_to_message_id=query.message.message_id, parse_mode=None)

        # Сообщение пользователю
        await query.message.edit_text(
            "Ваш запрос отправлен в поддержку. Ожидайте ответа.",
        )
    else:
        await query.message.edit_text("Не удалось отправить запрос в поддержку. Обратитесь к администратору.")


def get_main_menu_inline_keyboard(
    course_numeric_id: int,
    lesson_num: int,
    user_tariff: str,
    homework_pending: bool = False,
    courses_button_text: str = "📚 Мои курсы"  # Новый параметр с значением по умолчанию
) -> InlineKeyboardMarkup:
    """
    Создает inline-клавиатуру для основного меню с динамическим текстом кнопки курсов.

    Args:
        course_numeric_id course_id: ID курса - теперь числовой course_numeric_id
        lesson_num: Номер текущего урока
        user_tariff: Тариф пользователя (v1/v2/v3)
        homework_pending: Флаг наличия ДЗ для проверки
        courses_button_text: Текст кнопки "Мои курсы" с количеством (по умолчанию "📚 Мои курсы")

    Returns:
        InlineKeyboardMarkup: Клавиатура с динамическими кнопками
    """
    builder = InlineKeyboardBuilder()

    # Основная кнопка текущего урока
    builder.row(
        InlineKeyboardButton(
            text="📚 Текущий урок (прислать повторно)",
            callback_data=CourseCallback(
                action="menu_cur",
                course_id=course_numeric_id,
                lesson_num=lesson_num
            ).pack()
        )
    )

    # Кнопка самоодобрения для тарифа v1
    if user_tariff == "v1" and homework_pending:
        builder.row(
            InlineKeyboardButton(
                text="✅ СамоОдобрить ДЗ",
                callback_data=CourseCallback(
                    action="self_approve_hw",
                    course_id=course_numeric_id,
                    lesson_num=lesson_num
                ).pack()
            )
        )

    # Дополнительные кнопки с динамическим текстом
    builder.row(
        InlineKeyboardButton(text=courses_button_text, callback_data="menu_mycourses"),
        InlineKeyboardButton(text="📈 Прогресс", callback_data="menu_progress"),
        InlineKeyboardButton(text="📞 Поддержка", callback_data="menu_support")
    )

    return builder.as_markup()


# ============= для взаимодействия с группами уроков. работает при добавлении материала в группу ===========

@db_exception_handler
async def save_message_to_db(group_id: int, message: Message):
    """Сохранение сообщения в базу данных с валидацией текста."""
    global lesson_stack, last_message_info
    group_id = str(message.chat.id)
    mes_id = message.message_id
    logger.info(f"Saving message {mes_id=} from group {group_id=}")

    # Шаг 1: Определение course_id для данного group_id
    logger.info(f"777 ищем course_id для group_id {group_id}.")
    course_id = next(
        (course for g, course in settings["groups"].items() if g == str(group_id)),
        None
    )

    if not course_id:
        logger.warning(f"777 Не найден course_id для group_id {group_id}.")
        return
    logger.info(f"777 это {course_id=}.")

    # Определение типа контента и извлечение file_id
    text = message.text or ""
    user_id = message.from_user.id if message.from_user else None
    file_id = message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else None)
    logger.info(f"777!!! это {user_id=} {file_id=} {course_id=}")
    # Extract lesson markers
    start_lesson_match = re.search(r"\*START_LESSON (\d+)", text)
    end_lesson_match = re.search(r"\*END_LESSON (\d+)", text)
    hw_start_match = re.search(r"\*HW_START", text)
    hw_end_match = re.search(r"\*HW_END", text)
    course_end_match = re.search(r"\*COURSE_END", text)
    hw_type_match = re.search(r"\*HW_TYPE\s*(\w+)", text)

    lesson_num = None
    is_homework = False
    hw_type = 'none'  # Значение по умолчанию

    if hw_type_match:
        hw_type = hw_type_match.group(1).lower()  # Получаем тип ДЗ и приводим к нижнему регистру
        logger.info(f"Обнаружен тип домашнего задания: {hw_type}")

    # Remove tags from the text
    cleaned_text = re.sub(r"\*START_LESSON (\d+)", "", text)
    cleaned_text = re.sub(r"\*END_LESSON (\d+)", "", cleaned_text)
    cleaned_text = re.sub(r"\*HW_START", "", cleaned_text)
    cleaned_text = re.sub(r"\*HW_END", "", cleaned_text)
    cleaned_text = re.sub(r"\*COURSE_END", "", cleaned_text)
    cleaned_text = re.sub(r"\*HW_TYPE\s*(\w+)", "", cleaned_text)

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
            else:  # Если нет открытого урока, берем номер из последнего сообщения
                lesson_num = last_message_info.get(group_id, {}).get("lesson_num")
                logger.warning(f"HW_START Using last known lesson: {lesson_num}... without active lesson in group {group_id}.")
            is_homework = True
        elif hw_end_match:
            # Ignore end markers, we only care about start markers
            pass
        elif course_end_match:
            # Course end processing
            await process_course_completion(int(group_id), conn)
            logger.info(f"Course ended in group {group_id}. Statistics processed.")
            return  # Stop further processing of this message

        # If there are active lessons, take the latest
        if group_id in lesson_stack and lesson_stack[group_id]:
            lesson_num = lesson_stack[group_id][-1]

        # Extract course information from the first message
        course_snippet = None

        # Extract course information from the first message
        if cleaned_text.startswith("*Курс"):
            course_snippet = extract_course_snippet(cleaned_text)
            course_title = extract_course_title(cleaned_text)
            # Update course title and snippet
            await conn.execute("""
                UPDATE courses
                SET title = ?, description = ?
                WHERE course_id = ?
            """, (course_title, course_snippet, group_id))
            await conn.commit()
            logger.info(f"Upd {group_id} type {message.content_type}")

        # Определите тип контента
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
            text = message.caption  # Подпись к фото
        elif message.content_type == "video":
            file_id = message.video.file_id
            text = message.caption  # Подпись к видео
        elif message.content_type == "document":
            file_id = message.document.file_id
            text = message.caption  # Подпись к документу
        elif message.content_type == "audio" and message.audio:
            file_id = message.audio.file_id
        else:
            file_id = None
            text = cleaned_text  # Обычный текст

        # Валидация: текст не может быть пустым для текстовых сообщений
        if message.content_type == "text" and not text.strip():
            logger.warning(f"Текст не может быть пустым для текстовых сообщений. Пропускаем сохранение.")
            return  # Прерываем сохранение

        logger.info(f"13 {file_id=} {hw_type=}")
        # Save the message to the database
        await conn.execute("""
            INSERT INTO group_messages (
                group_id, message_id, content_type, text, file_id,
                is_forwarded, forwarded_from_chat_id, forwarded_message_id,
                course_id, lesson_num, is_homework, hw_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            group_id, message.message_id, message.content_type, text,
            file_id, message.forward_origin is not None, message.forward_origin.chat.id if message.forward_origin else None,
            message.forward_origin.message_id if message.forward_origin else None,
            course_id, lesson_num, is_homework, hw_type
        ))
        await conn.commit()

        # Обновляем информацию о последнем сообщении
        last_message_info[group_id] = {"lesson_num": lesson_num}
        logger.info(f"last_message_info {group_id=} = {lesson_num=}")

        logger.info(
            f"Сообщение сохранено: {group_id=}, {lesson_num=}, {course_id=}, {message.content_type=}, {is_homework=}, {text=}, {file_id=}"
        )

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
            await bot.send_message(chat_id=ADMIN_GROUP_ID, text=f"Случайный урок курса {course_name} ({course_id}), урок {lesson_num}:\n{lesson_text}", parse_mode=None)
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
            await bot.send_message(chat_id=ADMIN_GROUP_ID, text=stats_message, parse_mode=None)
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
    """Импортирует курсы и коды активации в БД"""
    logger.info("Starting settings import...")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем максимальный id на старте (если таблица пустая — начнем с 999)
            cursor = await conn.execute("SELECT MAX(id) FROM courses")
            row = await cursor.fetchone()
            max_id = row[0] if row[0] is not None else 0

            # Добавляем курсы
            for group_id, course_id in settings["groups"].items():
                max_id += 1  # Увеличиваем id для каждого нового курса
                await conn.execute("""
                    INSERT OR IGNORE INTO courses 
                    (id, course_id, group_id, title, message_interval) 
                    VALUES (?, ?, ?, ?, ?)
                """, (max_id, course_id, group_id, course_id, settings.get("message_interval", 24)))
                logger.info(f"Added course: {course_id} с id={max_id}")

            # Добавляем коды активации
            for code, code_info in settings["activation_codes"].items():
                await conn.execute("""
                    INSERT OR IGNORE INTO course_activation_codes 
                    (code_word, course_id, version_id, price_rub)
                    VALUES (?, ?, ?, ?)
                """, (
                    code,
                    code_info["course"],
                    code_info["version"],
                    code_info["price"]
                ))
                #logger.info(f"Added code: {code}")

            await conn.commit()
            logger.info("Settings imported successfully")

    except Exception as e:
        logger.error(f"Import error: {str(e)}")
        raise



# 14-04 - Проверка доступа в группах
async def check_groups_access(bot: Bot, raw_id: int, gr_name:str):
    """Проверяет доступ бота в указанной группе и возвращает отчет."""
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
    logger.info(f"222 {len(settings)=}")
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
        await bot.send_message(admin_group_id, message_text, parse_mode=ParseMode.MARKDOWN_V2)
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
    """Обрабатывает сообщения из группы."""
    logger.info(f"COURSE_GROUPS ПРИШЛО в {message.chat.id}, mes_id={message.message_id} {COURSE_GROUPS}")

    if message.chat.type == "private":
        logger.warning(f"!!приватное: {message.chat.id}, message_id={message.message_id}")
        await message.answer("Приватные сообщения не обрабатываются.", parse_mode=None)
        return

    await save_message_to_db(message.chat.id, message)


# Админские команды
#=======================================================================================================================
# Admin command to reply to user



@dp.callback_query(F.chat.id == ADMIN_GROUP_ID,lambda c: c.data in ["export_db", "import_db"])
async def handle_admin_actions(callback: CallbackQuery):
    if callback.data == "export_db":
        await export_db(callback.message)
    elif callback.data == "import_db":
        await import_db(callback.message)

@dp.message(Command("export_db"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def export_db(message: types.Message):  # types.Message instead of Message
    """Экспорт данных из базы данных в JSON-файл. Только для администраторов."""
    logger.info("3 Получена команда /export_db")

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
        await message.answer("❌ Произошла ошибка при экспорте базы данных.", parse_mode=None)

@dp.message(Command("import_db"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def import_db(message: types.Message):  # types.Message instead of Message
    """Импорт данных из JSON-файла в базу данных. Только для администраторов."""
    logger.info("4 Получена команда /import_db")

    if not message.document:
        await message.answer("❌ Пожалуйста, отправьте JSON-файл с данными.", parse_mode=None)
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

        await message.answer("✅ База данных успешно импортирована из JSON.", parse_mode=None)
        logger.info("База данных успешно импортирована.")
    except Exception as e:
        logger.error(f"Ошибка при импорте базы данных: {e}")
        await message.answer("❌ Произошла ошибка при импорте базы данных.", parse_mode=None)




async def update_settings_file():
    """Обновляет файл settings.json с информацией о курсах."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("SELECT course_id, group_id FROM courses")
            courses = await cursor.fetchall()

            settings = {
                "message_interval": 24,
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


@dp.message(F.text, F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def process_rejection_reason(message: Message):
    """Обрабатывает сообщение с причиной отклонения домашнего задания от админа."""
    admin_id = message.from_user.id
    logger.info(f"5557 Process request from admin to reject homework: {admin_id} ")
    rejection_reason = message.text

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Получаем user_id, course_id, lesson_num из admin_context
            context_cursor = await conn.execute("""
                SELECT user_id, course_id, lesson_num FROM admin_context WHERE user_id = ?
            """, (admin_id,))  # Используем admin_id для поиска
            context_data = await context_cursor.fetchone()

            if not context_data:
                await message.reply("Контекст не найден. Попробуйте отклонить ДЗ заново.", parse_mode=None)
                return

            user_id, course_id, lesson_num = context_data

            # Обновляем homework и устанавливаем статус "rejected"
            await conn.execute("""
                UPDATE homework SET status = 'rejected', admin_id = ?, 
                decision_date = CURRENT_TIMESTAMP, rejection_reason = ?
                WHERE user_id = ? AND course_id = ? AND lesson_num = ?
            """, (admin_id, rejection_reason, user_id, course_id, lesson_num))
            await conn.commit()

            # Уведомляем пользователя об отклонении
            await bot.send_message(
                user_id,
                f"❌ Ваше домашнее задание к уроку {lesson_num} курса '{course_id}' отклонено.\n"
                f"Причина: {rejection_reason}\n\n"
                "Вы можете отправить новое домашнее задание.", parse_mode=None)

            # Удаляем контекст
            await conn.execute("DELETE FROM admin_context WHERE user_id = ?", (admin_id,))  # Используем admin_id для удаления
            await conn.commit()

            await message.reply("Причина отклонения отправлена пользователю.", parse_mode=None)

    except Exception as e:
        logger.error(f"Ошибка в process_rejection_reason: {e}", exc_info=True)
        await message.reply("Произошла ошибка при обработке причины отклонения.", parse_mode=None)




# Команды для взаимодействия с пользователем - в конце, аминь.
#=======================================================================================================================


@db_exception_handler
async def check_homework_pending(user_id: int, course_id: str, lesson_num: int) -> bool:
    """Проверяет, есть ли у пользователя непроверенное ДЗ по данному уроку."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT COUNT(*)
                FROM homework_gallery
                WHERE user_id = ? AND course_id = ? AND lesson_num = ? AND approved_by = 0
            """, (user_id, course_id, lesson_num))
            result = await cursor.fetchone()
            return result[0] > 0  # returns true if homework pending
    except Exception as e:
        logger.error(f"Error while checking homework status: {e}")
        return False


@dp.message(CommandStart())
@db_exception_handler
async def cmd_start(message: types.Message):
    """Обработчик команды /start."""
    user = message.from_user
    user_id = user.id
    first_name = user.first_name or "Пользователь"
    logger.info(f"cmd_start {user_id=}")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверяем, есть ли пользователь в базе данных
            cursor = await conn.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
            user_exists = await cursor.fetchone()
            logger.info(f"cmd_start: user_exists = {user_exists}")

            if not user_exists:
                # Добавляем нового пользователя в базу данных
                await conn.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name)
                    VALUES (?, ?, ?, ?)
                """, (user_id, user.username, user.first_name, user.last_name))
                await conn.commit()
                logger.info(f"New user added: {user_id}")

            # Получаем данные активного курса пользователя из user_courses
            cursor = await conn.execute("""
                SELECT 
                    uc.course_id,
                    uc.current_lesson,
                    uc.version_id,
                    c.title AS course_name,
                    cv.title AS version_name,
                    uc.status,
                    uc.hw_status
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                JOIN course_versions cv ON uc.course_id = cv.course_id AND uc.version_id = cv.version_id
                WHERE uc.user_id = ? AND uc.status = 'active'
            """, (user_id,))
            current_course = await cursor.fetchone()
            logger.info(f"cmd_start: current_course = {current_course}")

            # Если у пользователя нет активного курса, предлагаем активировать
            if not current_course:
                logger.info(f"cmd_start: No active course found for {user_id}, asking for activation code")
                await message.answer(escape_md("❌ Нет активных курсов. Активируйте курс через код"), parse_mode="MarkdownV2")

                try:
                    if not os.path.exists("ask_parol.jpg"):
                        raise FileNotFoundError("Файл ask_parol.jpg не найден")

                    # InputFile должен принимать путь к файлу, а не открытый файл
                    await bot.send_photo(
                        chat_id=user_id,
                        photo=types.FSInputFile("ask_parol.jpg")  # Используем FSInputFile для файловой системы
                    )
                except FileNotFoundError as fnf_error:
                    logger.error(f"Файл не найден: {fnf_error}")
                    await message.answer("⚠️ Произошла ошибка при отправке фотографии.", parse_mode=None)
                except Exception as e:
                    logger.error(f"Ошибка при отправке фото: {e}", exc_info=True)
                    await message.answer("⚠️ Произошла ошибка при отправке фотографии.", parse_mode=None)

                return

            # Распаковываем данные активного курса
            course_id, lesson_num, version_id, course_name, version_name, status, hw_status = current_course
            course_numeric_id = await get_course_id_int(course_id) if course_id else None
            logger.info(
                f"cmd_start: active course - {course_id=}, {lesson_num=}, {version_id=}, {course_name=}, {version_name=}")

            # Получаем статистику по всем курсам пользователя (активные и завершенные)
            cursor = await conn.execute("""
                SELECT 
                    uc.course_id,
                    uc.status
                FROM user_courses uc
                WHERE uc.user_id = ?
            """, (user_id,))
            all_courses = await cursor.fetchall()

            active_courses = [c for c in all_courses if c[1] == 'active']
            completed_courses = [c for c in all_courses if c[1] == 'completed']

            # Получаем общее количество уроков
            cursor = await conn.execute("""
                SELECT 
                    MAX(gm.lesson_num)
                FROM group_messages gm
                WHERE gm.course_id = ?
            """, (course_id,))
            progress_data = await cursor.fetchone()
            total_lessons = progress_data[0] if progress_data and progress_data[
                0] is not None else 1  # Default to 1 if no lessons found

            # Get tariff names from settings
            tariff_names = settings.get("tariff_names", {
                "v1": "Соло",
                "v2": "Группа",
                "v3": "VIP"
            })
            tariff_name = tariff_names.get(version_id, "Базовый")

            # Общее количество курсов для кнопки "Мои курсы"
            total_courses = len(completed_courses) + len(active_courses)
            courses_button_text = f"📚 Мои курсы ({total_courses})"

            logger.info(f"Старт задачи для шедулера для {user_id=}")
            await start_lesson_schedule_task(user_id)
            # Генерация клавиатуры
            # пока выключим - вроде ненад todo разобраться
            #homework_pending = await check_homework_pending(user_id, course_id, lesson_num)
            logger.info(f"перед созданием клавиатуры{course_numeric_id=}")
            keyboard = get_main_menu_inline_keyboard(  # await убрали
                course_numeric_id = course_numeric_id, # закончил тут 16-04 13-11
                lesson_num=lesson_num,
                user_tariff=version_id,
                homework_pending=True if hw_status != 'approved' and hw_status != 'not_required' else False,
                courses_button_text=courses_button_text
            )

            welcome_message = (
                f"*С возвращением*, {escape_md(first_name)}\\!\n\n"
                f"🎓 Курс: {escape_md(course_name)}\n"
                f"🔑 Тариф: {escape_md(tariff_name)}\n"
                f"📚 Текущий урок: {lesson_num}"
            )
            logger.info(f"{welcome_message=}")
            await message.answer(welcome_message, reply_markup=keyboard, parse_mode="MarkdownV2")


    except Exception as e:
        logger.error(f"Error in cmd_start: {e}", exc_info=True)
        await message.answer("Произошла ошибка при обработке команды. Пожалуйста, попробуйте позже.", parse_mode=None)


async def send_course_description(user_id: int, course_id: str):
    """Отправляет описание курса пользователю."""
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT text
                FROM group_messages
                WHERE course_id = ? AND lesson_num = 0
            """, (course_id,))
            description = await cursor.fetchone()

            if description:
                await bot.send_message(user_id, description[0], parse_mode=None)
            else:
                await bot.send_message(user_id, "Описание курса не найдено.", parse_mode=None)

    except Exception as e:
        logger.error(f"Error sending course description: {e}")
        await bot.send_message(user_id, "Ошибка при получении описания курса. Или этот курс секретный и тогда вы знаете что делать!", parse_mode=None)



# help
@dp.message(Command("help"))
async def cmd_help(message: Message):
    logger.info(f"cmd_help  ")
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



# 17-04
@dp.callback_query(F.data == "menu_mycourses") #08-04 Предоставляет кнопки для продолжения или повторного просмотра
@db_exception_handler  # Показывает список активных и завершенных курсов # Разделяет курсы на активные и завершенные
async def cmd_mycourses_callback(query: types.CallbackQuery):
    """Показывает список активных и завершенных курсов."""
    user_id = query.from_user.id
    logger.info("12 cmd_mycourses_callback  {user_id=}   ")
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


# 11-04
@dp.callback_query(CourseCallback.filter(F.action == "menu_cur"))
async def show_lesson_content(callback_query: types.CallbackQuery, callback_data: CourseCallback):
    """Отображает текущий урок с динамическим меню"""
    logger.info("show_lesson_content: Callback получен!")
    user = callback_query.from_user
    user_id = user.id
    first_name = user.first_name or "Пользователь"
    logger.info(f"15 show_lesson_content из menu_current_lesson = menu_cur    {first_name} {user_id} ")
    logger.info(f"666    {callback_data}  ")

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            course_numeric_id = callback_data.course_id
            course_id = await get_course_id_str(course_numeric_id)
            logger.info(f"77 show_lesson_content {course_numeric_id=} {course_id=} ")

            # current_lesson из базы
            cursor = await conn.execute("""
                    SELECT current_lesson 
                    FROM user_courses 
                    WHERE user_id = ? AND course_id = ?
                """, (user_id, course_id))
            current_lesson = (await cursor.fetchone())[0]

        if current_lesson:
            lesson_num = current_lesson
        else:
            logger.error(f"800 Пустой урок: {course_id} урок {current_lesson}")
            lesson_num = 1

        logger.info(f"15 show_lesson_content {course_id=} {lesson_num=} ")
        # Получаем контент урока
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT text, content_type, file_id, is_homework, hw_type
                FROM group_messages 
                WHERE course_id = ? AND lesson_num = ?
            """, (course_id, lesson_num))

            lesson_content = await cursor.fetchall()

        if not lesson_content:
            logger.error(f"900 Пустой уоньТент урока: {course_id} урок {lesson_num}")
            await callback_query.answer("📭 Урок пуст")
            return

        ka="домашки нет"
        # Отправка контента с микрозадержкой
        for text, content_type, file_id, is_homework, hw_type in lesson_content:
            logger.info(f"\nrow: {len(text)=} | {content_type=} | {file_id=} {is_homework=}, {hw_type=}")
            if is_homework:
                if hw_type == "photo":
                    ka= "📸 Отправьте фото для домашнего задания"
                elif hw_type == "text":
                    ka="📝 Отправьте текст для домашнего задания"
                elif hw_type == "video":
                    ka= "📹 Отправьте видео для домашнего задания"
                elif hw_type == "any":
                    ka= "📹 Отправьте для домашнего задания что угодно"
                ka+=". и ждите следующий урок"
            if content_type == "video" and file_id:
                await bot.send_video(user_id, video=file_id, caption=text or None, parse_mode=None)
            elif content_type == "photo" and file_id:
                await bot.send_photo(user_id, photo=file_id, caption=text or None, parse_mode=None)
            elif content_type == "document" and file_id:
                await bot.send_document(user_id, document=file_id, caption=text or None, parse_mode=None)
            elif content_type == "audio" and file_id:
                await bot.send_audio(user_id, audio=file_id, caption=text or None, parse_mode=None)
            elif text:
                await bot.send_message(user_id, text=escape_md(text), parse_mode="MarkdownV2")

        # Обновляем время последнего урока
        now = datetime.now()
        now_str = now.strftime('%Y-%m-%d %H:%M:%S')

        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
               UPDATE user_courses 
               SET last_lesson_sent_time = ? 
               WHERE user_id = ? AND course_id = ?
           """, (now_str, user_id, course_id))  # передаем в строке!
            await conn.commit()



        # Получаем данные активного курса пользователя из user_courses
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT 
                    uc.version_id,
                    c.title AS course_name,
                     uc.current_lesson
                FROM user_courses uc
                JOIN courses c ON uc.course_id = c.course_id
                WHERE uc.user_id = ? AND uc.course_id = ? AND uc.status = 'active'
            """, (user_id, course_id))
            user_data = await cursor.fetchone()

        if not user_data:
            await callback_query.answer("❌ Активный курс не найден")
            return

        version_id, course_name, current_lesson = user_data

        # Формируем текст кнопки с количеством
        total_courses = len(settings["activation_codes"])  # общее кол-во курсов
        courses_button_text = f"📚 Мои курсы ({total_courses})"

        # Получаем тариф
        tariff_names = settings.get("tariff_names", {"v1": "Соло", "v2": "Группа", "v3": "VIP"})
        user_tariff = tariff_names.get(version_id, "Базовый")

        # Формируем прогресс
        async with aiosqlite.connect(DB_FILE) as conn:
            total_lessons_cursor = await conn.execute("""
                SELECT MAX(lesson_num) FROM group_messages WHERE course_id = ?
            """, (course_id,))
            total_lessons = (await total_lessons_cursor.fetchone())[0]

        lesson_progress = (
            f"\n📊 Прогресс: {current_lesson}/{total_lessons} уроков"
            f"\n✅ Последний пройденный: урок {current_lesson}" if current_lesson else ""
        )

        # Генерация клавиатуры
        homework_pending = await check_homework_pending(user_id, course_id, current_lesson)
        keyboard = get_main_menu_inline_keyboard(
            course_numeric_id=course_numeric_id,
            lesson_num=current_lesson,
            user_tariff=version_id,
            homework_pending=homework_pending,
            courses_button_text=courses_button_text
        )
        logger.info(f"15554 запишем в базу  current_lesson{current_lesson}  ")

        if current_lesson == 0:
            current_lesson = 1
            logger.info(f"1554 запишем в базу  current_lesson{1}  ")
            async with aiosqlite.connect(DB_FILE) as conn:
                await conn.execute("""
                    UPDATE user_courses 
                    SET current_lesson = ?
                    WHERE user_id = ? AND course_id = ?
                    """, (1, user_id, course_id))
                await conn.commit()

        message = (
            f"{ka}, {first_name}!\n\n"
            f"🎓 Курс: {course_name}\n"
            f"🔑 Тариф: {user_tariff}\n"
            f"📚 Текущий урок: {current_lesson}"
            f"{lesson_progress}"
        )
        if current_lesson == total_lessons:
            await bot.send_message(user_id, "🎉 Вы прошли все уроки курса!", parse_mode=None)
            await deactivate_course(user_id, course_id)
            logger.warning(f"⚠️ закончили курс.")
            await callback_query.message.delete() # окончание всего курса todo продумывать

        # мистер x пришел 14-04 todo разобраться тут про номер сообщения с менюшкой
        x = await bot.send_message(user_id, escape_md(message), reply_markup=keyboard, parse_mode="MarkdownV2")
        logger.info(f"=============1801 bot.send_message(user_id, message, reply_markup=keyboard)={x}  ")
        # запомним ID сообщения для последующего изменения времени красивого
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                UPDATE user_courses 
                SET last_menu_message_id = ?
                WHERE user_id = ? AND course_id = ?
            """, (x.message_id, user_id, course_id))  # Исправлено здесь: используем x.message_id
            await conn.commit()
        logger.info(f"Сохранен last_menu_message_id={x.message_id} для {user_id=}")
        await callback_query.answer()

    except Exception as e:
        logger.error(f"Ошибка в show_lesson_content: {str(e)}", exc_info=True)
        await callback_query.answer("⚠️ Произошла ошибка. Попробуйте позже")
        await bot.send_message(
            ADMIN_GROUP_ID,
            f"🚨 Ошибка у @{user.username}: {str(e)}", parse_mode=None)


# данные о курсе пользователя courses.id:int, user_courses.current_lesson, user_courses.version_id
async def get_user_course_data(user_id: int) -> tuple:
    """
    Получает данные о курсе пользователя (course_numeric_id, current_lesson, version_id).
    Возвращает None, если нет активного курса.
    """
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
            SELECT courses.id, user_courses.current_lesson, user_courses.version_id 
            FROM user_courses 
            JOIN courses ON user_courses.course_id = courses.course_id
            WHERE user_courses.user_id = ? AND user_courses.status = 'active'
        """, (user_id,))
        user_course_data = await cursor.fetchone()
        logger.info(f"776 {user_course_data=}  ")
        if not user_course_data:
            logger.warning(f"Нет активного курса для пользователя {user_id}")
            return None
    return user_course_data


# 17-04
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



# 14-04 ночью - кнопка самоодобрения
@dp.callback_query(CourseCallback.filter(F.action == "self_approve_hw"))
@db_exception_handler
async def process_self_approve_hw(callback: types.CallbackQuery, callback_data: CourseCallback):
    """Обрабатывает нажатие на кнопку самоодобрения ДЗ."""
    user_id = callback.from_user.id
    course_numeric_id = callback_data.course_id
    course_id = await get_course_id_str(course_numeric_id)

    lesson_num = callback_data.lesson_num

    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # 1.Обновляем статус ДЗ в базе данных
            await conn.execute("""
                UPDATE user_courses 
                SET hw_status = 'approved' 
                WHERE user_id = ? AND course_id = ? AND current_lesson = ?
            """, (user_id, course_id, lesson_num))

            # 2. Добавляем ДЗ в галерею (если еще не добавлено) и отмечаем как self-approved
            await conn.execute("""
                UPDATE homework_gallery
                SET approved_by = ?
                WHERE user_id = ? AND course_id = ? AND lesson_num = ?
            """, (user_id, user_id, course_id, lesson_num))
            await conn.commit()
            # Получаем version_id
            cursor = await conn.execute("""
                SELECT version_id FROM user_courses WHERE user_id = ? AND course_id = ? AND current_lesson = ?
            """, (user_id, course_id, lesson_num))
            version_id = (await cursor.fetchone())[0]

            keyboard = get_main_menu_inline_keyboard(
                course_id=course_id,
                lesson_num=lesson_num,
                user_tariff=version_id,
                homework_pending=False, #disable_button=True
                courses_button_text=f"📚 Мои курсы"
            )
            await callback.message.edit_text(  # TODO: Добавить текст
                text="🎉 ДЗ cамоодобрено! Так держать! 🔥",
                reply_markup=keyboard
            )
            # await callback.answer()  # Обязательно нужно ответить на callback
    except Exception as e:
        logger.error(f"Ошибка при самоодобрении ДЗ: {e}", exc_info=True)
        await callback.answer("⚠️ Произошла ошибка при самоодобрении ДЗ. Попробуйте позже.")




#======================Конец обработчиков слов и хэндлеров кнопок=========================================

async def check_state(message: types.Message, state: FSMContext) -> bool:
    """Проверяет, находится ли пользователь в состоянии Form.feedback"""
    return state and await state.get_state() != Form.feedback


@dp.message(F.text, check_state)
@db_exception_handler
async def handle_text(message: types.Message, state: FSMContext):
    """
    Минималистичный обработчик текста. Проверяет курс и передаёт дальше.
    """
    user_id = message.from_user.id
    text = message.text.strip()
    logger.info(f"handle_text: {text=} {user_id=}")

    if text == "/cancel":
        await message.reply("Действие отменено.", parse_mode=None)
        return

    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute(
            "SELECT course_id FROM user_courses WHERE user_id = ? AND status = 'active'",
            (user_id,))
        active_course = await cursor.fetchone()
        logger.info(f"handle_text: active_course={active_course}")

    if active_course:
        logger.info("handle_text: отправляем в handle_homework")
    return await handle_homework(message)


# ----------------- новый обработчик и текстовой домашки и фото --------------------
@dp.message(F.content_type.in_({'photo', 'document', 'text'}))
@db_exception_handler
async def handle_homework(message: types.Message):
    """Обрабатывает отправку домашних заданий (фото/документы/текст)"""
    user_id = message.from_user.id
    logger.info(f" новый обработчик и текстовой домашки и фото  17-04 {user_id=}")

    # Получаем данные о курсе
    user_course_data = await get_user_course_data(user_id)
    if not user_course_data:
        await message.answer("Не найден ваш активный курс для сдачи ДЗ.")
        return

    course_numeric_id, current_lesson, version_id = user_course_data
    course_id = await get_course_id_str(course_numeric_id)

    # Получаем имя пользователя для отображения в сообщении админам
    user_name = md.quote(message.from_user.full_name)

    # Создаем клавиатуру для админа (ДО формирования сообщения)
    keyboard = create_admin_keyboard(
        user_id=user_id,
        course_id=course_numeric_id,
        lesson_num=current_lesson,
        message_id=message.message_id
    )

    # Формируем сообщение для админа в зависимости от типа контента
    if message.text:
        homework_type = "Текстовая домашка"
        text = message.text.strip()
        file_id = None
        admin_message_content = f"✏️ Текст: {md.quote(text)}"
    elif message.photo:
        homework_type = "Домашка с фото"
        text = message.caption or ""  # Получаем подпись к фото (если есть)
        file_id = message.photo[-1].file_id  # Берем последнее (самое большое) фото
        admin_message_content = f"📸 Фото: {file_id}\n✏️ Описание: {md.quote(text)}"
    elif message.document:
        homework_type = "Домашка с документом"
        text = message.caption or ""  # Получаем подпись к документу (если есть)
        file_id = message.document.file_id
        admin_message_content = f"📎 Документ: {file_id}\n✏️ Описание: {md.quote(text)}"
    else:
        await message.answer("Неподдерживаемый тип контента.")
        return

    # Если тариф v1 → самопроверка
    if version_id == 'v1':
        try:
            await message.answer(md.quote("✅ Домашка принята для самопроверки и будет одобрена автоматически!"), parse_mode=ParseMode.MARKDOWN_V2)
            async with aiosqlite.connect(DB_FILE) as conn:
                await conn.execute("""
                    UPDATE user_courses 
                    SET hw_status = 'approved'
                    WHERE user_id = ? AND course_id = ?
                """, (user_id, course_id))
                await conn.commit()
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения об авто-аппруве: {e}", exc_info=True)
        return

    # Формируем сообщение для админа
    course_title = await get_course_title(course_numeric_id)
    admin_message = (
        f"📝 Новое ДЗ ({homework_type})\n"
        f"👤 Пользователь: {user_name}\n"
        f"📚 Курс: {md.quote(course_title)}\n"
        f"⚡ Тариф: {version_id}\n"
        f"📖 Урок: {current_lesson}\n"
        f"{admin_message_content}"  # Добавляем контент в сообщение
    )

    try:
        # Сохраняем информацию о ДЗ в базе данных (для последующей обработки админами)
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                INSERT OR REPLACE INTO admin_context (user_id, course_id, lesson_num, text)
                VALUES (?, ?, ?, ?)
            """, (user_id, course_numeric_id, current_lesson, f"{user_id}:{course_numeric_id}:{current_lesson}"))
            await conn.commit()

        # Отправляем сообщение админам
        await bot.send_message(
            ADMIN_GROUP_ID,
            admin_message,
            reply_markup=keyboard,
            parse_mode=None  # если нет форматирования, иначе ParseMode.MARKDOWN_V2 если нужно
        )

        # Отправляем сообщение пользователю
        await message.answer(
            md.quote(f"✅ {homework_type} на проверке!"),
            parse_mode=ParseMode.MARKDOWN_V2
        )

    except Exception as e:
        logger.error(f"Ошибка отправки домашки админам: {e}", exc_info=True)



# НАДО 17-04
@db_exception_handler
async def update_homework_status(user_id: int, course_id: str, lesson_num: int, status: str):
    """Updates homework status in the database"""
    try:
        async with aiosqlite.connect(DB_FILE) as db:
            await db.execute(
                "UPDATE user_courses SET hw_status = ? WHERE user_id = ? AND course_id = ? AND current_lesson = ?",
                (status, user_id, course_id, lesson_num),
            )
            await db.commit()
        logger.info(f"Homework status updated for user {user_id}, course {course_id}, lesson {lesson_num} to {status}")
    except Exception as e:
        logger.error(f"Error updating homework status in database: {e}")

# 16-04 ночер сделаем клаву отдельно
def create_admin_keyboard(user_id: int, course_id: int, lesson_num: int, message_id: int) -> InlineKeyboardMarkup:
    """Создает клавиатуру с кнопками принятия/отклонения ДЗ в две строки"""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
                InlineKeyboardButton(
                    text="✅ Принять",
                    callback_data=AdminHomeworkCallback(
                        action="approve_hw",
                        user_id=user_id,
                        course_id=course_id,
                        lesson_num=lesson_num,
                        message_id=message_id
                    ).pack()
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=AdminHomeworkCallback(
                        action="reject_hw",
                        user_id=user_id,
                        course_id=course_id,
                        lesson_num=lesson_num,
                        message_id=message_id
                    ).pack()
                )],
            [
                InlineKeyboardButton(
                    text="✅ Принять и отправить сообщение",
                    callback_data=AdminHomeworkCallback(
                        action="approve_reason",
                        user_id=user_id,
                        course_id=course_id,
                        lesson_num=lesson_num,
                        message_id=message_id
                    ).pack()
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить и обосновать",
                    callback_data=AdminHomeworkCallback(
                        action="reject_reason",
                        user_id=user_id,
                        course_id=course_id,
                        lesson_num=lesson_num,
                        message_id=message_id
                    ).pack()
                ) ]
        ])


# 14-04
@dp.message(F.text, IsAdmin())
async def handle_text_homework(message: types.Message):
    """
    Обработчик текстовых домашек, которые присылают пользователи.
    Работает только для админов (проверяется фильтром IsAdmin).
    """
    user_id = message.from_user.id  # ID пользователя, сдавшего ДЗ
    user_name = md.quote(message.from_user.full_name)  # Имя пользователя (экранируем для Markdown)
    text = message.text.strip()  # Текст домашки
    logger.info(f"66 handle_text_homework text='{text}' user_id={user_id}")  # Логируем

    # Получаем данные о курсе
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("""
                    SELECT courses.id, user_courses.current_lesson, user_courses.version_id 
                    FROM user_courses 
                    JOIN courses ON user_courses.course_id = courses.course_id
                    WHERE user_courses.user_id = ? AND user_courses.status = 'active'
                """, (user_id,))
        user_course_data = await cursor.fetchone()

        if not user_course_data:
            logger.warning(f"Нет активного курса для пользователя {user_id} в handle_text_homework")
            # Можно отправить сообщение пользователю, что нет активного курса
            # await message.answer("Не найден ваш активный курс для сдачи ДЗ.")
            return  # Выходим, если нет активного курса

    course_numeric_id, current_lesson, version_id = user_course_data  # Распаковываем данные
    course_id = await get_course_id_str(course_numeric_id)

    # Если тариф v1 → самопроверка (тут просто ставим отметку, что ДЗ сдано)
    if version_id == 'v1':
        try:
            await message.answer(md.quote("✅ Текстовая домашка принята для самопроверки. и тут же одобрена! уже скоро (завтра) будет новый урок"), parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"❌ Ошибка при отправке сообщения об авто-аппруве: {e}", exc_info=True)
        logger.info(f"отправляем след урок: {course_id=} {current_lesson=} к этому +1 сейчас сделаем. {user_id=}")
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                UPDATE user_courses 
                SET hw_status = 'approved', current_lesson = ?
                WHERE user_id = ? AND course_id = ?
            """, (current_lesson + 1, user_id, course_id))
            await conn.commit() # todo там где одобряем - просто ставим в базу 2 ячейки
       # await send_lesson_to_user(user_id, course_id, current_lesson + 1) - вызовется в таймере
        logger.info(f"600 до встречи в таймере ...отправляем след урок: {course_id=} {current_lesson=} к этому +1 сейчас сделаем. {user_id=}")
        return

    # Если тариф не v1 → отправляем ДЗ админам на проверку

    # Формируем сообщение для админа (MarkdownV2, поэтому экранируем)
    admin_message = (
        f"📝 *Новое ДЗ*\n"
        f"👤 Пользователь: {user_name}\n"
        f"📚 Курс: {md.quote(await get_course_title(course_numeric_id))}\n"  # Получаем название курса и экранируем
        f"⚡ Тариф: {version_id}\n"
        f"📖 Урок: {current_lesson}\n"
        f"✏️ Текст: {md.quote(text)}" # Экранируем текст todo разобраться с экранированием
    )

    try: # Сохраняем информацию о ДЗ в базе данных (для последующей обработки админами)
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                INSERT OR REPLACE INTO admin_context (user_id, course_id, lesson_num, text)
                VALUES (?, ?, ?, ?)
            """, (user_id, course_numeric_id, current_lesson, f"{user_id}:{course_numeric_id}:{current_lesson}"))  # Сохраняем user_id отправителя
            await conn.commit()
        logger.info(f"70 handle_text_homework admin_context записан user_id {user_id=}")

        # Создаем кнопки для админа (принять/отклонить)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="✅ Принять",
                callback_data=AdminHomeworkCallback(
                    action="approve_hw",
                    user_id=user_id,
                    course_id=course_numeric_id,
                    lesson_num=current_lesson,
                    message_id=message.message_id
                ).pack()
            ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=AdminHomeworkCallback(
                        action="reject_hw",
                        user_id=user_id,
                        course_id=course_numeric_id,
                        lesson_num=current_lesson,
                        message_id=message.message_id
                    ).pack()
                )]
        ])

        await bot.send_message(ADMIN_GROUP_ID, admin_message, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2)
        await message.answer(md.quote("✅ Текстовая домашка на проверке!"), parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        logger.error(f"❌ Ошибка отправки домашки админам: {e}", exc_info=True)



async def send_message_to_user(user_id: int, text: str, reply_markup: InlineKeyboardMarkup = None):
    """Утилита для отправки сообщения пользователю."""
    try:
        await bot.send_message(user_id, text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        logger.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения пользователю {user_id}: {e}", exc_info=True)


def get_tariff_name(version_id: str) -> str:
    """Возвращает человекочитаемое название тарифа."""
    TARIFF_NAMES = {
        "v1": "Соло",
        "v2": "Группа",
        "v3": "VIP"
    }
    return TARIFF_NAMES.get(version_id, f"Тариф {version_id}")


# НАДО 17-04
@dp.callback_query(AdminHomeworkCallback.filter(F.action == "approve_hw"))
async def approve_homework(callback_query: types.CallbackQuery, callback_data: AdminHomeworkCallback):
    """Approve a homework"""
    logger.info(f"16 тут approve_homework {callback_data=}")
    user_id = callback_data.user_id
    course_numeric_id = callback_data.course_id  # Теперь это числовой ID
    lesson_num = callback_data.lesson_num
    message_id = callback_data.message_id
    course_name = callback_data.course_id  # Оккам посоветовал
    course_name = await get_course_id_str(callback_data.course_id)
    logger.info(f"553 {course_name=} {course_numeric_id=} {message_id=}")

    try:
        # Обновляем статус ДЗ в базе данных
        await update_homework_status(user_id, course_name, lesson_num, "approved")

        # Получаем время следующего урока
        next_lesson_time = await get_next_lesson_time(user_id, course_name)
        logger.info(f"555 {next_lesson_time=}")
        # Формируем текст для сообщения об одобрении ДЗ
        approved_text = md.quote(f"Ваше домашнее задание по курсу {course_name}, урок {lesson_num} одобрено! ")

        # Получаем имя пользователя и формируем приветственный текст
        user = callback_query.from_user
        welcome_text = md.quote(f"С возвращением, {user.first_name or user.username}!")

        # Получаем название тарифа
        async with aiosqlite.connect(DB_FILE) as conn:  # 19-04
            version_id = await conn.execute(  # 19-04
                "SELECT version_id FROM user_courses WHERE user_id = ? AND course_id = ?",
                (user_id, course_name))
            version_id = (await version_id.fetchone())[0]

        tariff_name = get_tariff_name(version_id)

        # Формируем текст для меню
        menu_text = (
            f"{welcome_text}\n\n"
            f"🎓 Курс: {md.quote(course_name)}\n"
            f"🔑 Тариф: {md.quote(tariff_name)}\n"
            f"📚 Текущий урок: {lesson_num}\n"
            f"⏳ Следующий урок: {md.quote(next_lesson_time)}"  # добавлено время
        )

        # Получаем клавиатуру для меню
        keyboard = get_main_menu_inline_keyboard(course_numeric_id, lesson_num, version_id)  # 19-04

        # Отправляем сообщение пользователю с текстом об одобрении ДЗ и меню
        await bot.send_message(
            chat_id=user_id,
            text=approved_text + "\n\n" + menu_text,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN_V2  # Для форматирования menu_text
        )

        # Получаем имя админа, который нажал кнопку
        admin_name = callback_query.from_user.first_name or callback_query.from_user.username or "Администратор"

        # Отправляем новое сообщение в группу админов вместо редактирования старого
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"ДЗ от {user.first_name or user.username} ({user_id}) по курсу {course_name}, урок {lesson_num} одобрено администратором {admin_name}.",
            parse_mode=None
        )

        # Удаляем старое сообщение (опционально)
        await bot.delete_message(chat_id=ADMIN_GROUP_ID, message_id=callback_query.message.message_id)

        await callback_query.answer()

    except Exception as e:  # 18-04
        logger.error(f"❌ Ошибка в функции approve_homework: {e}", exc_info=True)  # 18-04


# НАДО 17-04
@dp.callback_query(AdminHomeworkCallback.filter(F.action == "reject_hw"))
async def reject_homework(callback_query: types.CallbackQuery, callback_data: AdminHomeworkCallback):
    """Reject a homework"""
    logger.info(f"16 тут reject_homework {callback_data=}")
    user_id = callback_data.user_id
    course_numeric_id = callback_data.course_id
    lesson_num = callback_data.lesson_num
    message_id = callback_data.message_id
    course_name = callback_data.course_id  # Оккам посоветовал
    course_name = await get_course_id_str(callback_data.course_id)

    logger.info(f"555 course_name={course_name}")


    try:
        # Обновляем статус ДЗ в базе данных
        await update_homework_status(user_id, course_name, lesson_num, "rejected")

        # Отправляем сообщение пользователю об отклонении ДЗ
        await bot.send_message(
            chat_id=user_id,
            text=f"К сожалению, ваше домашнее задание по курсу {course_name}, урок {lesson_num} отклонено. Пожалуйста, переделайте его и отправьте снова.",
            parse_mode = None
        )

        # Получаем имя админа, который нажал кнопку
        admin_name = callback_query.from_user.first_name or callback_query.from_user.username or "Администратор"

        # Отправляем новое сообщение в группу админов вместо редактирования старого
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Домашнее задание от пользователя {user_id} по курсу {course_name}, урок {lesson_num} отклонено администратором {admin_name}.",
            parse_mode=None
        )

        # Удаляем старое сообщение (опционально)
        await bot.delete_message(chat_id=ADMIN_GROUP_ID, message_id=callback_query.message.message_id)

        await callback_query.answer()

    except Exception as e:  # 18-04
        logger.error(f"❌ Ошибка в функции reject_homework: {e}", exc_info=True)  # 18-04


@dp.callback_query(ApproveFinalCallback.filter())
async def approve_final(callback_query: types.CallbackQuery, callback_data: ApproveFinalCallback):
    """Final approve"""
    try:
        user_id = callback_data.user_id
        course_numeric_id = callback_data.course_id
        course_id = await get_course_id_str(course_numeric_id)

        lesson_num = callback_data.lesson_num
        message_id = callback_data.message_id

        message_to_user = escape_md(f"✅ Следующий урок {course_id} — {lesson_num}!")  # Sanitize info

        await bot.edit_message_reply_markup(chat_id=ADMIN_GROUP_ID, message_id=message_id, reply_markup=None)

        # Send results
        logger.info(f" before DB write")

        # Grab user object
        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                UPDATE user_courses 
                SET hw_status = 'approved'
                WHERE user_id = ? AND course_id = ? AND current_lesson= ?
            """, (user_id, course_id, lesson_num))
            await conn.commit()
        logger.info(f" забубенили {user_id=} {course_id=} {lesson_num=} ")
        await send_message_to_user(str(user_id),message_to_user)

        #await check_lesson_schedule(int(user_id)) #отправляем следующий урок

        await callback_query.answer() #close query
    except Exception as e:
        logger.error(f"❌ Ошибка при изменение статуса домашки: {e}")


@dp.callback_query(RejectFinalCallback.filter())
async def reject_final(callback_query: types.CallbackQuery, callback_data: RejectFinalCallback):
    """Final reject"""
    try:
        user_id = callback_data.user_id
        course_numeric_id = callback_data.course_id
        course_id = await get_course_id_str(course_numeric_id)

        lesson_num = callback_data.lesson_num
        message_id = callback_data.message_id
        admin_id = callback_data.admin_id

        message_to_user = f"❌ Your homework for course *{course_id}*, lesson {lesson_num} has been rejected\\!"  # 17-04
        await bot.edit_message_reply_markup(chat_id=ADMIN_GROUP_ID, message_id=message_id, reply_markup=None)  # 17-04

        async with aiosqlite.connect(DB_FILE) as conn:
            await conn.execute("""
                UPDATE user_courses 
                SET hw_status = 'rejected'
                WHERE user_id = ? AND course_id = ? AND current_lesson= ?
            """, (user_id, course_id, lesson_num))
            await conn.commit()

             # Удаляем контекст из admin_context
            await conn.execute("DELETE FROM admin_context WHERE user_id = ?", (admin_id,))  # Опять Admin_id!
            await conn.commit()

        await send_message_to_user(int(user_id), message_to_user)  # 17-04 int()
        await callback_query.answer()  # close query
    except Exception as e:
        logger.error(f"❌ Ошибка при изменение статуса домашки: {e}")

@dp.callback_query(ApproveWithFeedbackCallback.filter())
async def approve_with_feedback(callback_query: types.CallbackQuery, callback_data: ApproveWithFeedbackCallback, state: FSMContext):
    """Approve with feedback - get text from admin"""
    try:
        user_id = callback_data.user_id
        course_numeric_id = callback_data.course_id
        course_id = await get_course_id_str(course_numeric_id)
        lesson_num = callback_data.lesson_num
        message_id = callback_data.message_id
        logger.info(f" approve_with_feedback {user_id=} {course_id=} {lesson_num=} {message_id=} ")
        await callback_query.answer()
        await state.update_data(
            user_id=user_id,
            course_id=course_id,
            lesson_num=lesson_num,
            message_id=message_id,
            action="approve",
            admin_id=callback_query.from_user.id  # ID админа
        )
        await bot.edit_message_text(chat_id=ADMIN_GROUP_ID, message_id=callback_query.message.message_id, text="Ожидаю сообщение от администратора")
        await state.set_state(Form.feedback)  # переключаем в режим ожидания фидбека

    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении данных для фидбека: {e}")

@dp.callback_query(RejectWithFeedbackCallback.filter())
async def reject_with_feedback(callback_query: types.CallbackQuery, callback_data: RejectWithFeedbackCallback, state: FSMContext):
    """Reject with feedback - get text from admin"""
    try:
        user_id = callback_data.user_id
        course_numeric_id = callback_data.course_id
        course_id = await get_course_id_str(course_numeric_id)
        lesson_num = callback_data.lesson_num
        message_id = callback_data.message_id
        logger.info(f" reject_with_feedback {user_id=} {course_id=} {lesson_num=} {message_id=} ")
        await callback_query.answer()
        await state.update_data(
            user_id=user_id,
            course_id=course_id,
            lesson_num=lesson_num,
            message_id=message_id,
            action="reject",
            admin_id=callback_query.from_user.id  # ID админа
        )
        await bot.edit_message_text(chat_id=ADMIN_GROUP_ID, message_id=callback_query.message.message_id, text="Ожидаю сообщение с причиной отказа от администратора")
        await state.set_state(Form.feedback)  # переключаем в режим ожидания фидбека

    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении данных для фидбека: {e}")

# Обработчик получения текста причины отказа и завершения процесса
@dp.message(Form.feedback)
async def process_rejection_feedback(message: types.Message, state: FSMContext):
    """Process feedback from admin and finalize rejection"""
    try:
        feedback_text = message.text
        data = await state.get_data()
        user_id = data.get("user_id")
        course_id = data.get("course_id")
        lesson_num = data.get("lesson_num")
        message_id = data.get("message_id")
        admin_id = data.get("admin_id")  # Получаем ID админа

        admin_name = message.from_user.first_name or message.from_user.username or "Администратор"

        # Обновляем статус ДЗ в базе данных
        await update_homework_status(user_id, course_id, lesson_num, "rejected")

        # Отправляем сообщение пользователю с причиной отказа
        await bot.send_message(
            chat_id=user_id,
            text=f"К сожалению, ваше домашнее задание по курсу {course_id}, урок {lesson_num} отклонено.\n\nПричина: {feedback_text}\n\nПожалуйста, исправьте ошибки и отправьте задание на проверку снова."
        )

        # Отправляем сообщение в группу админов
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=f"Домашнее задание от пользователя {user_id} по курсу {course_id}, урок {lesson_num} отклонено администратором {admin_name} с причиной: {feedback_text}"
        )

        # Удаляем клавиатуру из исходного сообщения в группе админов
        await bot.edit_message_reply_markup(chat_id=ADMIN_GROUP_ID, message_id=message_id, reply_markup=None)

        # Удаляем сообщение с причиной отказа от админа
        await bot.delete_message(chat_id=ADMIN_GROUP_ID, message_id=message.message_id)

        await state.clear()  # clear FSM

        logger.info(f"ДЗ для user_id={user_id}, course_id={course_id}, lesson_num={lesson_num} отклонено с причиной.")
    except Exception as e:
        logger.error(f"❌ Ошибка при обработке причины отказа и отклонении ДЗ: {e}")


# Обработчик последний - чтобы не мешал другим обработчикам работать. Порядок имеет значение
@dp.message(F.text)  # Фильтр только для текстовых сообщений
async def handle_activation_code(message: types.Message): # handle_activation_code process_message
    """Проверяет код активации и выдаёт уроки, если всё окей"""
    user_id = message.from_user.id
    code = message.text.strip().lower()  # Приводим к нижнему регистру
    logger.info(f"7 process_message Проверяем код: {code}")
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            # Ищем курс
            cursor = await conn.execute("""
                SELECT c.course_id, c.version_id, c.title
                FROM course_activation_codes cac
                JOIN course_versions c ON cac.course_id = c.course_id
                WHERE cac.code_word = ?
            """, (code,))
            course_data = await cursor.fetchone()
            logger.info(f"7 1318 course_data:Найдены данные курса: {course_data}")

        if not course_data:
            return await message.answer("Неверное кодовое слово. Попробуйте еще раз или свяжитесь с поддержкой.", parse_mode=None)

        course_id, version_id, course_name = course_data

        async with aiosqlite.connect(DB_FILE) as conn:
            # Проверим, не активирован ли уже этот курс
            cursor = await conn.execute("""
                SELECT 1 FROM user_courses
                WHERE user_id = ? AND course_id = ?
            """, (user_id, course_id))
            existing_enrollment = await cursor.fetchone()

            if existing_enrollment:
                await message.answer("Этот курс уже активирован.", parse_mode=None)
                # Load 0 lesson
                await send_course_description(user_id, course_id)

                # Generate keyboard
                course_numeric_id = await get_course_id_int(course_id)
                keyboard = get_main_menu_inline_keyboard(
                    course_numeric_id=course_numeric_id,
                    lesson_num=0,  # Для описания курса ставим урок 0
                    user_tariff=version_id,
                    homework_pending=False
                )
                await message.answer("Главное меню:", reply_markup=keyboard, parse_mode=None)
            else:
                # Активируем курс
                await conn.execute("""
                    INSERT OR REPLACE INTO user_courses (user_id, course_id, version_id, status, current_lesson, activation_date)
                    VALUES (?, ?, ?, 'active', 1, CURRENT_TIMESTAMP)
                """, (user_id, course_id, version_id))
                await conn.commit()
                await log_user_activity(user_id, "COURSE_ACTIVATION",
                                        f"Курс {course_id} активирован с кодом {message.text.strip()}")

                # Load 0 lesson
                await send_course_description(user_id, course_id)

        async with aiosqlite.connect(DB_FILE) as conn:

            # Получаем общее количество курсов
            cursor = await conn.execute("""
                    SELECT COUNT(*) 
                    FROM user_courses 
                    WHERE user_id = ? AND status IN ('active', 'completed')
                """, (user_id,))
            total_courses = (await cursor.fetchone())[0]

            # Формируем текст кнопки с количеством
            courses_button_text = f"📚 Мои курсы ({total_courses})"
            course_numeric_id = await get_course_id_int(course_id)
            # Генерация клавиатуры
            keyboard = get_main_menu_inline_keyboard(
                course_numeric_id=course_numeric_id,
                lesson_num=0,  # Для описания курса ставим урок 0
                user_tariff=version_id,
                homework_pending=False,
                courses_button_text=courses_button_text
            )

            # Отправляем сообщение
            tariff_names = settings.get("tariff_names", {"v1": "Соло", "v2": "Группа", "v3": "VIP"})
            message_text = (
                f"Курс успешно активирован!\n"
                f"🎓 Курс: {course_name}\n"
                f"🔑 Тариф: {tariff_names.get(version_id, 'Базовый')}\n"
                f"📚 Текущий урок: 1"
            )
        await message.answer(message_text, reply_markup=keyboard, parse_mode=None)

    except Exception as e:
        logger.error(f"Общая ошибка в process_message: {e}", exc_info=True)
        await message.answer("Произошла общая ошибка. Пожалуйста, попробуйте позже.", parse_mode=None)



#  Обработчик входящего контента от пользователя
@dp.message(F.photo | F.video | F.document | F.text)
async def handle_user_content(message: types.Message):
    """Обработчик пользовательского контента для ДЗ"""
    user_id = message.from_user.id
    try:
        async with aiosqlite.connect(DB_FILE) as conn:
            cursor = await conn.execute("""
                SELECT uc.course_id, uc.current_lesson, uc.version_id, uc.hw_status
                FROM user_courses uc
                WHERE uc.user_id = ? AND uc.status = 'active'
            """, (user_id,))
            user_data = await cursor.fetchone()

            if not user_data:
                # Нет активного курса - обрабатываем как код активации
                await handle_activation_code(message)
                return

            course_id, current_lesson, version_id, hw_status = user_data

            # Проверяем статус ДЗ
            if hw_status in ('required', 'rejected') and message.text:
                # Если ДЗ ожидается и это текст - игнорируем
                logger.info(f"Получен ненужный текст от {user_id}, игнорируем.")
                await message.answer("Текст не относится к текущему уроку, проигнорировано.", parse_mode=None)
            else:
                # Если статус ДЗ не 'required' или 'rejected', или это не текст - обрабатываем как обычно
                await handle_homework(message)

    except Exception as e:
        logger.error(f"Ошибка при обработке контента: {e}")
        await message.answer("Произошла ошибка при обработке вашего сообщения.", parse_mode=None)

#=======================Конец обработчиков текстовых сообщений=========================================

@dp.message(F.photo)
async def handle_photo(message: types.Message):
    """Обработчик для фотографий."""
    logger.info(f"88 handle_photo  ")
    try:
        await message.answer("Фотография получена!", parse_mode=None)
    except Exception as e:
        logger.error(f"Ошибка при обработке фотографии: {e}")

@dp.message(F.video)
async def handle_video(message: types.Message):
    """Обработчик для видео."""
    logger.info(f"89 handle_video  ")
    try:
        await message.answer("Видео получено!", parse_mode=None)
    except Exception as e:
        logger.error(f"Ошибка при обработке видео: {e}")

@dp.message(F.document)
async def handle_document(message: types.Message):
    """Обработчик для документов."""
    logger.info(f"90 handle_document  ")
    try:
        await message.answer("Документ получен!", parse_mode=None)
    except Exception as e:
        logger.error(f"Ошибка при обработке документа: {e}")


@dp.message()
async def default_handler(message: types.Message):
    logger.warning(f"Получено необработанное сообщение: {message.text}")

@dp.callback_query()
async def default_callback_handler(query: types.CallbackQuery):
    logger.warning(f"Получен необработанный callback_query: {query.data}")





async def main():
    logger.info("Запуск main()...")
    global settings, COURSE_GROUPS
    # Инициализация базы данных
    await init_db()
    settings = await load_settings()  # Загрузка настроек при запуске
    logger.info(f"444 load_settings {settings.get('groups')=}")

    COURSE_GROUPS = list(map(int, settings.get("groups", {}).keys()))
    logger.info(f"555  {COURSE_GROUPS=}")

    await import_settings_to_db()

    await send_startup_message(bot, ADMIN_GROUP_ID)
    # asyncio.create_task(check_and_schedule_lessons())

    # Запуск бота
    logger.info(f"Бот успешно запущен.")

    logger.info("пускаем таймеры")
    async with aiosqlite.connect(DB_FILE) as conn:
        cursor = await conn.execute("SELECT user_id FROM users")
        users = await cursor.fetchall()

        for user in users:
            await start_lesson_schedule_task(user[0])

    logger.info("Начинаем dp.start_polling()...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())


# Осознание обработчиков:
# @dp.message(Command(...)): Обработчики команд (начинаются с /).
# @dp.message(F.text): Обработчики текстовых сообщений (ловят любой текст).
# @dp.callback_query(lambda c: ...): Обработчики нажатий на кнопки (inline keyboard).
# @dp.message(lambda message: message.text.lower() in settings["activation_codes"]): Обработчик для активации курса по коду.
