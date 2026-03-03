# GOALS2.md — Цели и задачи проекта AntBot

## 🎯 Основные цели

1. **Бот для онлайн-курсов** — автоматизация выдачи уроков, проверки ДЗ, отслеживания прогресса
2. **Многопользовательский** — поддержка множества студентов с индивидуальным прогрессом
3. **Гибкая тарификация** — несколько тарифов на курс (Solo, Coach, VIP)
4. **AI-проверка ДЗ** — интеграция с n8n для автоматической проверки домашних заданий
5. **Мониторинг** — Prometheus + Grafana для метрик и Alertmanager для уведомлений

---

## 📁 Критически важные файлы (НЕ терять!)

### Данные (хранятся на хосте, монтируются в контейнер)
- `bot.db` — база данных SQLite (пользователи, курсы, прогресс, ДЗ)
- `settings.json` — настройки бота (курсы, коды активации, тарифы)
- `settings.json1` — предыдущая версия настроек (бэкап)
- `settings.json2` — позапрошлая версия настроек (бэкап)

### Конфигурация
- `.env` — переменные окружения (токен бота, админы, webhook URL)
- `docker-compose.yml` — оркестрация контейнеров
- `Dockerfile` — сборка образа бота
- `.dockerignore` — исключает файлы из Docker-сборки (защита данных)

### Скрипты БД
- `merge_spring31_to_spring3.sql` — слияние курсов
- `sync_db_with_settings.sql` — синхронизация БД с settings.json

---

## 🐳 Docker: защита данных

### .dockerignore защищает:
- `bot.db` — не копируется в образ, хранится только на хосте
- `settings.json` — не копируется в образ, хранится только на хосте
- `settings.json1`, `settings.json2` — бэкапы настроек
- `logs/` — логи не нужны в образе

### volumes в docker-compose.yml:
```yaml
volumes:
  - ./bot.db:/app/bot.db
  - ./settings.json:/app/settings.json
  - ./logs:/app/logs
  - ./VERSION:/app/VERSION
```

### Безопасные команды:
```bash
docker compose up -d              # Запуск (данные сохраняются)
docker compose up -d --build      # Пересборка (данные сохраняются)
docker compose restart bot        # Перезапуск (данные сохраняются)
docker compose down               # Остановка (данные сохраняются)
```

### ⚠️ Опасные команды:
```bash
docker compose down -v            # УДАЛЯЕТ volumes! (данные теряются)
docker rm -f antbot && docker volume prune  # УДАЛЯЕТ данные!
```

---

## 🔄 Бэкапы

### Автоматические бэкапы:
- При удалении курса → ротация `settings.json → settings.json1 → settings.json2`
- При добавлении курса → бэкап `settings_YYYY-MM-DD_HH-MM-SS.json`

### Ручные бэкапы:
```bash
# Бэкап базы данных
cp bot.db bot.db.backup.$(date +%Y%m%d_%H%M%S)

# Бэкап настроек
cp settings.json settings.json.backup.$(date +%Y%m%d_%H%M%S)

# Полный бэкап проекта
tar -czf antbot-backup-$(date +%Y%m%d).tar.gz \
    bot.db settings.json .env docker-compose.yml
```

---

## 🛠 Админ-команды бота

```
/show_codes — курсы и коды активации
/add_course — создать новый курс
/upload_lesson — загрузить уроки
/list_lessons — список уроков курса
/list_admins — список админов
/set_hw_timeout <мин> — таймаут AI-проверки
/export_db — экспорт базы данных
/import_db — импорт базы данных
/remind <id> <msg> — напоминание пользователю
/test_mode — тест-режим (12 часов)
```

---

## 📊 Структура базы данных

### Основные таблицы:
- `users` — пользователи бота
- `courses` — курсы (course_id, title, group_id, description)
- `course_versions` — тарифы курсов (v1, v2, v3)
- `user_courses` — активации курсов пользователями
- `group_messages` — уроки и материалы курсов
- `course_activation_codes` — коды активации
- `homework` — домашние задания на проверке
- `homework_gallery` — выполненные ДЗ
- `admin_context` — контекст администратора
- `user_states` — состояния FSM
- `admin_logs` — лог действий админа

---

## 🎓 Типовой рабочий процесс

### Добавление нового курса:
1. `/add_course` → указать group_id, коды активации
2. `/upload_lesson` → загрузить урок 0 (описание курса)
3. `/upload_lesson` → загрузить уроки 1, 2, 3...
4. Проверить `/show_codes` → коды активации работают

### Обновление бота:
```bash
git pull
docker compose up -d --build bot
```

### Синхронизация БД с settings.json:
1. Отредактировать `sync_db_with_settings.sql` → добавить актуальные курсы
2. Запустить: `sqlite3 bot.db < sync_db_with_settings.sql`
3. Перезапустить бота: `docker compose restart bot`

---

## 📝 Заметки

- **settings.json** — единственный источник истины для курсов и кодов
- **bot.db** — хранит прогресс пользователей и уроки
- При рассинхронизации settings.json и bot.db → использовать `sync_db_with_settings.sql`
- При слиянии курсов → использовать `merge_spring31_to_spring3.sql`
- Все изменения в БД делаются в транзакциях
- Логи пишутся в `logs/` и выводятся в stdout
