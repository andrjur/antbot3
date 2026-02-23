# AntBot - Telegram-бот для образовательных курсов

## Описание

AntBot - это многофункциональный Telegram-бот для автоматизации образовательных курсов. Поддерживает расписание уроков, проверку ДЗ (в т.ч. через AI/n8n) и техподдержку.

---

## 🚀 Критически важная настройка сети (Cloudflare + Docker)

Поскольку на сервере также работает **n8n** (занимающий порты 80 и 443), для бота используется специальная схема маршрутизации через Cloudflare, чтобы избежать конфликтов.

### 1. Проблема
Телеграм отправляет Webhook только на порты 443, 80, 88 или 8443. Порт 443 уже занят n8n.

### 2. Решение: Cloudflare Origin Rules
Мы используем Cloudflare, чтобы принимать запросы на `https://bot.your-domain.com` (порт 443), но перенаправлять их на сервер на порт **8080**.

**Настройка в Cloudflare Dashboard:**
1. Перейдите в **Rules** → **Origin Rules**.
2. Нажмите **Create Rule**.
3. **Field:** Hostname, **Operator:** equals, **Value:** `bot.your-domain.com`.
4. **Destination Port:** Rewrite to `8080`.
5. Нажмите **Deploy**.

### 3. Настройка Docker (`docker-compose.yml`)
Бот должен слушать порт 8080.
```yaml
ports:
  - "8080:8080"
```

### 4. Переменные окружения (`.env`)
Это **самая частая причина ошибок** (`Empty reply from server` или `Connection refused`).

*   **`WEBAPP_HOST=0.0.0.0`** — ОБЯЗАТЕЛЬНО. Если не указать, aiohttp может слушать только IPv6 (`::1`), и Docker не сможет пробросить IPv4 трафик внутрь контейнера.
*   **`WEBHOOK_HOST=https://bot.your-domain.com`** — Указывать **БЕЗ порта**, так как для внешнего мира (Телеграма) это стандартный HTTPS (443), а подмену порта делает Cloudflare скрыто.

---

## 🗄️ Структура Базы Данных (SQLite)

Бот использует базу данных SQLite для хранения всей информации.

### `users`
Хранит основную информацию о пользователях.
- `user_id` (INTEGER, PRIMARY KEY): Уникальный ID пользователя в Telegram.
- `username` (TEXT): Имя пользователя (@username).
- `first_name` (TEXT): Имя пользователя.
- `last_name` (TEXT): Фамилия пользователя.
- `timezone` (TEXT): Часовой пояс (по умолчанию 'Europe/Moscow').
- `registered_at` (TIMESTAMP): Дата регистрации.

### `courses`
Содержит определения курсов.
- `course_id` (TEXT, PRIMARY KEY): Строковый идентификатор (например, "base").
- `id` (INTEGER): Числовой автоинкрементный ID.
- `group_id` (TEXT): ID Telegram-канала с контентом.
- `title` (TEXT): Полное название курса.
- `course_type` (TEXT): `LESSON_BASED` или `TASK_BASED`.
- `message_interval` (REAL): Интервал между уроками в часах.
- `description` (TEXT): Описание курса.

### `course_versions`
Тарифы или версии курсов.
- `course_id` (TEXT): Внешний ключ к `courses`.
- `version_id` (TEXT): Идентификатор тарифа (например, "v1", "v2").
- `title` (TEXT): Название тарифа.
- `price` (REAL): Цена.
- `description` (TEXT): Описание тарифа.

### `user_courses`
Связующая таблица прогресса пользователя.
- `user_id`, `course_id`, `version_id`: Составной ключ.
- `status` (TEXT): 'active', 'inactive', 'completed'.
- `hw_status` (TEXT): 'none', 'pending', 'approved', 'rejected'.
- `current_lesson` (INTEGER): Номер текущего урока.
- `level` (INTEGER): Уровень сложности.
- `first_lesson_sent_time`, `last_lesson_sent_time`: Время отправки.

### `group_messages`
Контент уроков из каналов.
- `group_id`, `lesson_num`, `course_id`: Идентификация.
- `content_type` (TEXT): 'text', 'photo', 'video' и т.д.
- `is_homework` (BOOLEAN): Флаг домашнего задания.
- `text`, `file_id`: Содержимое.

### `course_activation_codes`
Коды активации курсов.
- `code_word` (TEXT, PRIMARY KEY): Код для активации.
- `course_id`, `version_id`: Что активируется.
- `price_rub` (INTEGER): Цена.

### `pending_admin_homework`
Очередь ДЗ на проверку.
- `admin_message_id` (INTEGER, PRIMARY KEY): ID в админ-группе.
- `student_user_id`: Кто отправил.
- `course_numeric_id`, `lesson_num`: Контекст.

### `user_actions_log`
Журнал действий для аналитики.
- `user_id`, `action_type`, `timestamp`: Основные поля.
- `course_id`, `lesson_num`, `details`: Контекст.

---

## ⚙️ Установка и Запуск

1. **Клонирование и настройка:**
    ```bash
    git clone <repo_url>
    cd antbot4
    cp .env.example .env
    nano .env  # Заполните переменные
    ```

