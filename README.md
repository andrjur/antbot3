# AntBot v4 — Руководство по развёртыванию

Telegram-бот для продажи и доставки образовательных курсов (aiogram 3 / aiosqlite / n8n / Docker).

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

Файл не в git. Содержит курсы, коды активации, тарифы. Структура:

```json
{
  "courses": {
    "sprint2": {
      "title": "Название курса",
      "versions": {
        "v1": { "price": 0, "activation_codes": ["CODE1"] }
      }
    }
  }
}
```

### 4. Запустить

```bash
sudo docker compose up -d --build
```

---

## Настройка n8n

### Шаг 1: Импортировать workflow

1. Открыть `https://n8n.yourdomain.ru`
2. Workflows → Import → загрузить `n8n.json`

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

---

## Архитектура

```
Студент → Telegram → Bot (main.py)
                       ├─ Сохраняет ДЗ в pending_admin_homework
                       ├─ Отправляет карточку в ADMIN_GROUP с кнопками
                       └─ Запускает обратный отсчёт (run_hw_countdown)

  Если таймаут истёк:
    Bot → n8n webhook → AI (Gemini) → callback → Bot
                                                   └─ обновляет hw_status
```

### hw_status жизненный цикл
`none` → `pending` → `approved` / `rejected`

---

## Обслуживание

```bash
# Логи
sudo docker compose logs bot -f --tail=50

# Перезапуск (без пересборки)
sudo docker compose restart bot

# Пересборка (после изменений в main.py)
sudo docker compose up -d --build bot

# Проверить pending ДЗ
sqlite3 bot.db "SELECT * FROM pending_admin_homework;"

# Активные студенты по курсам
sqlite3 bot.db "SELECT course_id, COUNT(*) FROM user_courses WHERE is_active=1 GROUP BY course_id;"
```

## Git

```bash
# Правильный remote для push
git push antbot4 main

# НЕ использовать origin — это antbot3!
```

---

## Известные проблемы

| Проблема | Причина | Решение |
|----------|---------|---------|
| n8n callback Error 521 | Трафик идёт через Cloudflare | Задать `BOT_INTERNAL_URL=http://bot:8080` в .env |
| Alertmanager `chat not found` | Неверный `ALERT_CHAT_ID` | Задать `ALERT_CHAT_ID=<ADMIN_GROUP_ID>` |
| Кнопки исчезают при отсчёте | edit_message без reply_markup | Исправлено в коде (передаём keyboard) |
