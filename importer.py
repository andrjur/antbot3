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

    # Сначала создадим таблицу, если ее нет (на всякий случай)
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
    current_karma = 1  # Значение по умолчанию

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # 1. Ищем заголовки категорий (#### ... (X КБ))
        category_match = re.search(r'####\s*\*\*(.*?)\*\*\s*\((\d+)\s*КБ\)', line)
        if category_match:
            current_category = category_match.group(1).strip()
            current_karma = int(category_match.group(2))
            print(f"\n--- Найдена категория: {current_category} ({current_karma} КБ) ---")
            continue

        # 2. Ищем задания с отчетом (формат 201. **...** ... *Отчет...*)
        task_with_report_match = re.search(r'^(\d+)\.\s*\*\*(.*?):\*\*\s*(.*?)\s*\*Отчет для бота:\s*(.*)\*', line)
        if task_with_report_match:
            task_num = int(task_with_report_match.group(1))
            title = task_with_report_match.group(2).strip()
            description = task_with_report_match.group(3).strip()
            report_example = task_with_report_match.group(4).strip()

            # Вставляем в БД
            cursor.execute("""
                INSERT OR REPLACE INTO task_templates 
                (task_num, category, title, description, karma_points, report_example)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (task_num, current_category, title, description, current_karma, report_example))
            print(f"Добавлено задание с отчетом: #{task_num} {title}")
            continue

        # 3. Ищем задания без отчета и без жирного выделения (1. Описание.)
        # и с баллами в конце (198. **Описание** - 7 КБ)
        task_simple_match = re.search(r'^(\d+)\.\s*(.*?)(?:\s*-\s*(\d+)\s*КБ)?$', line)
        if task_simple_match:
            task_num = int(task_simple_match.group(1))
            description_full = task_simple_match.group(2).strip().replace('**', '')  # Убираем звездочки, если они есть
            # Если у задания есть свои баллы, используем их, иначе - баллы категории
            karma_points = int(task_simple_match.group(3)) if task_simple_match.group(3) else current_karma

            # Разделяем на "название" и "описание", если есть двоеточие
            if ':' in description_full:
                title, description = [s.strip() for s in description_full.split(':', 1)]
            else:
                title = description_full  # Если двоеточия нет, все описание идет в название
                description = ""

            cursor.execute("""
                INSERT OR REPLACE INTO task_templates 
                (task_num, category, title, description, karma_points, report_example)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (task_num, current_category, title, description, karma_points, None))
            print(f"Добавлено простое задание: #{task_num} {title}")
            continue

    conn.commit()
    conn.close()
    print("\nИмпорт завершен!")


if __name__ == "__main__":
    parse_and_import()