2. **Создание файла настроек (ВАЖНО!):**
    
    ⚠️ **ПРЕДУПРЕЖДЕНИЕ:** Docker автоматически создаёт `settings.json` как **директорию**, если файла не существует. Перед запуском ОБЯЗАТЕЛЬНО создайте файл:
    
    ```bash
    # Вариант 1: Скопировать из примера (рекомендуется)
    cp settings.json.example settings.json
    
    # Вариант 2: Создать пустой файл
    echo '{}' > settings.json
    ```
    
3. **Запуск контейнеров:**
    ```bash
    docker-compose up -d --build
    ```

3. **Установка Webhook (Вручную):**
    Вставьте ссылку в браузер, подставив свои значения:
    ```
    https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://bot.your-domain.com/<SECRET_PATH>&secret_token=<SECRET_TOKEN>
    ```
    *Должно вернуть: `"Webhook was set"`.*

4. **Проверка работоспособности:**
    Из консоли сервера:
    ```bash
    curl -v http://127.0.0.1:8080/health/live
    ```
    *Должно вернуть: `HTTP/1.1 200 OK` и `{"status": "alive"}`.*

---

## 🗑️ Очистка базы данных (удаление старых курсов)

Если в базе данных остались старые тестовые курсы, которые не нужны в `settings.json`, их можно удалить через SQLite:

### 1. Подключение к базе данных
```bash
cd ~/antbot4
sqlite3 bot.db
```

### 2. Удаление старого курса полностью (пример: "женственность15")
```sql
DELETE FROM user_courses WHERE course_id = 'женственность15';
DELETE FROM course_activation_codes WHERE course_id = 'женственность15';
DELETE FROM course_versions WHERE course_id = 'женственность15';
DELETE FROM group_messages WHERE course_id = 'женственность15';
DELETE FROM pending_admin_homework WHERE course_numeric_id IN (SELECT id FROM courses WHERE course_id = 'женственность15');
DELETE FROM user_actions_log WHERE course_id = 'женственность15';
DELETE FROM courses WHERE course_id = 'женственность15';
```

### 3. Переименование курса (пример: "база" → "base")
```sql
UPDATE user_courses SET course_id = 'base' WHERE course_id = 'база';
UPDATE course_activation_codes SET course_id = 'base' WHERE course_id = 'база';
UPDATE course_versions SET course_id = 'base' WHERE course_id = 'база';
UPDATE group_messages SET course_id = 'base' WHERE course_id = 'база';
UPDATE pending_admin_homework SET course_numeric_id = (SELECT id FROM courses WHERE course_id = 'base') WHERE course_numeric_id IN (SELECT id FROM courses WHERE course_id = 'база');
UPDATE user_actions_log SET course_id = 'base' WHERE course_id = 'база';
UPDATE courses SET course_id = 'base', title = 'base' WHERE course_id = 'база';
```

### 4. Выход из SQLite
```sql
.quit
```

### 5. Обновление settings.json вручную
```bash
nano settings.json
```

Оставить только нужные курсы и коды:
```json
{
    "message_interval": 12,
    "tariff_names": {
        "v1": "Solo",
        "v2": "coach",
        "v3": "premium"
    },
    "groups": {
        "-1002549199868": "base"
    },
    "activation_codes": {
        "b1": {"course": "base", "version": "v1", "price": 5000},
        "b22": {"course": "base", "version": "v2", "price": 7000},
        "bvip": {"course": "base", "version": "v3", "price": 18000}
    }
}
```

### 6. Перезапуск бота
```bash
docker-compose restart bot
```

---

## 📊 Доступ к сервисам

| Сервис | URL | Внутренний порт |
|--------|-----|-----------------|
| **Бот** | `https://bot.your-domain.com` | 8080 |
| **n8n** | `https://n8n.your-domain.com` | 5678 |
| **Grafana** | `http://<IP>:3000` | 3000 |
| **Prometheus** | `http://<IP>:9090` | 9090 |

---

## 🔧 Исправление проблем

### n8n: Ошибка прав доступа (Permission denied)

Если n8n падает с ошибкой `EACCES: permission denied, open '/home/node/.n8n/crash.journal'`:

```bash
# 1. Остановить n8n
docker-compose stop n8n

# 2. Исправить права на папку n8n_data
sudo chown -R 1000:1000 ~/antbot4/n8n_data
sudo chmod -R 755 ~/antbot4/n8n_data

# 3. Запустить n8n
docker-compose start n8n

# 4. Проверить логи
docker-compose logs n8n --tail=20
```

**Альтернативное решение** - добавить в `docker-compose.yml`:

```yaml
n8n:
  environment:
    - N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS=false
```

### Бот: Ошибка "no such column: timestamp"

Если в `pending_admin_homework` нет колонки `timestamp`:

```bash
# Добавить колонку вручную
sqlite3 bot.db "ALTER TABLE pending_admin_homework ADD COLUMN timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP;"

# Проверить
sqlite3 bot.db ".schema pending_admin_homework"

# Перезапустить бота
docker-compose restart bot
```

### Очистка места на диске

```bash
# Очистить Docker кэш
docker system prune -af
docker volume prune -f

# Удалить старые бэкапы settings
rm -f settings_*.json

# Проверить место
df -h
```

---

## 📜 Лицензия

MIT
