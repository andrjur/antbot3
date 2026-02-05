#!/bin/bash

# Гарантируем использование UTF-8
export PYTHONIOENCODING=utf-8
export PYTHONLEGACYWINDOWSSTDIO=utf-8

echo "Starting AntBot in Docker..."
echo

# Проверяем, что Docker запущен
if ! docker version &> /dev/null; then
    echo "ERROR: Docker is not running!"
    echo "Please start Docker first."
    exit 1
fi

# Проверяем наличие .env файла
if [ ! -f .env ]; then
    echo "WARNING: .env file not found!"
    if [ -f .env.txt ]; then
        echo "Copying from .env.txt template..."
        cp .env.txt .env
        echo "Please edit .env file with your settings"
    else
        echo "ERROR: No .env template found!"
        exit 1
    fi
fi

echo
echo "=== Building and starting services ==="
docker-compose up --build -d

if [ $? -ne 0 ]; then
    echo "ERROR: Failed to start containers!"
    exit 1
fi

echo
echo "=== Services status ==="
docker-compose ps

echo
echo "=== Bot logs (press Ctrl+C to stop watching logs) ==="
sleep 5
docker-compose logs -f bot