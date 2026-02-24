-- Очистка user_courses от курсов без уроков
-- Запуск: sqlite3 bot.db < cleanup_empty_courses.sql

-- 1. Показать курсы без уроков
SELECT 'Курсы без уроков:' as '';
SELECT course_id, COUNT(*) as lessons 
FROM group_messages 
WHERE lesson_num > 0 
GROUP BY course_id 
HAVING lessons = 0;

-- 2. Удалить user_courses для курсов без уроков
DELETE FROM user_courses 
WHERE course_id IN (
    SELECT course_id 
    FROM group_messages 
    GROUP BY course_id 
    HAVING COUNT(CASE WHEN lesson_num > 0 THEN 1 END) = 0
);

-- 3. Показать результат
SELECT 'Оставшиеся активные курсы:' as '';
SELECT uc.user_id, uc.course_id, uc.status, COUNT(gm.lesson_num) as lessons
FROM user_courses uc
LEFT JOIN group_messages gm ON uc.course_id = gm.course_id AND gm.lesson_num > 0
WHERE uc.status = 'active'
GROUP BY uc.user_id, uc.course_id;
