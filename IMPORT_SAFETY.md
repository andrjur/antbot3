# 📥 Безопасный импорт базы данных

## ⚠️ WARNING: Текущий `/import_db` ПОЛНОСТЬЮ очищает базу!

При использовании `/import_db`:
1. **Все таблицы очищаются** (`DELETE FROM table`)
2. **Данные из файла заменяют текущие**
3. **Прогресс студентов теряется**

---

## ✅ Безопасные сценарии использования

### Сценарий 1: Восстановление после сбоя (пустая БД)

```bash
# Если база данных пуста или повреждена
/export_db  # Сначала сделай бэкап текущего состояния
/import_db  # Затем загрузи рабочий бэкап
```

**Результат:** База восстанавливается из бэкапа.

---

### Сценарий 2: Добавление данных из старого бэкапа

**Не используй `/import_db`!** Вместо этого:

```bash
# 1. Создай резервную копию текущей БД
cp bot.db bot.db.backup.before_import

# 2. Используй SQL-скрипт для выборочного импорта
sqlite3 bot.db < selective_import.sql
```

---

## 🔧 selective_import.sql — выборочный импорт

Создайте файл `selective_import.sql` для импорта **только недостающих данных**:

```sql
-- ============================================
-- Выборочный импорт из бэкапа
-- ============================================
-- Использование: sqlite3 bot.db < selective_import.sql
-- ============================================

-- Подключаем файл бэкапа как дополнительную БД
ATTACH DATABASE 'database_export.json' AS backup;

-- Импортируем ТОЛЬКО курсы, которых нет в текущей БД
INSERT OR IGNORE INTO courses (course_id, title, group_id, description)
SELECT course_id, title, group_id, description 
FROM backup.courses
WHERE course_id NOT IN (SELECT course_id FROM courses);

-- Импортируем ТОЛЬКО коды активации, которых нет
INSERT OR IGNORE INTO course_activation_codes (code_word, course_id, version_id, price_rub)
SELECT code_word, course_id, version_id, price_rub 
FROM backup.course_activation_codes
WHERE code_word NOT IN (SELECT code_word FROM course_activation_codes);

-- ОТКЛЮЧАЕМ бэкап
DETACH DATABASE backup;

-- Проверка результата
SELECT 'Импортировано курсов:' as проверка, COUNT(*) FROM courses;
SELECT 'Кодов активации:' as проверка, COUNT(*) FROM course_activation_codes;
```

---

## 📋 Сравнение команд

| Команда | Что делает | Безопасно? |
|---------|------------|------------|
| `/export_db` | Экспорт БД → JSON | ✅ Да |
| `/import_db` | **Полная замена БД** из JSON | ❌ **НЕТ (удаляет всё)** |
| `cp bot.db backup.db` | Копия БД | ✅ Да |
| `sqlite3 bot.db < script.sql` | Выполнение SQL | ⚠️ Зависит от скрипта |

---

## 🚨 Чего НЕ делать

```bash
# ❌ НЕ делай это, если есть активные студенты:
/import_db  # Удалит весь прогресс!

# ❌ НЕ делай это без бэкапа:
rm bot.db && sqlite3 bot.db < database_export.json

# ✅ ДЕЛАЙ так:
cp bot.db bot.db.backup  # Бэкап
/import_db               # Импорт (если действительно нужно)
# ИЛИ
sqlite3 bot.db < selective_import.sql  # Выборочный импорт
```

---

## ✅ Рекомендуемый рабочий процесс

### Перед любыми изменениями:
```bash
# 1. Бэкап БД
cp bot.db bot.db.backup.$(date +%Y%m%d_%H%M%S)

# 2. Экспорт в JSON (дополнительный бэкап)
# Через бота: /export_db
```

### Для добавления курсов/кодов:
```bash
# 1. Используй SQL-скрипт для выборочного импорта
sqlite3 bot.db < selective_import.sql

# 2. ИЛИ через бота (безопасно):
/add_course      # Добавить курс
/upload_lesson   # Загрузить уроки
```

### Для восстановления после сбоя:
```bash
# 1. Остановить бота
docker compose down

# 2. Восстановить из бэкапа
cp bot.db.backup.20260305 bot.db

# 3. Запустить бота
docker compose up -d
```

---

## 📊 Таблицы и их важность

| Таблица | Важность | Можно перезаписать? |
|---------|----------|---------------------|
| `users` | 🔴 Критично | ❌ Нет (потеря пользователей) |
| `user_courses` | 🔴 Критично | ❌ Нет (потеря прогресса) |
| `group_messages` | 🔴 Критично | ❌ Нет (потеря уроков) |
| `homework_gallery` | 🔴 Критично | ❌ Нет (потеря ДЗ) |
| `courses` | 🟡 Средне | ⚠️ Только если курс новый |
| `course_activation_codes` | 🟡 Средне | ✅ Да (можно добавить) |
| `admin_context` | 🟢 Низко | ✅ Да |
| `user_states` | 🟢 Низко | ✅ Да (сбросится при рестарте) |

---

## 🛠 Если нужно объединить данные

Для **объединения** данных из бэкапа с текущей БД:

```python
# Python-скрипт для безопасного импорта
import sqlite3, json

# Загружаем бэкап
with open('database_export.json', 'r') as f:
    backup_data = json.load(f)

# Подключаемся к БД
conn = sqlite3.connect('bot.db')
cursor = conn.cursor()

# Импортируем ТОЛЬКО курсы (игнорируем дубликаты)
for course in backup_data.get('courses', []):
    cursor.execute('''
        INSERT OR IGNORE INTO courses (course_id, title, group_id, description)
        VALUES (?, ?, ?, ?)
    ''', (course['course_id'], course['title'], course['group_id'], course['description']))

# Импортируем ТОЛЬКО коды активации (игнорируем дубликаты)
for code in backup_data.get('course_activation_codes', []):
    cursor.execute('''
        INSERT OR IGNORE INTO course_activation_codes 
        (code_word, course_id, version_id, price_rub)
        VALUES (?, ?, ?, ?)
    ''', (code['code_word'], code['course_id'], code['version_id'], code['price_rub']))

conn.commit()
conn.close()
print("✅ Выборочный импорт завершён")
```

---

## 📞 Чек-лист перед импортом

- [ ] Я сделал бэкап текущей БД (`cp bot.db bot.db.backup`)
- [ ] Я экспортировал текущее состояние (`/export_db`)
- [ ] Я понимаю, что `/import_db` **удалит все текущие данные**
- [ ] У меня есть веская причина для полного импорта (сбой, откат)
- [ ] Все активные студенты завершили текущие уроки

**Если хотя бы один пункт не выполнен — НЕ используй `/import_db`!**
