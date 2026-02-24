# AntBot - Goals and Development Notes

## Project Overview

Telegram-бот для образовательных курсов с функциями:
- Рассылка уроков по расписанию
- Проверка домашних заданий (вручную админами + AI через n8n webhooks)
- Мультикурсовая система с тарифами
- Система админов (суперадмины из .env + админы группы)

---

## Current Architecture

### Components
1. **Bot (Python/Aiogram)** - main.py
2. **n8n** - AI проверка ДЗ через webhook
3. **Prometheus** - метрики
4. **Grafana** - визуализация
5. **Alertmanager** - уведомления в Telegram

### Database Tables (DO NOT MODIFY)
- `users` - пользователи
- `courses` - курсы
- `course_versions` - тарифы
- `user_courses` - прогресс пользователей
- `group_messages` - контент уроков
- `pending_admin_homework` - ДЗ на проверке
- `admin_context` - контекст для админов

---

## Critical Rules

1. **НИКОГДА не менять схему БД** (no ALTER TABLE)
2. **settings.json в .gitignore** - не перезаписывать
3. **Без markdown-звёздочек** в сообщениях бота (parse_mode=None)
4. **Суперадмины из ADMIN_IDS (.env)**, админы группы из ADMIN_GROUP_ID
5. **🚨 БАЗА ДАННЫХ ДОЛЖНА ВСЕГДА БЫТЬ В ПОРЯДКЕ, ДАЖЕ ПРИ ПЕРЕСБОРКЕ DOCKER КОНТЕЙНЕРОВ!**
   - bot.db смонтирован как volume: `./bot.db:/app/bot.db`
   - При `docker-compose down` база НЕ должна удаляться
   - При `docker-compose up -d --build` база должна сохраняться
   - РЕГУЛЯРНО ДЕЛАТЬ БЭКАПЫ: `cp bot.db bot.db.backup`
6. **🚨 ПРИ РЕПОСТЕ УРОКОВ** - показывать одно сообщение со счётчиком, а не много окон
7. **🚨 ВСЕГДА ПУШИТЬ ИЗМЕНЕНИЯ ПОСЛЕ КОММИТА!**
   - `git commit` ≠ `git push`
   - После коммита проверить: `git status`
   - Если "Your branch is ahead" → **СРАЗУ ПУШИТЬ**: `git push antbot4 main`
   - На сервере: `git pull` для получения изменений

---

## Environment Variables

```
HW_TIMEOUT_SECONDS=120          # Таймаут AI-проверки (по умолчанию 120 сек)
N8N_WEBHOOK_SECRET=n8n_sec_...  # Для аутентификации webhook от бота
N8N_CALLBACK_SECRET=500         # Для callback от n8n в бот
ADMIN_GROUP_ID=-100...          # ID админ-группы
```

---

## 📝 Git Workflow Instruction

### После каждого коммита:

```bash
# 1. Сделать коммит
git add <файлы>
git commit -m "описание изменений"

# 2. ПРОВЕРИТЬ СТАТУС (ОБЯЗАТЕЛЬНО!)
git status

# 3. Если видишь "Your branch is ahead of 'antbot4/main'" → ПУШИТЬ!
git push antbot4 main

# 4. Убедиться что пуш успешен (нет ошибок)
```

### На сервере после пуша:

```bash
cd ~/antbot4
git pull
docker-compose restart bot  # или up -d --build bot
```

### Частые ошибки:

| Ошибка | Решение |
|--------|---------|
| Коммит сделал, а на сервере нет изменений | `git push antbot4 main` |
| `git pull` говорит "Already up to date" | Проверить remote: `git remote -v` |
| Конфликт при `git pull` | `git status`, решить конфликты, `git commit`, `git push` |
| Файл не пушится | Проверить `.gitignore`, добавить `-f` если нужно |

### Проверка перед пушем:

```bash
# Что изменено?
git diff HEAD

# Какие файлы будут закоммичены?
git status

# Последний коммит
git log -1 --oneline
```

---

