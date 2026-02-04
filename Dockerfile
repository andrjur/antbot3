FROM python:3.12-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код бота
COPY . .

# Создаем необходимые директории
RUN mkdir -p logs

# Устанавливаем права на выполнение
RUN chmod +x /app/entrypoint.sh || true

# Открываем порт для вебхука и метрик
EXPOSE 8080

# Команда запуска
CMD ["python", "main.py"]
