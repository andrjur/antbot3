# AntBot v4 — Контекст проекта (на 2026-03-01)

## О проекте

**Telegram-бот для онлайн-курсов** с интеграцией n8n для AI-проверки домашнихних заданий.

**Репозиторий:** https://github.com/andrjur/antbot4

**Основной файл:** `c:\Clau\main.py`

---

## Основная функциональность

### Для студентов:
- Активация курсов через коды активации
- Просмотр уроков (текст + медиа)
- Отправка ДЗ (текст/фото/видео/документ)
- Команды: `/start`, `/activate`, `/mycourses`, `/lesson`, `/homework`, `/support`, `/progress`
- Таймзоны для планирования уроков
- Главное меню с навигацией по курсам

### Для админов:
- Админ-панель в отдельной группе (`ADMIN_GROUP_ID`)
- Кнопки "Принять/Отклонить" для ДЗ
- Команды: `/approve`, `/reject`, `/export_db`, `/import_db`, `/course_desc`, `/edit_desc`
- Получение статистики по курсам

### Интеграция с n8n:
- Вебхук при отправке ДЗ студентом
- AI-агент (OpenRouter + Qwen 3.5 Flash) анализирует ДЗ
- Callback обратно в бота с результатом (`is_approved`, `feedback_text`)

---

## Архитектура

### База данных (SQLite):
- `users` — пользователи
- `courses` — курсы (course_id, description, group_id)
- `user_courses` — привязка студентов к курсам (status, current_lesson, version_id, hw_status)
- `course_activation_codes` — коды активации
- `group_messages` — контент уроков (текст, фото, видео)
- `pending_admin_homework` — ДЗ в ожидании проверки админом/n8n
- `homework_gallery` — сданные ДЗ
- `admin_context` — контекст для админских кнопок
- `user_states` — состояния FSM

### Ключевые функции:
- `handle_homework()` — приём ДЗ от студента, отправка в админ-группу, запуск таймера
- `check_pending_homework_timeout()` — цикл 60 сек, отправка вебхука в n8n по таймеру
- `run_hw_countdown()` — обновление таймера в админ-группе (каждые 22 сек)
- `handle_n8n_hw_approval()` — вебхук от n8n с результатом AI-проверки
- `activate_course()` — активация курса по коду
- `send_main_menu()` — главное меню с кнопками

### Настройки (`.env`):
```
BOT_TOKEN=...
BOT_INTERNAL_URL=http://bot:8080
WEBHOOK_HOST_CONF=https://bot.indikov.ru
WEBHOOK_SECRET_PATH=<secret>
ADMIN_GROUP_ID=-1002591981307
N8N_WEBHOOK_SECRET=...
HW_TIMEOUT_SECONDS=120
```

### Docker:
- `docker-compose.yml` — бот + n8n + PostgreSQL (для n8n)
- `bot.db` — том для SQLite

---

## Последнее исправление (2026-03-01)

### Проблема с обработкой фото в n8n

**Диагноз:** n8n не обрабатывал фотографии от студентов, потому что:
1. Нода `Get a file` использовала неправильное имя переменной (`student_homework_file_id` вместо `hw_file_id`)
2. Промпт ИИ не получал тег о наличии фото

**Решение (исправлено в `n8n-flow.json`):**

1. **Нода `Get a file`:**
   ```javascript
   // Было:
   fileId: "={{ $('Edit Fields').item.json.student_homework_file_id }}"
   
   // Стало:
   fileId: "={{ $('Edit Fields').item.json.hw_file_id }}"
   ```

2. **Нода `Agent` (промпт):**
   ```javascript
   // Было:
   {{ $("Merge").first() && $("Merge").first().json.data ? "[ПРИКРЕПЛЕНО ИЗОБРАЖЕНИЕ]" : "" }}
   
   // Стало:
   {{ $('Edit Fields').item.json.hw_file_id ? "[СТУДЕНТ ПРИКРЕПИЛ ФОТОГРАФИЮ СВОЕЙ КОМНАТЫ]" : "" }}
   ```

3. **Добавлено правило для ИИ:**
   > Если в работе есть текст "[СТУДЕНТ ПРИКРЕПИЛ ФОТОГРАФИЮ СВОЕЙ КОМНАТЫ]", значит он успешно выполнил визуальную часть задания. Обязательно похвали его за присланное фото.

**Файл:** `c:\Clau\n8n-flow.json`

---

## История багов (из GOALS.md)

### Bug 1 (2026-02-25): wrong course_id
`get_user_course_data()` возвращала случайный курс при нескольких активных записях.  
**Фикс:** Добавлен `ORDER BY activation_date DESC LIMIT 1`

### Bug 2 (2026-02-25): hw_status race condition
`deactivate_course()` не удаляла `pending_admin_homework` → старое ДЗ улетало в n8n после активации нового курса.  
**Фикс:** Удаление pending ДЗ перед деактивацией.

### zadacha1 (2026-02-26): JSON payload mismatch
Бот и n8n использовали разные имена полей.  
**Фикс:** Унификация payload (`user_fullname`, `original_admin_message_id`, `homework_text`, `homework_content_type`).

### zadacha2 (2026-02-26): Таймер в админ-группе
По истечении таймера кнопки не убирались → админ мог нажать параллельно с ИИ.  
**Фикс:** При `remaining <= 0` ставить `reply_markup=None`.

---

## Файлы проекта

| Файл | Описание |
|------|----------|
| `main.py` | Основной код бота (~9000 строк) |
| `n8n-flow.json` | Воркфлоу n8n для AI-проверки ДЗ |
| `GOALS.md` | История решений и багов |
| `course_description_management.md` | Spec для управления описанием курсов |
| `settings.json.example` | Шаблон настроек |
| `docker-compose.yml` | Docker-конфигурация |
| `tests/` | Тесты |

---

## Текущее состояние

✅ Бот работает стабильно  
✅ Интеграция с n8n настроена  
✅ Исправлен баг с передачей `hw_file_id` в n8n  
⏳ Требуется деплой на сервер

### Следующие шаги:
1. Импортировать обновлённый `n8n-flow.json` в n8n
2. Протестировать отправку фото через бот
3. Проверить, что ИИ хвалит за фото

---

## Контакты

- GitHub: https://github.com/andrjur/antbot4
- Админ-группа Telegram: `-1002591981307`
