import sqlite3
import re

DB_FILE = "bot.db"
TASKS_FILE = "tasks.txt"


def parse_and_import():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    with open(TASKS_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    # Простая регулярка для парсинга ваших заданий
    # Она ищет номер, точку, текст и стоимость в скобках
    tasks = re.findall(r'(\d+)\.\s*(.*?)\s*-\s*(\d+)\s*КБ', content, re.DOTALL)

    for task_num, description, karma_points in tasks:
        task_num = int(task_num)
        description = description.strip()
        karma_points = int(karma_points)

        # Здесь вы можете вручную или автоматически определить категорию по номеру
        category = "Не определена"
        if 1 <= task_num <= 15:
            category = "Внутренний Порядок и Осознанность"
        elif 16 <= task_num <= 30:
            category = "Молчаливое Служение и Забота"
        # ... и так далее для всех ваших категорий

        # Вставляем в БД, игнорируя дубликаты по task_num
        cursor.execute("""
            INSERT OR IGNORE INTO task_templates (task_num, category, description, karma_points)
            VALUES (?, ?, ?, ?)
        """, (task_num, category, description, karma_points))

        print(f"Добавлено/обновлено задание #{task_num}")

    conn.commit()
    conn.close()
    print("Импорт завершен!")


if __name__ == "__main__":
    parse_and_import()