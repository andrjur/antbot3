# Интеграция мониторинга в main.py

## Шаг 1: Добавить импорты

В начало файла `main.py` добавьте:

```python
# Импорты для мониторинга (добавить после существующих импортов)
from services.metrics import (
    track_command, track_callback, track_db_operation,
    track_lesson_sent, track_homework_submission,
    track_homework_check_duration, track_n8n_request,
    track_webhook_request, init_bot_info, BOT_UPTIME
)
from services.monitoring import setup_metrics_endpoints
```

## Шаг 2: Инициализация метрик

В функции `main()` после создания бота и диспетчера добавьте:

```python
# Инициализация метрик
init_bot_info(version="1.0.0", environment="production")
```

## Шаг 3: Настройка эндпоинтов мониторинга

В функции `main()` после создания `web_app` и перед запуском:

```python
# Настройка эндпоинтов мониторинга
await setup_metrics_endpoints(
    app=web_app,
    db_file=DB_FILE,
    bot_token=BOT_TOKEN,
    admin_group_id=ADMIN_GROUP_ID,
    n8n_domain=N8N_DOMAIN,
    webhook_url=f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}"
)
```

## Шаг 4: Добавить декораторы к обработчикам

Примеры декорирования обработчиков:

### Команды:
```python
@dp.message(CommandStart())
@track_command("start")
async def start(message: Message):
    # существующий код
    ...

@dp.message(Command("activate"))
@track_command("activate")
async def activate_command(message: Message, state: FSMContext):
    # существующий код
    ...

@dp.message(Command("mycourses"))
@track_command("mycourses")
async def show_my_courses(message: Message):
    # существующий код
    ...
```

### Callback-и:
```python
@dp.callback_query(CourseCallback.filter())
@track_callback("course_action")
async def handle_course_callback(query: CallbackQuery, callback_data: CourseCallback):
    # существующий код
    ...
```

## Шаг 5: Трекинг операций БД

Добавьте декоратор `@track_db_operation` к функциям работы с БД:

```python
@track_db_operation("get_user_courses")
async def get_user_courses(user_id: int) -> list:
    # существующий код
    ...

@track_db_operation("activate_course")
async def activate_course(user_id: int, activation_code: str, level: int = 1):
    # существующий код
    ...
```

## Шаг 6: Трекинг отправки уроков

В функции `send_lesson_to_user()` добавьте:

```python
async def send_lesson_to_user(user_id: int, course_id: str, lesson_num: int, force: bool = False):
    try:
        # существующий код отправки
        ...
        
        # Успешная отправка
        track_lesson_sent(course_id, success=True)
        
    except Exception as e:
        # Ошибка отправки
        track_lesson_sent(course_id, success=False)
        logger.error(f"Error sending lesson: {e}")
        raise
```

## Шаг 7: Трекинг ДЗ

В функции `process_homework()` добавьте:

```python
async def process_homework(message: Message, ...):
    try:
        # существующий код
        ...
        
        # Трекинг отправки ДЗ
        track_homework_submission(status="submitted", course_id=course_id)
        
        # Трекинг времени проверки AI (если используется)
        start_time = time.time()
        # ... отправка в N8N ...
        duration = time.time() - start_time
        track_homework_check_duration(check_type="ai", duration=duration)
        
    except Exception as e:
        track_homework_submission(status="error", course_id=course_id)
        raise
```

## Шаг 8: Трекинг N8N запросов

В функциях, делающих запросы к N8N:

```python
async def send_homework_to_n8n(...):
    import time
    start_time = time.time()
    
    try:
        # существующий код запроса
        async with aiohttp.ClientSession() as session:
            async with session.post(N8N_HOMEWORK_CHECK_WEBHOOK_URL, ...) as response:
                ...
        
        duration = time.time() - start_time
        track_n8n_request(endpoint="homework_check", duration=duration, success=True)
        
    except Exception as e:
        duration = time.time() - start_time
        track_n8n_request(endpoint="homework_check", duration=duration, success=False)
        raise
```

## Шаг 9: Трекинг webhook запросов

Добавьте в обработчики webhook:

```python
async def handle_n8n_hw_approval(request: web.Request):
    track_webhook_request(endpoint="n8n_hw_approval", status_code=200)
    # существующий код
    ...
```

## Шаг 10: Тестирование

После интеграции проверьте эндпоинты:

```bash
# Метрики
curl http://localhost:8080/metrics

# Health-check
curl http://localhost:8080/health
curl http://localhost:8080/health/live
curl http://localhost:8080/health/ready
```

## Готово!

Теперь метрики доступны в Prometheus, а алерты будут отправляться в Telegram при проблемах.
