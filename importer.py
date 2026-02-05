import sqlite3
import re

DB_FILE = "bot.db"
TASKS_FILE = "tasks.txt"


def parse_and_import():
    """
    Парсит файл tasks.txt с разными форматами заданий и импортирует их в БД.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Убедимся, что таблица существует с правильной структурой.
    # Это важно, если вы запускаете импортер до первого запуска main.py
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_num INTEGER UNIQUE,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            karma_points INTEGER NOT NULL,
            report_example TEXT
        )
    ''')

    with open(TASKS_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    current_category = "Без категории"
    # Значение по умолчанию для стоимости, если не указано в категории
    current_karma = 1

    print("--- Начало импорта заданий ---")
    for line in lines:
        line = line.strip()
        if not line or line.startswith('*') or line.startswith('Конечно!'):
            continue

        # 1. Ищем заголовки категорий (#### **Название** (X КБ))
        category_match = re.search(r'####\s*\*\*(.*?)\*\*\s*\((\d+)\s*КБ\)', line)
        if category_match:
            current_category = category_match.group(1).strip()
            current_karma = int(category_match.group(2))
            print(f"\nНайдена категория: '{current_category}' (стоимость по умолчанию: {current_karma} КБ)")
            continue

        # 2. Ищем задания с отчетом (формат: 201. **Практика "Ф...":** ... *Отчет...*)
        task_with_report_match = re.search(r'^(\d+)\.\s*\*\*(.*?):\*\*\s*(.*?)\s*\*Отчет для бота:\s*(.*)\*', line)
        if task_with_report_match:
            task_num = int(task_with_report_match.group(1))
            title = task_with_report_match.group(2).strip()
            description = task_with_report_match.group(3).strip()
            report_example = task_with_report_match.group(4).strip()

            cursor.execute("""
                INSERT OR REPLACE INTO task_templates (task_num, category, title, description, karma_points, report_example)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (task_num, current_category, title, description, current_karma, report_example))
            print(f"  [OK] Задание с отчетом: #{task_num} '{title}'")
            continue

        # 3. Ищем задания с явной стоимостью в конце (формат: 46. **...** - 5 КБ)
        task_with_own_karma_match = re.search(r'^(\d+)\.\s*\*\*(.*?)\*\*\s*-\s*(\d+)\s*КБ', line)
        if task_with_own_karma_match:
            task_num = int(task_with_own_karma_match.group(1))
            full_title = task_with_own_karma_match.group(2).strip().split(':')[0]
            description = task_with_own_karma_match.group(2).strip().split(':')[
                1].strip() if ':' in task_with_own_karma_match.group(2) else ""
            karma_points = int(task_with_own_karma_match.group(3))

            cursor.execute("""
                INSERT OR REPLACE INTO task_templates (task_num, category, title, description, karma_points)
                VALUES (?, ?, ?, ?, ?)
            """, (task_num, current_category, full_title, description, karma_points))
            print(f"  [OK] Задание со своей стоимостью: #{task_num} '{full_title}'")
            continue

        # 4. Ищем самые простые задания (формат: 1. Описание.)
        task_simple_match = re.search(r'^(\d+)\.\s*(.*)', line)
        if task_simple_match:
            task_num = int(task_simple_match.group(1))
            description_full = task_simple_match.group(2).strip()

            title = description_full.split(':')[0] if ':' in description_full else description_full
            description = description_full.split(':')[1].strip() if ':' in description_full else ""

            # Определяем стоимость по номеру, если она не указана явно
            karma_points = 1  # Значение по умолчанию
            if 1 <= task_num <= 15:
                karma_points = 1
            elif 16 <= task_num <= 30:
                karma_points = 2
            elif 31 <= task_num <= 45:
                karma_points = 3
            elif 46 <= task_num <= 55:
                karma_points = 5  # Смешанная категория
            elif 56 <= task_num <= 100:
                karma_points = 2  # Примерная оценка для остатка
            elif 101 <= task_num <= 115:
                karma_points = 1
            elif 116 <= task_num <= 130:
                karma_points = 2
            elif 131 <= task_num <= 145:
                karma_points = 3

            cursor.execute("""
                INSERT OR REPLACE INTO task_templates (task_num, category, title, description, karma_points)
                VALUES (?, ?, ?, ?, ?)
            """, (task_num, current_category, title, description, karma_points))
            print(f"  [OK] Простое задание: #{task_num} '{title}'")
            continue

    conn.commit()
    conn.close()
    print("\nИмпорт завершен!")


if __name__ == "__main__":
    parse_and_import()