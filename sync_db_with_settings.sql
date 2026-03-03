-- ============================================
-- Синхронизация БД с settings.json
-- Удаляет из БД курсы, которых нет в settings.json
-- ============================================
-- Запуск: sqlite3 bot.db < sync_db_with_settings.sql
-- ============================================
-- ВАЖНО: Перед запуском убедитесь, что settings.json актуален!
-- Скрипт удаляет из БД только то, чего НЕТ в settings.json
-- Новые курсы из settings.json НЕ добавляются (только очистка)
-- ============================================

-- ============================================
-- НАСТРОЙКА: Впишите сюда course_id из settings.json
-- ============================================
-- Пример: если в settings.json есть только base и sprint2,
-- то оставляем только их

-- Временная таблица с разрешёнными курсами из settings.json
CREATE TEMP TABLE allowed_courses (course_id TEXT PRIMARY KEY);

-- ============================================
-- ДОБАВЬТЕ СЮДА ВСЕ КУРСЫ ИЗ settings.json
-- ============================================
INSERT INTO allowed_courses (course_id) VALUES 
    ('base'),
    ('sprint2');
-- Если есть другие курсы, добавьте их выше через запятую
-- Пример: ('spring3'), ('spring4');

-- ============================================
-- ПРОВЕРКА ПЕРЕД ИЗМЕНЕНИЯМИ
-- ============================================

.headers on
.mode column

SELECT '=== КУРСЫ В БД (ПЕРЕД) ===' as '';
SELECT course_id, title, group_id FROM courses ORDER BY course_id;

SELECT '=== КУРСЫ В settings.json (РАЗРЕШЕНЫ) ===' as '';
SELECT * FROM allowed_courses;

SELECT '=== БУДУТ УДАЛЕНЫ ===' as '';
SELECT c.course_id, c.title, c.group_id,
       (SELECT COUNT(*) FROM group_messages gm WHERE gm.course_id = c.course_id) as уроков,
       (SELECT COUNT(*) FROM course_versions cv WHERE cv.course_id = c.course_id) as версий,
       (SELECT COUNT(*) FROM user_courses uc WHERE uc.course_id = c.course_id) as активаций
FROM courses c
WHERE c.course_id NOT IN (SELECT course_id FROM allowed_courses);

-- ============================================
-- СИНХРОНИЗАЦИЯ (в транзакции)
-- ============================================

BEGIN TRANSACTION;

-- 1. Удаляем group_messages для курсов, которых нет в settings.json
DELETE FROM group_messages 
WHERE course_id NOT IN (SELECT course_id FROM allowed_courses);

-- 2. Удаляем course_versions для курсов, которых нет в settings.json
DELETE FROM course_versions 
WHERE course_id NOT IN (SELECT course_id FROM allowed_courses);

-- 3. Удаляем course_activation_codes для курсов, которых нет в settings.json
DELETE FROM course_activation_codes 
WHERE course_id NOT IN (SELECT course_id FROM allowed_courses);

-- 4. Обновляем user_courses: деактивируем курсы, которых нет в settings.json
-- (не удаляем, а помечаем как inactive для истории)
UPDATE user_courses 
SET status = 'inactive' 
WHERE course_id NOT IN (SELECT course_id FROM allowed_courses) 
  AND status != 'inactive';

-- 5. Удаляем admin_context для курсов, которых нет в settings.json
DELETE FROM admin_context 
WHERE course_id NOT IN (SELECT course_id FROM allowed_courses);

-- 6. Удаляем homework для курсов, которых нет в settings.json
DELETE FROM homework 
WHERE course_id NOT IN (SELECT course_id FROM allowed_courses);

-- 7. Удаляем курсы из таблицы courses
DELETE FROM courses 
WHERE course_id NOT IN (SELECT course_id FROM allowed_courses);

COMMIT;

-- ============================================
-- ПРОВЕРКА ПОСЛЕ ИЗМЕНЕНИЙ
-- ============================================

SELECT '=== КУРСЫ В БД (ПОСЛЕ) ===' as '';
SELECT course_id, title, group_id FROM courses ORDER BY course_id;

SELECT '=== СТАТИСТИКА ===' as '';
SELECT 
    (SELECT COUNT(*) FROM courses) as 'Курсов в БД',
    (SELECT COUNT(*) FROM allowed_courses) as 'Разрешено в settings.json',
    (SELECT COUNT(*) FROM group_messages) as 'Сообщений в БД',
    (SELECT COUNT(*) FROM user_courses WHERE status='active') as 'Активных активаций';

-- ============================================
-- ОЧИСТКА
-- ============================================

DROP TABLE allowed_courses;

SELECT '=== СИНХРОНИЗАЦИЯ ЗАВЕРШЕНА ===' as '';
