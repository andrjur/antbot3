-- ============================================
-- Слияние курса spring31 в spring3
-- и удаление всех упоминаний spring31
-- ============================================
-- Запуск: sqlite3 bot.db < merge_spring31_to_spring3.sql
-- ============================================

-- ============================================
-- ЧАСТЬ 1: ПРОВЕРКА ПЕРЕД ИЗМЕНЕНИЯМИ
-- ============================================

.headers on
.mode column

SELECT '=== ПЕРЕД СЛИЯНИЕМ ===' as '';

SELECT 'courses' as таблица, course_id, title, substr(description, 1, 50) as desc_preview FROM courses WHERE course_id IN ('spring3', 'spring31')
UNION ALL
SELECT 'course_versions', course_id, version_id, NULL FROM course_versions WHERE course_id IN ('spring3', 'spring31')
UNION ALL
SELECT 'user_courses', course_id, status, NULL FROM user_courses WHERE course_id IN ('spring3', 'spring31');

-- Урок 0 (описания курсов)
SELECT 'Описания (lesson_num=0):' as '';
SELECT course_id, id, content_type, length(text) as text_len, substr(text, 1, 80) as text_preview 
FROM group_messages 
WHERE course_id IN ('spring3', 'spring31') AND lesson_num = 0 
ORDER BY course_id, id;

-- ============================================
-- ЧАСТЬ 2: СЛИЯНИЕ (в транзакции)
-- ============================================

BEGIN TRANSACTION;

-- 1. Обновить group_messages: spring31 → spring3
UPDATE group_messages SET course_id = 'spring3' WHERE course_id = 'spring31';

-- 2. Обновить course_versions: spring31 → spring3
UPDATE course_versions SET course_id = 'spring3' WHERE course_id = 'spring31';

-- 3. Обновить course_activation_codes: spring31 → spring3
UPDATE course_activation_codes SET course_id = 'spring3' WHERE course_id = 'spring31';

-- 4. Обновить user_courses: spring31 → spring3
UPDATE user_courses SET course_id = 'spring3' WHERE course_id = 'spring31';

-- 5. Обновить admin_context: spring31 → spring3
UPDATE admin_context SET course_id = 'spring3' WHERE course_id = 'spring31';

-- 6. Обновить homework: spring31 → spring3
UPDATE homework SET course_id = 'spring3' WHERE course_id = 'spring31';

-- 7. Удалить запись из courses (spring31)
DELETE FROM courses WHERE course_id = 'spring31';

COMMIT;

-- ============================================
-- ЧАСТЬ 3: ПРОВЕРКА ПОСЛЕ ИЗМЕНЕНИЙ
-- ============================================

SELECT '=== ПОСЛЕ СЛИЯНИЯ ===' as '';

-- Проверить что spring31 больше нет
SELECT 'Осталось spring31:' as проверка, 
       (SELECT COUNT(*) FROM courses WHERE course_id = 'spring31') as courses,
       (SELECT COUNT(*) FROM course_versions WHERE course_id = 'spring31') as versions,
       (SELECT COUNT(*) FROM course_activation_codes WHERE course_id = 'spring31') as codes,
       (SELECT COUNT(*) FROM user_courses WHERE course_id = 'spring31') as users,
       (SELECT COUNT(*) FROM group_messages WHERE course_id = 'spring31') as messages;

-- Проверить spring3
SELECT 'Данные spring3:' as проверка,
       (SELECT COUNT(*) FROM courses WHERE course_id = 'spring3') as courses,
       (SELECT COUNT(*) FROM course_versions WHERE course_id = 'spring3') as versions,
       (SELECT COUNT(*) FROM user_courses WHERE course_id = 'spring3') as users,
       (SELECT COUNT(*) FROM group_messages WHERE course_id = 'spring3') as messages;

-- Показать все описания курсов
SELECT 'Все описания курсов:' as '';
SELECT course_id, title, substr(description, 1, 100) as description_preview FROM courses ORDER BY course_id;

-- Показать урок 0 для spring3
SELECT 'Урок 0 для spring3:' as '';
SELECT id, content_type, length(text) as text_len, substr(text, 1, 100) as text_preview 
FROM group_messages 
WHERE course_id = 'spring3' AND lesson_num = 0 
ORDER BY id;
