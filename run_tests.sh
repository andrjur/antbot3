#!/bin/bash

echo "Running AntBot Tests..."
echo

# Проверяем установлен ли pytest
if ! command -v pytest &> /dev/null; then
    echo "Installing test dependencies..."
    pip install -r requirements-test.txt
fi

echo "=== Running all tests ==="
pytest -v

echo
echo "=== Running tests with coverage ==="
pytest --cov=. --cov-report=term-missing --cov-report=html

echo
echo "=== Test coverage report generated in htmlcov/ ==="
echo "Open htmlcov/index.html to view detailed report"

echo
echo "=== Running database tests ==="
pytest tests/test_database.py -v

echo
echo "=== Running activation tests ==="
pytest tests/test_activation.py -v

echo
echo "=== Running homework tests ==="
pytest tests/test_homework.py -v

echo
echo "=== Running scheduler tests ==="
pytest tests/test_scheduler.py -v

echo
echo "All tests completed!"
