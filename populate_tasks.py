

import sqlite3
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
DB_FILE = Path(__file__).parent / "bot.db"

# Структура теперь включает bonus_points
TASKS_TO_ADD = [
    {
        "id": 201, "category": "Управление вниманием", "points": 20, "bonus_points": 2, "is_repeatable": True,
        "text": "Практика 'Фонарик Внимания': ...",
        "report": "Сделано. Сложнее всего было удержать внимание на [тело/звуки/предмет]."
    },
    {
        "id": 202, "category": "Управление вниманием", "points": 20, "bonus_points": 2, "is_repeatable": False,
        "text": "'Одна Задача': ...",
        "report": "Сделано. Заметил(а), что отвлекался(ась) примерно [количество] раз."
    },
    {
        "id": 214, "category": "Восстановление энергии", "points": 10, "bonus_points": 1, "is_repeatable": True,
        "text": "Празднование 'Микро-Победы': ...",
        "report": "Сегодня я отпраздновал(а) завершение [название дела]."
    }
]

def populate_database():
    """Наполняет или обновляет таблицу task_pool в базе данных."""
    if not DB_FILE.exists():
        logging.error(f"Файл базы данных {DB_FILE} не найден. Запустите основного бота для его создания.")
        return

    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        logging.info("Соединение с базой данных установлено.")

        # Убедимся, что таблица имеет новую колонку
        try:
            cursor.execute("SELECT bonus_points FROM task_pool LIMIT 1")
        except sqlite3.OperationalError:
            logging.warning("Колонка 'bonus_points' отсутствует. Добавляю...")
            cursor.execute("ALTER TABLE task_pool ADD COLUMN bonus_points INTEGER DEFAULT 1")

        added_count = 0
        updated_count = 0

        for task in TASKS_TO_ADD:
            cursor.execute("SELECT id FROM task_pool WHERE id = ?", (task["id"],))
            if cursor.fetchone():
                # Обновляем, включая bonus_points
                cursor.execute("""
                    UPDATE task_pool SET
                        task_category = ?, task_text = ?, report_format = ?,
                        karma_points = ?, is_repeatable = ?, bonus_points = ?
                    WHERE id = ?
                """, (
                    task["category"], task["text"], task["report"],
                    task["points"], task["is_repeatable"], task["bonus_points"], task["id"]
                ))
                updated_count += 1
            else:
                # Вставляем, включая bonus_points
                cursor.execute("""
                    INSERT INTO task_pool (id, task_category, task_text, report_format, karma_points, is_repeatable, bonus_points)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    task["id"], task["category"], task["text"], task["report"],
                    task["points"], task["is_repeatable"], task["bonus_points"]
                ))
                added_count += 1

        conn.commit()
        logging.info(f"Операция завершена. Добавлено: {added_count}. Обновлено: {updated_count}.")

    except sqlite3.Error as e:
        logging.error(f"Ошибка при работе с базой данных: {e}")
    finally:
        if conn:
            conn.close()
            logging.info("Соединение с базой данных закрыто.")

if __name__ == "__main__":
    populate_database()