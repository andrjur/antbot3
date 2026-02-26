# AntBot v4 - CLAUDE.md
## Руководство для AI-агента (актуально на 2026-02-26)

---

## 1. Что такое этот проект

Telegram-бот для продажи и доставки образовательных курсов.
- Рассылает уроки по расписанию
- Принимает и проверяет домашние задания (вручную через админов + AI через n8n)
- Мультикурсовая система с тарифами
- Активация курсов по кодовым словам

**Стек:** Python 3.12 / aiogram 3.19 / aiosqlite / aiohttp / n8n / Prometheus / Docker

---

## 2. Критические правила (НЕ НАРУШАТЬ)

1. **НИКОГДА не менять схему БД** — только добавлять. Никаких ALTER TABLE.
2. **settings.json в .gitignore** — не перезаписывать, не коммитить.
3. **parse_mode=None везде** — Markdown V2 ломает сообщения.
4. **Суперадмины из ADMIN_IDS (.env)** — не хардкодить ID в коде.
5. **bot.db смонтирован как Docker volume** — не удаляется при `docker-compose down`.
6. **ВСЕГДА пушить на antbot4**: `git push antbot4 <ветка>` (НЕ origin — это antbot3!)
7. **Текущая рабочая ветка**: `fix/bug1-course-id-hw-status`

---

## 3. Инфраструктура

| Сервис | Адрес | Порт |
|--------|-------|------|
| Бот | `https://bot.indikov.ru` | 8080 |
| n8n | `https://n8n.indikov.ru` | 5678 |
| Prometheus | `http://<server>:9090` | 9090 |
| Grafana | `http://<server>:3000` | 3000 |
| Alertmanager | `http://<server>:9093` | 9093 |

**Git remotes:**
- `antbot4` → `https://github.com/andrjur/antbot4.git` (правильный!)
- `origin` → antbot3 (не использовать для push)

**Сервер:** `andrjur@etppjmdtxr` (alwaysdata)

---

## 4. Архитектура файлов

```
main.py (~9800 строк) - весь бот в одном файле
settings.json         - курсы, коды активации, тарифы (НЕ в git)
bot.db                - SQLite база данных (Docker volume)
.env                  - переменные окружения (НЕ в git)
n8n.json              - n8n workflow (для импорта в n8n UI)
README.md             - инструкция по развёртыванию
services/
  metrics.py          - Prometheus метрики
  monitoring.py       - /metrics эндпоинт
  health.py           - /health/live, /health/ready
```

### Таблицы БД

| Таблица | Описание |
|---------|----------|
| `users` | Telegram пользователи |
| `user_courses` | Прогресс: lesson_num, hw_status, is_active |
| `group_messages` | Контент уроков из Telegram-каналов |
| `pending_admin_homework` | ДЗ в очереди на проверку |
| `admin_context` | Контекст для поддержки |
| `course_activation_codes` | Коды → курс + тариф |

### hw_status значения
`none` → `pending` → `approved` / `rejected`

---

## 5. Ключевые функции

| Функция | Строки (прим.) | Описание |
|---------|--------|----------|
| `run_hw_countdown()` | ~1166 | Обратный отсчёт на карточке ДЗ каждые 22 сек |
| `check_pending_homework_timeout()` | ~1210 | Фоновый цикл: отправляет ДЗ в n8n после таймаута |
| `send_data_to_n8n()` | ~1735 | POST в n8n с заголовком Authorization |
| `handle_n8n_hw_approval()` | ~1834 | Callback от n8n (результат AI-проверки) |
| `handle_homework()` | ~8250 | Приём ДЗ от студента |
| `on_startup()` | ~9439 | Старт бота |

---

## 6. n8n интеграция

**Webhook (бот → n8n):**
- URL: `https://n8n.indikov.ru/webhook/aa46a723-619e-42e9-8e51-49ba51813718`
- Auth header: `Authorization: <N8N_WEBHOOK_SECRET>`
- Модель: `google/gemini-2.5-flash-lite` через OpenRouter

**Callback (n8n → бот):**
- URL строится из `BOT_INTERNAL_URL` (если задан) иначе `WEBHOOK_HOST`
- **Рекомендуется:** `BOT_INTERNAL_URL=http://bot:8080` — напрямую через Docker, без Cloudflare
- Header: `X-CALLBACK-SIGNATURE: <N8N_CALLBACK_SECRET>`
- Тело: `{student_user_id, course_numeric_id, lesson_num, is_approved, feedback_text, original_admin_message_id}`

---

## 7. Текущие проблемы

| # | Проблема | Статус |
|---|----------|--------|
| 1 | **n8n callback Error 521** — n8n стучится на `https://bot.indikov.ru` через Cloudflare, Cloudflare не достигает контейнер | Исправлено в коде: добавлена переменная `BOT_INTERNAL_URL`. Нужно добавить в .env на сервере: `BOT_INTERNAL_URL=http://bot:8080` |
| 2 | **Alertmanager** `chat not found (400)` — неверный `ALERT_CHAT_ID` | Исправить в .env на сервере: `ALERT_CHAT_ID=-1002591981307` |
| 3 | **Кнопки исчезали** при обратном отсчёте | Исправлено (commit b362a91): `reply_markup` передаётся в `run_hw_countdown` |

---

## 8. Деплой

```bash
# На сервере
git pull
sudo docker compose up -d --build bot

# Логи
sudo docker compose logs bot --tail=50 -f

# После правок .env — перезапуск без ребилда
sudo docker compose restart bot
```

**Что добавить в .env на сервере:**
```
BOT_INTERNAL_URL=http://bot:8080
ALERT_CHAT_ID=-1002591981307
```

---

## 9. Debug команды

```bash
sqlite3 bot.db "SELECT * FROM pending_admin_homework;"
sqlite3 bot.db "SELECT course_id, COUNT(*) FROM user_courses WHERE is_active=1 GROUP BY course_id;"
sudo docker compose logs bot -f --tail=50
```

---

## 10. Переменные окружения (.env) — ключевые

```
BOT_TOKEN=
ADMIN_IDS=182643037,954230772
ADMIN_GROUP_ID=-1002591981307

N8N_HOMEWORK_CHECK_URL=https://n8n.indikov.ru/webhook/aa46a723-619e-42e9-8e51-49ba51813718
N8N_WEBHOOK_SECRET=
N8N_CALLBACK_SECRET=500
BOT_INTERNAL_URL=http://bot:8080    # ← ВАЖНО для обхода Cloudflare

HW_TIMEOUT_SECONDS=120
WEBHOOK_HOST=https://bot.indikov.ru

ALERT_BOT_TOKEN=
ALERT_CHAT_ID=-1002591981307
```