## Homework Flow (IDEAL)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         HOMEWORK FLOW                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  1. Student sends homework                                          │
│     └──> Bot saves to pending_admin_homework                        │
│     └──> Bot sends to admin group with buttons [Accept] [Reject]    │
│     └──> Bot shows student: "✅ На проверке!"                        │
│                                                                     │
│  2a. Admin clicks button (within timeout)                           │
│      └──> Bot updates student's hw_status                           │
│      └──> Bot sends feedback to student                             │
│      └──> Bot removes buttons from admin message                    │
│      └──> Bot deletes from pending_admin_homework                   │
│      └──> Bot cancels n8n timeout check for this HW                 │
│                                                                     │
│  2b. Timeout expires (admin didn't respond)                         │
│      └──> Bot sends to n8n for AI check                             │
│      └──> Bot edits admin message: "🤖 ИИ проверяет..."              │
│      └──> n8n processes and calls callback webhook                  │
│      └──> Bot receives result                                       │
│      └──> Bot updates student's hw_status                           │
│      └──> Bot sends feedback to student                             │
│      └──> Bot removes buttons from admin message                    │
│      └──> Bot deletes from pending_admin_homework                   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## ✅ RESOLVED Issues

### Skip homework feature
**Решено:** 2026-02-24
**Ключевые слова:** *пропускаю*, пропускаю, пропуск, /skip
**Код:** main.py ~8345, `handle_homework()`

### Lesson 0 error
**Решено:** 2026-02-24
**Проблема:** При lesson_num=0 показывалась ошибка "урок недоступен"
**Решение:** Добавлена проверка в `send_lesson_to_user()` - при lesson_num <= 0 показывается главное меню

### Delete old pending HW on resubmit
**Решено:** 2026-02-24
**Решение:** При повторной отправке ДЗ удаляется старая запись из pending_admin_homework

### HW timeout in seconds
**Решено:** 2026-02-24
**Было:** HW_TIMEOUT_MINUTES (минуты)
**Стало:** HW_TIMEOUT_SECONDS (секунды, по умолчанию 120)

### Remove pending HW from DB only on callback
**Решено:** 2026-02-24
**Было:** Удалялось при отправке на n8n
**Стало:** Удаляется только при получении callback или при ручной проверке админом

### Clear admin buttons on startup
**Решено:** 2026-02-24
**Решение:** При старте бота убираются кнопки со всех pending ДЗ в админ-группе

### Filter admin group messages
**Решено:** 2026-02-24
**Проблема:** Сообщения из админ-группы считались домашкой
**Решение:** Добавлен фильтр `F.chat.type == "private"` в handle_text

---

## ✅ RESOLVED Issues

### /start для админа не работает (Telegram медленный)
**Status:** ✅ RESOLVED 2026-02-24
**Cause:** Telegram API Flood control - ответ задерживается на 30+ секунд
**Evidence:** `Flood control exceeded on method 'SendMessage'. Retry in 36 seconds.`
**Fix:** Это проблема на стороне Telegram, не код. Нужно подождать.
**Note:** Код работает корректно, логи показывают `is admin, checking for active course...`

---

## ❌ CURRENT PROBLEMS

### 1. n8n callback НЕ приходит в бот
**Status:** BLOCKING - Needs n8n configuration
**Symptoms:**
- Bot sends to n8n: ✅ OK (200)
- NO logs in bot about receiving callback
- Student never gets feedback
- Buttons remain visible

**Debug steps:**
1. Check n8n workflow execution history
2. Verify HTTP Request node URL = `{{ $('Edit Fields').item.json.callback_webhook_url_result }}`
3. Verify Header: `X-CALLBACK-SIGNATURE` = `500` (N8N_CALLBACK_SECRET)
4. Test callback URL manually:
```bash
curl -X POST https://bot.indikov.ru/webhook/n8n_hw_result \
  -H "X-CALLBACK-SIGNATURE: 500" \
  -H "Content-Type: application/json" \
  -d '{"student_user_id": 123, "course_numeric_id": 8, "lesson_num": 2, "is_approved": true, "feedback_text": "test", "original_admin_message_id": 123}'
```

### 2. /start для админа не показывает меню
**Status:** DEBUGGING - added logs to find exact error location
**Symptoms:**
- Админ вводит /start
- Логи: `is_admin=True` ✓
- НЕТ логов `showing admin menu` или `has NO active course`
- Бот молчит

**Hypothesis:**
SQL запрос падает с ошибкой в JOIN с course_versions.
```sql
JOIN course_versions cv ON uc.course_id = cv.course_id AND uc.version_id = cv.version_id
```
Если для курса нет записи в course_versions, JOIN вернёт пустой результат.

**Debug added:**
- Log before SQL query
- try/except around query
- Log query result

**Server command:**
```bash
git pull && docker-compose up -d --build bot
docker-compose logs bot --tail=30
```

### 3. Пропали все уроки из базы!
**Status:** 🔴 CRITICAL DATA LOSS - NEED TO RELOAD LESSONS
**Evidence:**
- `Для курса 'sprint2' найдено 0 уроков. Запрошен урок 1.`
- Ранее было 25 уроков для sprint2
- group_messages пуста!

**Recovery:**
1. Загрузить уроки заново через репост в админ-группу
2. Использовать `/upload_lesson` команду

**Prevention:**
```bash
# На сервере - установить sqlite3
sudo apt-get install sqlite3

# Сделать бэкап базы
./backup_db.sh

# Или вручную
cp bot.db backups/bot_$(date +%Y%m%d).db

# Проверить содержимое
sqlite3 bot.db "SELECT course_id, COUNT(*) FROM group_messages GROUP BY course_id;"
```

### 4. Много окон при репосте уроков
**Status:** TODO
**Problem:** При загрузке уроков репостом показывается много сообщений с кнопками
**Fix:** Редактировать предыдущее сообщение вместо отправки нового
**Note:** Нужно сохранять message_id последнего сообщения в state

### 5. Спам "урок недоступен"
**Status:** ✅ FIXED 2026-02-24
**Problem:** Каждую минуту отправлялось сообщение "урок недоступен"
**Fix:** Добавлен set `missing_lesson_warnings_sent` для отслеживания уже отправленных предупреждений

---

## n8n Workflow

### Webhook Node (Input from Bot)
- **URL:** `/webhook/aa46a723-619e-42e9-8e51-49ba51813718`
- **Authentication:** Header Auth
  - Header: `X-N8N-Signature`
  - Value: `N8N_WEBHOOK_SECRET` from .env

### HTTP Request Node (Callback to Bot)
- **URL:** `{{ $('Edit Fields').item.json.callback_webhook_url_result }}`
- **Authentication:** Header Auth
  - Header: `X-CALLBACK-SIGNATURE`
  - Value: `500` (N8N_CALLBACK_SECRET from .env)

### Payload from Bot
```json
{
  "action": "check_homework_timeout",
  "student_user_id": 123456789,
  "student_name": "Имя Фамилия",
  "course_numeric_id": 8,
  "course_id": "sprint2",
  "course_title": "Спринт",
  "lesson_num": 2,
  "lesson_assignment_description": "Текст задания...",
  "expected_homework_type": "text",
  "homework_text": "Текст ДЗ от студента",
  "homework_file_id": null,
  "admin_message_id": 768,
  "admin_group_id": -1002591981307,
  "student_message_id": 12345,
  "callback_webhook_url_result": "https://bot.indikov.ru/webhook/n8n_hw_result",
  "telegram_bot_token": "bot_token",
  "timeout_seconds": 120
}
```

### Callback to Bot
```json
{
  "student_user_id": 123456789,
  "course_numeric_id": 8,
  "lesson_num": 2,
  "is_approved": true,
  "feedback_text": "Отличная работа!",
  "original_admin_message_id": 768
}
```

---

## Code Locations

| Feature | File | Lines |
|---------|------|-------|
| HW_TIMEOUT_SECONDS | main.py | ~180 |
| check_pending_homework_timeout | main.py | ~1094-1200 |
| handle_homework | main.py | ~8250-8700 |
| handle_homework_result | main.py | ~7930-8050 |
| n8n callback handler | main.py | ~1676 |
| send_data_to_n8n | main.py | ~1603 |
| cmd_set_hw_timeout | main.py | ~4119 |
| Пропуск ДЗ | main.py | ~8345 |
| cmd_start (admin check) | main.py | ~5862 |
| on_startup (clear pending) | main.py | ~9056 |

---

## Debug Commands

```bash
# На сервере

# Проверить pending ДЗ
sqlite3 bot.db "SELECT * FROM pending_admin_homework;"

# Очистить pending ДЗ
sqlite3 bot.db "DELETE FROM pending_admin_homework;"

# Проверить курсы
sqlite3 bot.db "SELECT course_id, COUNT(*) FROM group_messages GROUP BY course_id;"

# Логи бота
docker-compose logs bot --tail=50

# Перезапуск
git pull && docker-compose up -d --build bot

# Полный перезапуск
docker-compose down && docker-compose up -d

# Тест callback
curl -X POST https://bot.indikov.ru/webhook/n8n_hw_result \
  -H "X-CALLBACK-SIGNATURE: 500" \
  -H "Content-Type: application/json" \
  -d '{"test": true}'
```

---

## Notes

- n8n модель: DeepSeek не поддерживает изображения
- Для изображений нужно использовать Gemini или GPT-4 Vision
- Memory node в n8n не нужен для timeout (single request)
- Курс "base" не имеет контента в group_messages

---

## 📦 Git & Database Policy

### НИКОГДА не пушить в git:
- `bot.db` (база данных)
- `settings.json` (настройки курсов)
- `backups/` (бэкапы)
- `logs/*.log` (логи)
- `.env` (секреты)

### Файлы в .gitignore:
```
bot.db
settings.json
backups/
logs/
.env
__pycache__/
*.pyc
.coverage
```

### Синхронизация данных:
| Что | Где хранить | Как синхронизировать |
|-----|-------------|---------------------|
| Код | GitHub | `git push` / `git pull` |
| База данных | Сервер (не в git) | `scp`, Telegram-бот, backup-репо |
| Настройки | Сервер (не в git) | Ручное редактирование |
| Бэкапы | Сервер + backup-репо | Автоматически по cron |

---

## 🗄️ Backup System

### Требования:
- **Хранение:** 365 дневных + 52 недельных бэкапа (1 год)
- **Лимит:** ~400-500 МБ макс (с gzip сжатием)
- **Сжатие:** gzip (быстро, ~50% размер)
- **Автоматизация:** cron + Docker

### Скрипт backup_db.sh:
```bash
#!/bin/bash
# Ежедневный бэкап bot.db с gzip сжатием
# Хранение: 365 дней + 52 недели

BACKUP_DIR="./backups"
DATE=$(date +%Y-%m-%d)
DAY_OF_WEEK=$(date +%u)

mkdir -p "$BACKUP_DIR"

# Дневной бэкап
gzip -c bot.db > "$BACKUP_DIR/bot_daily_$DATE.db.gz"

# Недельный бэкап (по воскресеньям, day 7)
if [ "$DAY_OF_WEEK" -eq 7 ]; then
    gzip -c bot.db > "$BACKUP_DIR/bot_weekly_$DATE.db.gz"
fi

# Удаление старых бэкапов (>365 дней для daily, >52 недель для weekly)
find "$BACKUP_DIR" -name "bot_daily_*.db.gz" -mtime +365 -delete
find "$BACKUP_DIR" -name "bot_weekly_*.db.gz" -mtime +364 -delete

echo "Backup completed: $DATE"
```

### Автоматизация (cron):
```bash
# Редактирование crontab
crontab -e

# Добавить строку (ежедневно в 3:00)
0 3 * * * cd /home/andrjur/antbot4 && ./backup_db.sh

# Проверка cron
crontab -l
```

### Telegram-команда /backup:
- По команде `/backup` бот сжимает БД и отправляет файл админу
- Опционально: автоматическая отправка раз в неделю в админ-чат

### Backup-репозиторий (приватный):
```bash
# Создание
cd ~/antbot4
git clone git@github.com:yourusername/antbot4-backups.git

# Скрипт синхронизации (добавить в backup_db.sh после бэкапа)
cd ~/antbot4/antbot4-backups
cp ../backups/bot_daily_$DATE.db.gz .
git add bot_daily_$DATE.db.gz
git commit -m "Backup $DATE"
git push

# Очистка старых файлов в репо (раз в месяц)
git ls-files | grep -E "^bot_daily_" | head -n -30 | xargs git rm
git commit -m "Remove backups older than 30 days"
git push
```

**Важно:**
- Репозиторий должен быть **приватным**
- Использовать **Git LFS** если файлы >100 МБ
- Настроить SSH-ключ для автоматического push

---

## 🔧 Course ID Migration

### Проблема:
В БД course_id = `база`, `женственность15`, а в `settings.json` = `base`, `sprint2`

### Решение (на сервере):
```bash
# 1. Бэкап перед изменениями
./backup_db.sh

# 2. Обновление всех таблиц
sqlite3 bot.db <<EOF
UPDATE group_messages SET course_id = 'base' WHERE course_id = 'база';
UPDATE group_messages SET course_id = 'sprint2' WHERE course_id = 'женственность15';

UPDATE user_courses SET course_id = 'base' WHERE course_id = 'база';
UPDATE user_courses SET course_id = 'sprint2' WHERE course_id = 'женственность15';

UPDATE course_activation_codes SET course_id = 'base' WHERE course_id = 'база';
UPDATE course_activation_codes SET course_id = 'sprint2' WHERE course_id = 'женственность15';

UPDATE course_versions SET course_id = 'base' WHERE course_id = 'база';
UPDATE course_versions SET course_id = 'sprint2' WHERE course_id = 'женственность15';

UPDATE courses SET course_id = 'base', title = 'base' WHERE course_id = 'база';
UPDATE courses SET course_id = 'sprint2', title = 'sprint2' WHERE course_id = 'женственность15';

UPDATE user_actions_log SET course_id = 'base' WHERE course_id = 'база';
UPDATE user_actions_log SET course_id = 'sprint2' WHERE course_id = 'женственность15';
.quit
EOF

---

## ⚡ Admin Test Mode

### Проблема:
Админам нужно быстро тестировать курсы, ожидая 12 часов между уроками неудобно.

### Решение:
Команда `/test_mode` для суперадминов:
- Включает интервал 5 минут вместо 12 часов
- Работает ТОЛЬКО для ADMIN_IDS (суперадмины)
- Статус показывается в главном меню: `⚡[ТЕСТ]` и `⏳ Интервал: 5 мин ⚡`
- Обычные пользователи всегда в обычном режиме (12 часов)

### Как использовать:
```
# Включить
/test_mode

# Выключить
/test_mode
```

### Индикация в меню:
```
🎓 Курс: base ⚡[ТЕСТ]
🔑 Тариф: с проверкой
📖 Урок: 1 из 777
🥇 Уровень: 1
⏳ Интервал: 5 мин ⚡
📝 Домашка: не требуется
🕒 Следующий урок: ...
```

### Кнопка возврата в начало:
Добавлена кнопка `🔙 /start - В главное меню` в главное меню курса.

---

## 📊 Issues & Solutions

### 1. Статистика пропала из меню
**Проблема:** В главном меню не показывалась статистика (урок X из Y)

**Решение:** 
- Проверить `send_main_menu()` — статистика формируется в `base_text_lines`
- Убедиться что `total_lessons_on_level` считается корректно
- Добавить `test_mode_badge` для админов

### 2. /test_mode не работал
**Проблема:** Команда попадала в `default_handler`

**Решение:** 
- Убрать декоратор `F.from_user.id.in_(ADMIN_IDS_CONF)`
- Проверка внутри функции: `if user_id not in ADMIN_IDS_CONF: return`

### 3. settings.json перезаписывался
**Проблема:** При рестарте бота файл затирался

**Решение:**
- `update_settings_file()` теперь ТОЛЬКО добавляет новые курсы
- НЕ создаёт новый файл автоматически
- НЕ затирает существующие поля

---

# 3. Проверка
sqlite3 bot.db "SELECT course_id, COUNT(*) FROM group_messages GROUP BY course_id;"
# Ожидается: base|17, sprint2|44

# 4. Перезапуск бота
docker-compose restart bot
```

### Prevention:
- При создании нового курса сразу использовать латинские course_id
- Проверять соответствие перед деплоем
