# AntBot v4 - История решений и исправленных багов

Репозиторий: https://github.com/andrjur/antbot4

---

## Формат записи

```
### Краткое название
**Коммит:** [hash](https://github.com/andrjur/antbot4/commit/HASH)
**Дата:** YYYY-MM-DD
**Проблема:** что было не так
**Решение:** что сделали
```

---

## 2026-02-25 — Исправление bug1: wrong course_id + hw_status race condition

**Коммит:** *(этот коммит)*

### Bug 1: send_course_description получала course_id='base' вместо 'sprint2'

**Проблема:** В `handle_homework()` после активации курса SELECT для получения `course_id` не имел `WHERE status='active'`. При наличии старых неактивных записей в `user_courses` (например, курс 'base' с status='inactive') запрос возвращал не тот курс.

**Симптом из лога:**
```
10:55:46 activate_course() → course_id='sprint2'
10:55:46 send_course_description() → course_id_str='base'  ← НЕВЕРНО
```

**Решение:** Добавлен `AND status = 'active'` в SELECT в `handle_homework()` (строка ~8590):
```python
"SELECT course_id, version_id FROM user_courses WHERE user_id = ? AND status = 'active'"
```

---

### Bug 2: hw_status становился 'approved' через 4-5 секунд после активации

**Проблема:** При деактивации курса (`cb_stop_current_course`) функция `deactivate_course()` не удаляла записи из `pending_admin_homework`. После активации нового курса `check_pending_homework_timeout()` (цикл каждые 60 сек) находил старые pending ДЗ и отправлял их в n8n. n8n отвечал `is_approved=true`, что ставило hw_status='approved' только что активированному курсу.

**Симптом из лога:**
```
10:55:51 send_main_menu() → hw_status='pending'
10:55:55 handle_homework() → hw_status='approved'  ← за 4 секунды!
```

**Решение:** В `deactivate_course()` добавлено удаление `pending_admin_homework` для студента по курсу перед деактивацией.

---

### Fix 3: lru_cache на async функции (тихий баг)

**Проблема:** `@lru_cache(maxsize=100)` на `async def get_course_status()` — `lru_cache` кэшировал объект корутины, а не результат. Кэш не работал, при этом утекала память.

**Решение:** Удалён декоратор `@lru_cache`.

---

### Fix 4: cleanup_orphaned_courses убрана из автозапуска

**Проблема:** Функция вызывалась при каждом старте бота. При случайно пустом/неполном settings.json (ошибка деплоя) — снимала всех студентов с активных курсов без предупреждения.

**Решение:** Убрана из `main()`. Добавлена команда `/cleanup_orphaned` (только суперадмины, только в личке) с проверкой что valid_courses не пустой перед запуском.

---

### Fix 5: debug-логи (ЛОГ #0..#19, ЛОГ A1..A20) убраны из продакшна

**Проблема:** `send_course_description()` содержала `import traceback`, `traceback.format_stack()` и 20 пронумерованных `logger.info("ЛОГ #N")`. `handle_homework()` содержала ещё 20 ЛОГ A*. Забытый отладочный код спамил в логи и скрывал реальные проблемы.

**Решение:** Все отладочные логи удалены. Оставлены осмысленные `logger.info` и `logger.debug`.

---

### Fix 6: Раскомментирован guard hw_status='approved' в handle_homework

**Проблема:** Блок `if hw_status == 'approved': return` был закомментирован "для отладки" и никогда не раскомментирован. Бот принимал повторные ДЗ даже после уже одобренного.

**Решение:** Guard раскомментирован и работает: если `hw_status='approved'` — отвечаем "Домашка уже засчитана" и возвращаем время следующего урока.

---

### Fix 7: cleanup_orphaned_courses — NameError при смене тарифа

**Проблема:** В `activate_course()` блок `if "Смена тарифа" in activation_log_details:` использовал `current_active_version_id`, которая доступна только если `active_record` не None. При нестандартном code-path — потенциальный `NameError`.

**Решение:** Переменная `old_tariff = current_active_version_id if active_record else None` определяется явно перед использованием.

---

### Fix 8: check_pending_homework_timeout — supervision при падении

**Проблема:** Бесконечный цикл без `except asyncio.CancelledError` — при shutdown задача не завершалась корректно. При необработанном исключении задача падала тихо и таймауты ДЗ переставали работать навсегда.

**Решение:** Добавлен `except asyncio.CancelledError: return` и `except Exception: sleep(60); continue` — задача перезапускается при ошибке.

---

### Fix 9: Мусор в on_startup

**Проблема:** Три закомментированные строки с устаревшими вариантами webhook URL и комментарий `# <--- ДОБАВИТЬ ЭТОТ АРГУМЕНТ` остались от разработки.

**Решение:** Удалены.

---

## 2026-02-24 — Серия патчей активации и ДЗ

### fix: race condition при активации курса
**Коммит:** [98923f3](https://github.com/andrjur/antbot4/commit/98923f3)
Коммит данных в БД происходил ПОСЛЕ вызова `get_course_title()` — title читался из незакоммиченных данных. Порядок изменён: сначала commit, потом все чтения.

### fix: race condition - коммит ДО get_course_title
**Коммит:** [3b77dcf](https://github.com/andrjur/antbot4/commit/3b77dcf)
Уточнение предыдущего фикса.

### fix: hw_status='none' при первой активации курса
**Коммит:** [72bc683](https://github.com/andrjur/antbot4/commit/72bc683)
При INSERT в `user_courses` не указывался `hw_status` — получал NULL вместо 'none'.

### fix: сброс hw_status при активации ТОГО ЖЕ тарифа
**Коммит:** [d49...](https://github.com/andrjur/antbot4/commit/d499275)
Повторная активация того же кода не сбрасывала hw_status, студент застревал в 'pending'.

### fix: skip homework (/пропускаю, /skip)
**Решено:** 2026-02-24
Добавлена обработка ключевых слов для пропуска ДЗ. hw_status='approved' автоматически.

### fix: lesson_num=0 спам "урок недоступен"
**Решено:** 2026-02-24
Set `missing_lesson_warnings_sent` предотвращает повторные предупреждения каждую минуту.

### fix: удаление старого pending ДЗ при повторной отправке
**Решено:** 2026-02-24
При повторной отправке ДЗ удаляется старая запись из `pending_admin_homework`.

### fix: HW_TIMEOUT_SECONDS вместо MINUTES
**Решено:** 2026-02-24
Переменная переименована, единица изменена на секунды (дефолт 120).

### fix: фильтр F.chat.type == "private" в handle_text
**Решено:** 2026-02-24
Сообщения из admin-группы перестали считаться домашними заданиями.

### fix: очистка pending ДЗ при старте бота
**Решено:** 2026-02-24
При рестарте убираются кнопки со всех pending сообщений в admin-группе, таблица очищается.

---

## Архитектурные решения (принятые)

| Решение | Почему |
|---------|--------|
| parse_mode=None везде | Markdown V2 спецсимволы ломали сообщения, escape сложный |
| escape_md() как no-op | Убрать Markdown V2, но сохранить вызовы для будущей смены режима |
| Один файл main.py | Рефакторинг слишком рискован без покрытия тестами |
| bot.db как Docker volume | База не удаляется при пересборке контейнера |
| settings.json не в git | Содержит реальные коды активации и цены |
| HW_TIMEOUT_SECONDS=120 | Дефолт 2 минуты — достаточно для тестов, разумно для продакшна |
| cleanup_orphaned только вручную | Автозапуск при пустом settings.json → потеря данных студентов |
