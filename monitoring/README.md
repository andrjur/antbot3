# Мониторинг AntBot

## Компоненты

1. **Prometheus** - сбор метрик (порт 9090)
2. **Grafana** - визуализация (порт 3000)
3. **Alertmanager** - отправка алертов в Telegram (порт 9093)
4. **Node Exporter** - метрики системы (порт 9100)

## Быстрый старт

```bash
# Запуск мониторинга
cd monitoring
docker-compose -f docker-compose.monitoring.yml up -d

# Проверка статуса
docker-compose -f docker-compose.monitoring.yml ps

# Остановка
docker-compose -f docker-compose.monitoring.yml down
```

## Доступ к сервисам

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (admin/admin)
- Alertmanager: http://localhost:9093

## Эндпоинты бота

После интеграции в main.py будут доступны:

- `/metrics` - метрики Prometheus
- `/health` - полная проверка здоровья
- `/health/live` - liveness probe (Kubernetes)
- `/health/ready` - readiness probe (Kubernetes)

## Интеграция в main.py

Добавьте в начало main.py:

```python
from services.monitoring import setup_metrics_endpoints
from services.metrics import (
    track_command, track_callback, track_db_operation,
    track_lesson_sent, track_homework_submission
)
```

В функции `main()` после создания `web_app`:

```python
# Настройка мониторинга
await setup_metrics_endpoints(
    app=web_app,
    db_file=DB_FILE,
    bot_token=BOT_TOKEN,
    admin_group_id=ADMIN_GROUP_ID,
    n8n_domain=N8N_DOMAIN,
    webhook_url=f"{BASE_WEBHOOK_URL}{WEBHOOK_PATH}"
)
```

Декораторы для отслеживания:

```python
@track_command("start")
async def start(message: Message):
    ...

@track_callback("course_activation")
async def activate_callback(query: CallbackQuery):
    ...

@track_db_operation("get_user_courses")
async def get_user_courses(user_id: int):
    ...
```

## Метрики

### Бизнес-метрики
- `bot_commands_total` - количество команд
- `bot_lessons_sent_total` - отправленные уроки
- `bot_homework_submissions_total` - отправленные ДЗ
- `bot_active_users` - активные пользователи
- `bot_pending_homework` - ДЗ на проверке

### Технические метрики
- `bot_db_operations_duration_seconds` - время операций БД
- `bot_errors_total` - ошибки
- `bot_uptime_seconds` - время работы
- `bot_n8n_requests_duration_seconds` - время запросов к N8N

### Health-check
- Database connection
- Telegram API доступность
- N8N webhook доступность
- Дисковое пространство
- Uptime

## Алерты

Настроены алерты для:
- Высокой частоты ошибок
- Недоступности бота/БД/API
- Медленных запросов
- Места на диске
- Накопившихся ДЗ

## Настройка Telegram алертов

В `.env` добавьте:
```
ALERT_BOT_TOKEN=your_bot_token
ALERT_CHAT_ID=your_chat_id
```

## Графана дашборд

Дашборд автоматически импортируется при первом запуске.

URL: http://localhost:3000/d/antbot-main
