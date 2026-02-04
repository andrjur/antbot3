# Тестирование AntBot

## Установка зависимостей

```bash
pip install -r requirements-test.txt
```

## Запуск тестов

### Все тесты
```bash
pytest
```

### С покрытием кода
```bash
pytest --cov=. --cov-report=html
```

### Конкретный файл тестов
```bash
pytest tests/test_database.py
pytest tests/test_activation.py
pytest tests/test_homework.py
pytest tests/test_scheduler.py
```

### С подробным выводом
```bash
pytest -v
```

### Только медленные тесты
```bash
pytest -m slow
```

### Исключить интеграционные тесты
```bash
pytest -m "not integration"
```

## Структура тестов

```
tests/
├── conftest.py          # Фикстуры и конфигурация pytest
├── test_database.py     # Тесты базы данных
├── test_activation.py   # Тесты активации курсов
├── test_homework.py     # Тесты обработки ДЗ
└── test_scheduler.py    # Тесты расписания уроков
```

## Фикстуры

### test_db
Временная SQLite база данных для каждого теста. Создается и удаляется автоматически.

### mock_bot
Мок-объект Telegram бота для тестирования без реальных API запросов.

### sample_settings
Тестовые настройки из `settings.json`.

## Покрытие кода

После запуска с `--cov-report=html` отчет будет в `htmlcov/index.html`

## Написание новых тестов

```python
import pytest

@pytest.mark.asyncio
async def test_something(test_db):
    # Используйте test_db для операций с БД
    await test_db.execute("INSERT ...")
    await test_db.commit()
    
    # Проверки
    cursor = await test_db.execute("SELECT ...")
    result = await cursor.fetchone()
    assert result[0] == expected_value
```

## CI/CD

Для GitHub Actions:

```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt -r requirements-test.txt
      - run: pytest --cov=. --cov-report=xml
      - uses: codecov/codecov-action@v2
```
