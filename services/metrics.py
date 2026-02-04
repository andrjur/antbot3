"""
Сервис метрик Prometheus для мониторинга бота
"""
from prometheus_client import Counter, Histogram, Gauge, Info, generate_latest, CONTENT_TYPE_LATEST
from functools import wraps
import time
import logging

logger = logging.getLogger(__name__)

# Метрики бота
BOT_MESSAGES_TOTAL = Counter(
    'bot_messages_total',
    'Общее количество сообщений',
    ['message_type', 'command']
)

BOT_COMMANDS_TOTAL = Counter(
    'bot_commands_total',
    'Общее количество команд',
    ['command']
)

BOT_CALLBACKS_TOTAL = Counter(
    'bot_callbacks_total',
    'Общее количество callback запросов',
    ['callback_type']
)

BOT_ERRORS_TOTAL = Counter(
    'bot_errors_total',
    'Общее количество ошибок',
    ['error_type', 'location']
)

BOT_DB_OPERATIONS_DURATION = Histogram(
    'bot_db_operations_duration_seconds',
    'Время выполнения операций с БД',
    ['operation'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

BOT_SCHEDULER_CHECKS_TOTAL = Counter(
    'bot_scheduler_checks_total',
    'Количество проверок расписания',
    ['status']
)

BOT_LESSONS_SENT_TOTAL = Counter(
    'bot_lessons_sent_total',
    'Количество отправленных уроков',
    ['course_id', 'status']
)

BOT_HOMEWORK_SUBMISSIONS_TOTAL = Counter(
    'bot_homework_submissions_total',
    'Количество отправленных ДЗ',
    ['status', 'course_id']
)

BOT_HOMEWORK_CHECKS_DURATION = Histogram(
    'bot_homework_checks_duration_seconds',
    'Время проверки ДЗ',
    ['check_type'],  # 'ai' или 'manual'
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0]
)

BOT_ACTIVE_USERS = Gauge(
    'bot_active_users',
    'Количество активных пользователей',
    ['period']  # '24h', '7d', '30d'
)

BOT_ACTIVE_COURSES = Gauge(
    'bot_active_courses',
    'Количество активных курсов'
)

BOT_PENDING_HOMEWORK = Gauge(
    'bot_pending_homework',
    'Количество ДЗ ожидающих проверки'
)

BOT_INFO = Info(
    'bot',
    'Информация о боте'
)

BOT_UPTIME = Gauge(
    'bot_uptime_seconds',
    'Время работы бота в секундах'
)

BOT_WEBHOOK_REQUESTS_TOTAL = Counter(
    'bot_webhook_requests_total',
    'Количество webhook запросов',
    ['endpoint', 'status']
)

BOT_N8N_REQUESTS_TOTAL = Counter(
    'bot_n8n_requests_total',
    'Количество запросов к n8n',
    ['endpoint', 'status']
)

BOT_N8N_REQUESTS_DURATION = Histogram(
    'bot_n8n_requests_duration_seconds',
    'Время запросов к n8n',
    ['endpoint'],
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
)


def track_db_operation(operation_name):
    """Декоратор для отслеживания времени выполнения операций БД"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                BOT_DB_OPERATIONS_DURATION.labels(operation=operation_name).observe(time.time() - start_time)
                return result
            except Exception as e:
                BOT_DB_OPERATIONS_DURATION.labels(operation=operation_name).observe(time.time() - start_time)
                BOT_ERRORS_TOTAL.labels(error_type=type(e).__name__, location=f"db_{operation_name}").inc()
                raise
        return wrapper
    return decorator


def track_command(command_name):
    """Декоратор для отслеживания команд"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            BOT_COMMANDS_TOTAL.labels(command=command_name).inc()
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                BOT_ERRORS_TOTAL.labels(error_type=type(e).__name__, location=f"cmd_{command_name}").inc()
                raise
            finally:
                duration = time.time() - start_time
                logger.debug(f"Command {command_name} took {duration:.3f}s")
        return wrapper
    return decorator


def track_callback(callback_type):
    """Декоратор для отслеживания callback запросов"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            BOT_CALLBACKS_TOTAL.labels(callback_type=callback_type).inc()
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                BOT_ERRORS_TOTAL.labels(error_type=type(e).__name__, location=f"callback_{callback_type}").inc()
                raise
        return wrapper
    return decorator


def track_lesson_sent(course_id, success=True):
    """Записывает метрику отправленного урока"""
    status = "success" if success else "failure"
    BOT_LESSONS_SENT_TOTAL.labels(course_id=course_id, status=status).inc()


def track_homework_submission(status, course_id):
    """Записывает метрику отправки ДЗ"""
    BOT_HOMEWORK_SUBMISSIONS_TOTAL.labels(status=status, course_id=course_id).inc()


def track_homework_check_duration(check_type, duration):
    """Записывает метрику времени проверки ДЗ"""
    BOT_HOMEWORK_CHECKS_DURATION.labels(check_type=check_type).observe(duration)


def track_n8n_request(endpoint, duration, success=True):
    """Записывает метрику запроса к n8n"""
    status = "success" if success else "failure"
    BOT_N8N_REQUESTS_TOTAL.labels(endpoint=endpoint, status=status).inc()
    if success:
        BOT_N8N_REQUESTS_DURATION.labels(endpoint=endpoint).observe(duration)


def track_webhook_request(endpoint, status_code):
    """Записывает метрику webhook запроса"""
    status = "success" if 200 <= status_code < 300 else "failure"
    BOT_WEBHOOK_REQUESTS_TOTAL.labels(endpoint=endpoint, status=status).inc()


async def update_active_users_metric(db_file):
    """Обновляет метрику активных пользователей"""
    try:
        import aiosqlite
        from datetime import datetime, timedelta
        
        async with aiosqlite.connect(db_file) as conn:
            now = datetime.now()
            
            # За 24 часа
            day_ago = (now - timedelta(days=1)).isoformat()
            cursor = await conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_actions_log WHERE created_at > ?",
                (day_ago,)
            )
            result = await cursor.fetchone()
            BOT_ACTIVE_USERS.labels(period='24h').set(result[0] or 0)
            
            # За 7 дней
            week_ago = (now - timedelta(days=7)).isoformat()
            cursor = await conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_actions_log WHERE created_at > ?",
                (week_ago,)
            )
            result = await cursor.fetchone()
            BOT_ACTIVE_USERS.labels(period='7d').set(result[0] or 0)
            
            # За 30 дней
            month_ago = (now - timedelta(days=30)).isoformat()
            cursor = await conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM user_actions_log WHERE created_at > ?",
                (month_ago,)
            )
            result = await cursor.fetchone()
            BOT_ACTIVE_USERS.labels(period='30d').set(result[0] or 0)
    except Exception as e:
        logger.error(f"Error updating active users metric: {e}")


async def update_active_courses_metric(db_file):
    """Обновляет метрику активных курсов"""
    try:
        import aiosqlite
        
        async with aiosqlite.connect(db_file) as conn:
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM user_courses WHERE status = 'active'"
            )
            result = await cursor.fetchone()
            BOT_ACTIVE_COURSES.set(result[0] or 0)
    except Exception as e:
        logger.error(f"Error updating active courses metric: {e}")


async def update_pending_homework_metric(db_file):
    """Обновляет метрику ДЗ ожидающих проверки"""
    try:
        import aiosqlite
        
        async with aiosqlite.connect(db_file) as conn:
            cursor = await conn.execute(
                """SELECT COUNT(*) FROM user_courses 
                    WHERE hw_status = 'pending' AND status = 'active'"""
            )
            result = await cursor.fetchone()
            BOT_PENDING_HOMEWORK.set(result[0] or 0)
    except Exception as e:
        logger.error(f"Error updating pending homework metric: {e}")


def get_metrics_response():
    """Возвращает метрики в формате Prometheus"""
    from aiohttp import web
    return web.Response(
        body=generate_latest(),
        content_type=CONTENT_TYPE_LATEST
    )


def init_bot_info(version="1.0.0", environment="production"):
    """Инициализирует информацию о боте"""
    BOT_INFO.info({
        'version': version,
        'environment': environment,
        'framework': 'aiogram',
        'language': 'python'
    })
