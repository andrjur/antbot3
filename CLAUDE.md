# CLAUDE.md — Документация проекта AntBot для ИИ-ассистентов

## 📌 О проекте

**AntBot** — Telegram-бот для управления онлайн-курсами с автоматической выдачей уроков, AI-проверкой ДЗ и мониторингом.

**Стек:**
- Python 3.12 + aiogram 3.x
- SQLite (aiosqlite)
- Docker + Docker Compose
- Prometheus + Grafana (мониторинг)
- n8n (AI-проверка ДЗ)

---

## 📁 Критически важные файлы

### ⚠️ НИКОГДА НЕ КОПИРОВАТЬ В DOCKER-ОБРАЗ (защита в .dockerignore)
```
bot.db              # База данных — хранится только на хосте
settings.json       # Настройки — хранится только на хосте
settings.json1      # Бэкап настроек (предыдущая версия)
settings.json2      # Бэкап настроек (позапрошлая версия)
logs/               # Логи — не нужны в образе
```

### 🔧 Конфигурация
```
.env                # Переменные окружения (токен, админы, webhook)
docker-compose.yml  # Оркестрация (volumes для bot.db и settings.json)
Dockerfile          # Сборка образа (COPY . . с учётом .dockerignore)
.dockerignore       # Исключает файлы из сборки (защита данных)
```

### 📜 Скрипты БД
```
merge_spring31_to_spring3.sql   # Слияние курсов
sync_db_with_settings.sql       # Синхронизация БД с settings.json
```

---

## 🐳 Docker: правила работы

### .dockerignore защищает:
- `bot.db`, `*.db` — базы данных
- `settings.json`, `settings_*.json`, `settings.json1`, `settings.json2` — настройки и бэкапы
- `logs/`, `*.log` — логи
- `__pycache__/`, `*.pyc` — Python кэш
- `*.md` — документация (не нужна в рантайме)
- `tests/` — тесты (не нужны в продакшене)

### volumes в docker-compose.yml:
```yaml
volumes:
  - ./bot.db:/app/bot.db              # База данных с хоста
  - ./settings.json:/app/settings.json # Настройки с хоста
  - ./logs:/app/logs                   # Логи
  - ./VERSION:/app/VERSION             # Версия бота
```

### ✅ Безопасные команды:
```bash
docker compose up -d              # Запуск (данные сохраняются)
docker compose up -d --build      # Пересборка (данные сохраняются)
docker compose restart bot        # Перезапуск (данные сохраняются)
docker compose down               # Остановка (данные сохраняются)
docker compose logs -f bot        # Просмотр логов
```

### ❌ Опасные команды (удаляют данные):
```bash
docker compose down -v            # УДАЛЯЕТ volumes!
docker rm -f antbot && docker volume prune  # УДАЛЯЕТ данные!
docker system prune -a            # УДАЛЯЕТ образы и volumes!
```

---

## 📊 Структура базы данных (bot.db)

### Таблицы:
| Таблица | Описание |
|---------|----------|
| `users` | Пользователи бота (user_id, username, first_name, timezone) |
| `courses` | Курсы (course_id, group_id, title, description, course_type) |
| `course_versions` | Тарифы курсов (course_id, version_id, title, price) |
| `user_courses` | Активации курсов (user_id, course_id, version_id, status, lesson_num) |
| `group_messages` | Уроки (course_id, lesson_num, content_type, text, file_id, level) |
| `course_activation_codes` | Коды активации (code_word, course_id, version_id, price) |
| `homework` | ДЗ на проверке (user_id, course_id, lesson_num, admin_message_id) |
| `homework_gallery` | Выполненные ДЗ (user_id, course_id, lesson_num, file_id) |
| `admin_context` | Контекст администратора (admin_id, context_data) |
| `user_states` | FSM состояния (user_id, state, data) |
| `admin_logs` | Лог действий (user_id, action, course_id, details) |

---

## ⚙️ settings.json структура

```json
{
  "message_interval": 10,
  "tariff_names": {
    "v1": "Базовый",
    "v2": "с проверкой",
    "v3": "VIP"
  },
  "groups": {
    "-1002549199868": "base",
    "-1003710956962": "sprint2",
    "-1003820533058": "spring3"
  },
  "activation_codes": {
    "b1": {"course": "base", "version": "v1", "price": 5000},
    "b22": {"course": "base", "version": "v2", "price": 7000},
    "spro": {"course": "spring3", "version": "v2", "price": 0}
  },
  "course_descriptions": {},
  "courses": {
    "spring3": {
      "title": "spring3 basic",
      "description": "Описание курса..."
    }
  }
}
```

---

## 🛠 Админ-команды бота

| Команда | Описание |
|---------|----------|
| `/show_codes` | Показать курсы и коды активации |
| `/add_course` | Создать новый курс |
| `/upload_lesson` | Загрузить урок (0 = описание, 1+ = уроки) |
| `/list_lessons` | Список уроков курса |
| `/list_admins` | Список админов |
| `/set_hw_timeout <мин>` | Таймаут AI-проверки ДЗ |
| `/export_db` | Экспорт базы данных (JSON) |
| `/import_db` | Импорт базы данных |
| `/remind <id> <msg>` | Отправить напоминание пользователю |
| `/test_mode` | Включить тест-режим (12 часов) |

---

## 🔄 Типовые операции

### Добавление курса:
1. `/add_course` → group_id, коды активации (code1, code2, code3)
2. `/upload_lesson` → урок 0 (описание курса, текст)
3. `/upload_lesson` → уроки 1, 2, 3...
4. Проверить `/show_codes`

### Удаление курса:
1. Админ-панель → Удалить курс → Подтвердить
2. Автоматически: удаление из БД + очистка settings.json + 3 бэкапа

