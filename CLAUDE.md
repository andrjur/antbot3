# AntBot v4 — Контекст для разработки

## 🎯 Текущая задача (01.03.2026)

**Мультимодальная проверка ДЗ через n8n + OpenRouter API**

### Что сделано:
- ✅ Воркфлоу n8n-flow.json переписан с Agent на прямой HTTP Request
- ✅ Поддержка: текст + фото (Base64) + аудио/видео (input_audio)
- ✅ Модель: google/gemini-2.5-flash (мультимодальная)
- ✅ Обработка ошибок: 3 уровня + fallback (авто-одобрение)
- ✅ Логирование: 📸🎧🎥 эмодзи для типов файлов

### Текущие проблемы:
- ⚠️ n8n не возвращает callback в бота (ДЗ #1043 ушло в таймаут)
- ⚠️ Конфликт вебхуков в n8n (дублирующийся path)
- ⚠️ Process Audio/Video не сохраняли binary data — **ИСПРАВЛЕНО**
- ⚠️ specifyBody: "useJson" ломал экспорт — **ИСПРАВЛЕНО** (теперь bodyParameters)

### План на сейчас:
1. **Тестирование** — проверить все 4 сценария:
   - Текст → ИИ → ответ
   - Фото → Base64 → Gemini Vision → ответ
   - Аудио → input_audio → Gemini Audio → ответ
   - Видео (кружочек) → input_audio → ответ

2. **Рефакторинг main.py** — вынести стабильные функции в отдельные файлы:
   - `services/n8n_integration.py` — отправка/получение от n8n
   - `services/homework_processing.py` — handle_homework_result + логика
   - `utils/formatters.py` — format_time_duration, escape_md
   - `utils/karma.py` — award_karma_points
   - `db/queries.py` — SQL запросы к БД
   - `handlers/admin.py` — админ команды
   - `handlers/student.py` — студент команды
   - `services/lesson_scheduler.py` — отправка уроков по таймеру
   - `services/metrics.py` — уже есть
   - `services/monitoring.py` — уже есть
   - `services/health.py` — уже есть

3. **Документация** — обновить README с инструкцией по настройке n8n

---

## 📁 Структура проекта

```
c:\Clau/
├── main.py                 # Основной файл бота (10k+ строк, требует рефакторинга)
├── n8n-flow.json          # Воркфлоу мультимодальной проверки ДЗ
├── n8n-text-flow.json     # Старый воркфлоу (только текст)
├── docker-compose.yml     # Docker конфигурация
├── .env                   # Переменные окружения
├── bot.db                 # SQLite база данных
├── settings.json          # Настройки курсов
├── GOALS2.md              # История решений и багов
├── CLAUDE.md              # Этот файл (контекст разработки)
├── services/
│   ├── metrics.py         # Prometheus метрики
│   ├── monitoring.py      # Эндпоинты мониторинга
│   ├── health.py          # Health checks
│   └── n8n_integration.py # [TODO] Интеграция с n8n
├── utils/
│   ├── formatters.py      # [TODO] Форматирование текста
│   └── karma.py           # [TODO] Система кармы
├── db/
│   └── queries.py         # [TODO] SQL запросы
└── handlers/
    ├── admin.py           # [TODO] Админ команды
    └── student.py         # [TODO] Студент команды
```

---

## 🔧 Ключевые конфигурации

### Docker (внутренняя сеть)
- Бот: `http://bot:8080` (внутри Docker), `https://bot.indikov.ru` (внешний)
- n8n: `http://n8n:5678` (внутри Docker), `https://n8n.indikov.ru` (внешний)
- **Важно:** Cloudflare не нужен для internal calls (бот ↔ n8n внутри Docker)

### Переменные окружения (.env)
```bash
# Бот
BOT_TOKEN=7473862113:AAH...
WEBHOOK_MODE=false  # Polling режим
BOT_INTERNAL_URL=http://bot:8080  # Для n8n callback
N8N_HOMEWORK_CHECK_URL=https://n8n.indikov.ru/webhook/aa46a723-619e-42e9-8e51-49ba51813718
N8N_CALLBACK_SECRET=500

# n8n
N8N_HOST=n8n.indikov.ru
N8N_PROTOCOL=https
N8N_TRUST_PROXY=true  # Для Cloudflare
WEBHOOK_URL=https://n8n.indikov.ru/
```

### n8n воркфлоу — ключевые ноды:
1. **Webhook-homework** — принимает от бота
2. **Get a file** — скачивает фото/аудио из Telegram
3. **Process Photo/Audio/Video** — подготовка данных
4. **OpenRouter AI** — HTTP Request к Gemini 2.5 Flash
5. **Parse JSON Response** — парсинг + fallback
6. **HTTP Request** — отправка результата обратно в бот

---

## 🧪 Тестирование

### Быстрый тест (текст):
```bash
# 1. Студент отправляет текст ДЗ
# 2. Ждёт 34 сек (HW_TIMEOUT_SECONDS)
# 3. Бот отправляет в n8n → ИИ → ответ
# 4. Студент получает вердикт

# Логи:
docker compose logs -f bot | grep -E "📤|n8n|callback"
docker compose logs -f n8n | grep -E "🤔|✅|❌"
```

### Тест с фото:
```bash
# 1. Студент: фото + "Выполнил"
# 2. n8n: Process Photo → Base64 → OpenRouter
# 3. Gemini видит фото → ответ
# 4. Бот: отправляет студенту + админу

# Проверка:
docker compose logs n8n | grep "📸 Фото"
```

### Тест с аудио:
```bash
# 1. Студент: голосовое + текст
# 2. n8n: Process Audio → input_audio → Gemini
# 3. Gemini "слушает" → ответ
# 4. Бот: вердикт

# Проверка:
docker compose logs n8n | grep "🎧 Аудио"
```

### Ручной тест webhook:
```bash
curl -X POST https://bot.indikov.ru/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL/n8n_hw_result \
  -H "Content-Type: application/json" \
  -H "X-CALLBACK-SIGNATURE: 500" \
  -d '{
    "feedback_text": "Тестовый ответ",
    "is_approved": true,
    "student_user_id": 123,
    "original_admin_message_id": 456,
    "course_numeric_id": 1,
    "lesson_num": 1
  }'
```

---

## 📝 Известные проблемы

### 1. Конфликт вебхуков n8n
```
There is a conflict with one of the webhooks.
```
**Решение:** Деактивировать старый воркфлоу или удалить дубль webhook path.

### 2. n8n не возвращает callback
**Симптом:** ДЗ уходит в авто-одобрение через 104 сек.

**Причины:**
- OpenRouter API недоступен (баланс = 0)
- Таймаут 60 сек превышен
- Ошибка парсинга JSON

**Диагностика:**
```bash
docker compose logs n8n | grep -E "❌|Error|OpenRouter"
```

### 3. database is locked
**Симптом:** Бот падает при записи в БД.

**Решение:**
```bash
# Найти блокирующий процесс:
sqlite3 bot.db "SELECT * FROM sqlite_master WHERE type='lock';"

# Или просто перезапустить бота:
docker compose restart bot
```

---

## 🚀 Деплой

### Обновление на сервере:
```bash
cd ~/antbot4

# 1. Забрать изменения
git pull origin main

# 2. Пересобрать контейнеры
docker compose down
docker compose up -d --build

# 3. Проверить логи
docker compose logs -f bot n8n
```

### Импорт воркфлоу n8n:
1. n8n → Settings → Import From File
2. Выбрать `n8n-flow.json`
3. Проверить credentials:
   - OpenRouter account (API ключ)
   - N8N_WEBHOOK_SECRE (500)
   - Antbot_api (Telegram токен)
4. Активировать воркфлоу

---

## 📚 Ссылки

- **OpenRouter API:** https://openrouter.ai/docs
- **Gemini 2.5 Flash:** https://cloud.google.com/vertex-ai/docs/generative-ai/model-reference/gemini
- **n8n HTTP Request:** https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest/
- **Telegram Bot API:** https://core.telegram.org/bots/api

---

**Последнее обновление:** 01.03.2026
**Статус:** 🟡 Тестирование мультимодального воркфлоу
