# AntBot v4 — Руководство по развёртыванию

Telegram-бот для продажи и доставки образовательных курсов (aiogram 3 / aiosqlite / n8n / Docker).

**Версия:** 4.0 (март 2026)

---

## 📖 Оглавление

1. [Быстрый старт](#быстрый-старт)
2. [Установка с нуля (рекомендуется)](#установка-с-нуля-рекомендуется)
3. [Настройка n8n](#настройка-n8n)
4. [Админ-команды](#админ-команды)
5. [Бэкапы и восстановление](#бэкапы-и-восстановление)
6. [Обслуживание](#обслуживание)
7. [Известные проблемы](#известные-проблемы)

---

## Быстрый старт

### 1. Клонировать репозиторий

```bash
git clone https://github.com/andrjur/antbot4.git
cd antbot4
```

### 2. Создать .env

```env
# Telegram
BOT_TOKEN=<токен бота>
ADMIN_IDS=182643037,954230772        # Суперадмины (через запятую)
ADMIN_GROUP_ID=-1002591981307        # Группа для карточек ДЗ

# Webhook
WEBHOOK_HOST=https://bot.yourdomain.ru
WEBHOOK_PATH=/bot/
WEB_SERVER_PORT=8080
WEBAPP_HOST=::

# n8n интеграция
N8N_HOMEWORK_CHECK_URL=https://n8n.yourdomain.ru/webhook/aa46a723-619e-42e9-8e51-49ba51813718
N8N_WEBHOOK_SECRET=<секрет для Authorization заголовка>
N8N_CALLBACK_SECRET=<секрет от n8n в бот, например 500>
BOT_INTERNAL_URL=http://bot:8080    # Внутренний Docker URL (без Cloudflare!)

# Таймаут ДЗ
HW_TIMEOUT_SECONDS=120

# Alertmanager
ALERT_BOT_TOKEN=<токен бота для алертов>
ALERT_CHAT_ID=-1002591981307        # = ADMIN_GROUP_ID
```

### 3. Создать settings.json

Файл не в git. Содержит курсы, коды активации, тарифы:

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
    "-1003710956962": "sprint2"
  },
  "activation_codes": {
    "b1": {"course": "base", "version": "v1", "price": 5000},
    "b22": {"course": "base", "version": "v2", "price": 7000}
  },
  "course_descriptions": {},
  "courses": {}
}
```

### 4. Запустить

```bash
sudo docker compose up -d --build
```

### 5. Проверить

```bash
docker compose ps
docker compose logs -f bot
```

---

## Установка с нуля (рекомендуется)

### Сценарий: Разворачиваешь бота на новом сервере

**1. Установи Docker и Docker Compose:**

```bash
# Ubuntu/Debian
curl -fsSL https://get.docker.com | sh
usermod -aG docker $USER
```

**2. Клонируй репозиторий:**

```bash
git clone https://github.com/andrjur/antbot4.git
cd antbot4
```

**3. Настрой .env:**

```bash
cp .env.example .env
nano .env  # Отредактируй BOT_TOKEN, ADMIN_IDS и т.д.
```

**4. Запусти бота:**

```bash
docker compose up -d --build
```

**5. Импортируй базу данных (если есть бэкап):**

```bash
# Если у тебя есть database_export.json со старого сервера:
# Отправь файл боту в Telegram

# В Telegram боту:
/import_db

# Бот попросит отправить JSON-файл
# Отправь database_export.json

# ⚠️ ВАЖНО: /import_db полностью заменяет БД!
# Используй ТОЛЬКО при установке с нуля или для полного восстановления.
```

**6. Альтернативно — безопасное восстановление:**

```bash
# Если нужно восстановить курсы/уроки, но сохранить студентов:
/import_db_safe

# Этот команда:
# ✅ Восстановит курсы, тарифы, уроки, коды
# ✅ Сохранит студентов и их прогресс
```

**7. Проверь что всё работает:**

```bash
# В Telegram боту:
/show_codes      # Проверить курсы и коды
/list_admins     # Проверить админов
```

---

## Настройка n8n

### Шаг 1: Импортировать workflow

**Актуальный файл:** `n8n_flow (5) итоговый.json`

1. Открыть `https://n8n.yourdomain.ru`
2. Workflows → Import → загрузить `n8n_flow (5) итоговый.json`
3. Сохранить и активировать (переключатель Active)

### Шаг 2: Настроить credentials

**OpenRouter (для AI):**
- Тип: OpenRouter API
- API Key: ключ от openrouter.ai

**Webhook auth (входящий от бота):**
- Тип: Header Auth
- Header Name: `Authorization`
- Header Value: то же значение что в `N8N_WEBHOOK_SECRET` в .env

**Callback auth (исходящий в бот):**
- Тип: Header Auth
- Header Name: `X-CALLBACK-SIGNATURE`
- Header Value: то же значение что в `N8N_CALLBACK_SECRET` в .env

### Шаг 3: URL callback

n8n отправляет результат на URL, который ему передаёт бот в поле `callback_webhook_url_result`.
Бот строит этот URL из `BOT_INTERNAL_URL` (если задан) или `WEBHOOK_HOST`.

**Рекомендуется:** задать `BOT_INTERNAL_URL=http://bot:8080` в .env —
тогда n8n стучится через внутреннюю Docker-сеть, минуя Cloudflare (нет ошибки 521).

### Шаг 4: Проверить

1. Отправь боту ДЗ (текст или фото)
2. Через 15 сек бот отправит в n8n
3. Через 5-10 сек n8n вернёт результат (approved/rejected)
4. Бот покажет фидбек студенту

---

## Админ-команды

### Управление курсами

| Команда | Описание |
|---------|----------|
| `/show_codes` | Показать курсы и коды активации |
| `/add_course` | Создать новый курс |
| `/upload_lesson` | Загрузить урок (0 = описание, 1+ = уроки) |
| `/list_lessons` | Список уроков курса |
| `/edit_course_description` | Редактировать описание курса |

### Просмотр данных

| Команда | Описание |
|---------|----------|
| `/list_admins` | Список админов |
| `/view_homework [курс]` | Просмотр выполненных ДЗ (последние 50) |
| `/mycourses` | Мои активные курсы |
| `/progress` | Мой прогресс |

### Бэкапы и импорт

| Команда | Описание | Когда использовать |
|---------|----------|-------------------|
| `/export_db` | Экспорт базы в JSON (сохраняет в `backups/`) | Регулярно, перед изменениями |
| `/import_db` | **Полный импорт базы** (⚠️ ОПАСНО!) | **Только при установке с нуля!** |
| `/import_db_safe` | Безопасный импорт (сохраняет студентов) | Для восстановления курсов/уроков |

### Настройки

| Команда | Описание |
|---------|----------|
| `/set_hw_timeout <мин>` | Таймаут AI-проверки ДЗ |
| `/remind <id> <msg>` | Напоминание пользователю |
| `/test_mode` | Тест-режим (12 часов) |
| `/cleanup_courses` | Очистка удалённых курсов |

---

## Бэкапы и восстановление

### Автоматические бэкапы

- При `/export_db` → `backups/database_export_YYYYMMDD_HHMMSS.json`
- При удалении курса → ротация `settings.json → settings.json1 → settings.json2`

### Ручные бэкапы

```bash
# Бэкап базы данных
cp bot.db bot.db.backup.$(date +%Y%m%d_%H%M%S)

# Бэкап настроек
cp settings.json settings.json.backup.$(date +%Y%m%d_%H%M%S)

# Полный бэкап проекта
tar -czf antbot-backup-$(date +%Y%m%d).tar.gz \
    bot.db settings.json .env docker-compose.yml
```

### Восстановление из бэкапа

**Сценарий 1: Полное восстановление (при установке с нуля)**

```bash
# 1. Установить бота (см. "Установка с нуля")
# 2. В Telegram боту:
/import_db

# 3. Отправить файл database_export.json
# ✅ Восстановится ВСЁ: курсы, уроки, студенты, прогресс, ДЗ
```

**Сценарий 2: Безопасное восстановление (прод работает)**

```bash
# 1. В Telegram боту:
/import_db_safe

# 2. Отправить файл database_export.json
# ✅ Восстановятся: курсы, уроки, тарифы, коды
# ✅ Сохранятся: студенты, прогресс, выполненные ДЗ
```

**Сценарий 3: Восстановление базы из файла**

```bash
# Если бот не запускается:
sqlite3 bot.db < backup.sql

# Или через Python:
python3 -c "import json; ..."
```

---

## Обслуживание

### Логи

```bash
# Логи в реальном времени
docker compose logs -f bot

# Только ошибки
docker compose logs bot | grep ERROR

# Последние 100 строк
docker compose logs bot --tail=100

# Очистить логи
docker compose logs --tail=0
```

### Перезапуск

```bash
# Перезапуск без пересборки
docker compose restart bot

# Пересборка и перезапуск (после изменений в main.py)
docker compose up -d --build bot

# Полная пересборка всех сервисов
docker compose down
docker compose up -d --build
```

### Мониторинг

```bash
# Проверка состояния
docker compose ps

# Метрики Prometheus
curl http://localhost:8080/metrics

# Здоровье бота
curl http://localhost:8080/health/live
```

### Ротация логов

Настроена автоматически в `docker-compose.yml`:
- Максимум 10MB на файл
- Хранится 3 файла (итого 30MB)
- Старые логи удаляются

---

## Известные проблемы

| Проблема | Причина | Решение |
|----------|---------|---------|
| n8n callback Error 521 | Трафик идёт через Cloudflare | Задать `BOT_INTERNAL_URL=http://bot:8080` в .env |
| Alertmanager `Unauthorized (401)` | Неверный `ALERT_BOT_TOKEN` | Проверить токен в .env |
| `database is locked` | Гонки SQLite | Увеличен `busy_timeout=30000`, добавлены паузы |
| `chat not found` для курса | Группа удалена или бот кикнут | Проверить доступ: `/list_lessons` |
| Кружочки без текста | Telegram не поддерживает caption для video_note | Текст отправляется отдельным сообщением |

---

## Файловая структура

```
antbot4/
├── main.py                          # Основной код бота
├── docker-compose.yml               # Оркестрация контейнеров
├── Dockerfile                       # Сборка образа
├── .env                             # Переменные окружения (не в git!)
├── .env.example                     # Шаблон .env
├── settings.json                    # Настройки курсов (не в git!)
├── bot.db                           # База данных SQLite (не в git!)
├── backups/                         # Автоматические бэкапы
│   └── database_export_YYYYMMDD_HHMMSS.json
├── logs/                            # Логи (авеоматическая ротация)
├── n8n_flow (5) итоговый.json       # Актуальный workflow для n8n
├── README.md                        # Этот файл
├── CLAUDE.md                        # Документация для ИИ-ассистентов
├── GOALS2.md                        # Цели и задачи проекта
├── IMPORT_SAFETY.md                 # Безопасность импорта БД
└── LOGS_FIX.md                      # Настройка логирования
```

---

## Поддержка

- **GitHub:** https://github.com/andrjur/antbot4
- **Telegram:** @Andreyjurievich
- **Документация:** CLAUDE.md, GOALS2.md

---

## Лицензия

Проприетарное ПО. Все права защищены.