### Слияние курсов (spring31 → spring3):
```bash
sqlite3 bot.db < merge_spring31_to_spring3.sql
docker compose restart bot
```

### Синхронизация БД с settings.json:
1. Отредактировать `sync_db_with_settings.sql` → добавить курсы из settings.json
2. Запустить: `sqlite3 bot.db < sync_db_with_settings.sql`
3. Перезапустить: `docker compose restart bot`

### Обновление бота:
```bash
git pull
docker compose up -d --build bot
```

---

## 📝 Логирование

### Уровни логов:
- `INFO` — обычные события (активация курса, отправка урока)
- `WARNING` — предупреждения (нет активного курса, описание не найдено)
- `ERROR` — ошибки (database is locked, нет file_id)
- `DEBUG` — отладка (поиск описания курса, проверка запросов)

### Просмотр логов:
```bash
docker compose logs -f bot        # В реальном времени
docker compose logs --tail=100    # Последние 100 строк
docker compose logs bot | grep ERROR  # Только ошибки
```

### Частые ошибки:
- `database is locked` — гонка БД, лечится паузами 10мс и `PRAGMA busy_timeout = 5000`
- `Отсутствует file_id для медиа` — медиа без file_id (left_chat_member, new_chat_title)
- `Описание не найдено` — урок 0 пустой или нет в group_messages

---

## 🎯 Функции main.py (ключевые)

| Функция | Описание |
|---------|----------|
| `send_course_description()` | Отправка описания курса (приоритет: courses.description → lesson_num=0 → lesson_num=NULL → урок 1) |
| `activate_course()` | Активация курса по коду |
| `send_lesson_to_user()` | Отправка урока пользователю |
| `handle_homework()` | Обработка ДЗ |
| `check_lesson_schedule()` | Проверка расписания уроков |
| `callback_delete_course_execute()` | Удаление курса + очистка settings.json + бэкапы |
| `clean_settings_json_course()` | Очистка settings.json от курса |
| `backup_settings_file_rotate()` | Ротация бэкапов (json → json1 → json2) |
| `process_add_course_to_db()` | Добавление курса в БД и settings.json |

---

## 🔧 Переменные окружения (.env)

```bash
BOT_TOKEN=7473862113:AAH...
ADMIN_IDS=182643037,954230772
WEBHOOK_URL=https://antbot.indikov.ru/webhook
ALERT_BOT_TOKEN=...
ALERT_CHAT_ID=-100...
DB_FILE=bot.db
SETTINGS_FILE=settings.json
```

---

## 📚 Дополнительные файлы

| Файл | Описание |
|------|----------|
| `описание.md` | Граф взаимодействия и описание функций бота |
| `codebase_summary.txt` | Сводка по кодовой базе |
| `course_description_management.md` | Управление описаниями курсов |
| `DOCKER.md` | Docker-документация |
| `bug1.md` | Отчёт об ошибке (send_course_description) |
| `adv2.md`, `adv3.md` | Улучшения |
| `comp.md` | Заметки |

---

## ⚡ Быстрые команды

```bash
# Перезапуск бота (без перечитки .env)
docker compose restart bot

# Перезапуск с перечиткой .env (данные НЕ теряются!)
docker compose stop && docker compose up -d
# ИЛИ
docker compose down && docker compose up -d

# Логи в реальном времени
docker compose logs -f bot

# Войти в контейнер
docker exec -it antbot /bin/bash

# Проверка БД
sqlite3 bot.db "SELECT course_id, title FROM courses;"

# Бэкап БД (автоматически в backups/)
/export_db  # Через бота (отправит файл)
# Бэкапы сохраняются в backups/database_export_YYYYMMDD_HHMMSS.json

# Экспорт БД в JSON (на сервере)
sqlite3 bot.db ".mode json" ".output export.json" "SELECT * FROM users;" ".output stdout"

# Проверка settings.json
cat settings.json | python -m json.tool
```

---

## ⚠️ Импорт БД — ОПАСНО!

**`/import_db` ПОЛНОСТЬЮ заменяет базу данных!**

### Что происходит при `/import_db`:
1. **Все таблицы очищаются** (`DELETE FROM table`)
2. **Данные из файла заменяют текущие**
3. **Прогресс студентов теряется** ❌

### Когда использовать `/import_db`:
- ✅ База данных пуста или повреждена
- ✅ Откат к предыдущей версии (с потерей прогресса)
- ✅ Тестовое окружение

### Когда НЕ использовать `/import_db`:
- ❌ Есть активные студенты с прогрессом
- ❌ Нужно добавить только курсы/коды
- ❌ Бот работает в продакшене

### Безопасная альтернатива — `/import_db_safe`:
- ✅ Восстанавливает курсы, уроки, тарифы, коды
- ✅ Сохраняет студентов и их прогресс
- ✅ Использует INSERT OR IGNORE / INSERT OR REPLACE

```bash
# Через бота (безопасно):
/import_db_safe  # Отправить JSON-файл из /export_db

# Или через бота (полный импорт, ОПАСНО):
/import_db  # Полная замена БД!
```

**Подробности:** см. `IMPORT_SAFETY.md`

---

## 🔄 Применение изменений в .env

**Важно:** `docker compose restart` **НЕ перечитывает .env**!

### Чтобы применить изменения в .env:

```bash
# 1. Остановить контейнер
docker compose down

# 2. Изменить .env
nano .env

# 3. Запустить заново (перечитает .env, данные НЕ теряются)
docker compose up -d
```

**Данные НЕ теряются**, потому что `bot.db` и `settings.json` смонтированы как volumes.

### ⚠️ Опасные команды (удаляют данные):
```bash
docker compose down -v            # УДАЛЯЕТ volumes!
docker volume prune               # УДАЛЯЕТ все volumes!
```
