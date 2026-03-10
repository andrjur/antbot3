# 🔧 Сокращение логов и исправление Alertmanager

## Проблема 1: Alertmanager спамит ошибками 401

### Симптомы
```
antbot-alertmanager | telegram: Unauthorized (401)
```

### Причина
Бот для алертов не авторизован или токен неверный.

### Решение А: Остановить Alertmanager (быстро)
```bash
cd ~/antbot4

# Остановить только alertmanager
docker compose stop alertmanager

# Проверить что работает
docker compose ps
```

### Решение Б: Исправить токен
1. Проверь `.env`:
   ```bash
   cat .env | grep ALERT
   ```

2. Должно быть:
   ```env
   ALERT_BOT_TOKEN=7473862113:AAF...
   ALERT_CHAT_ID=-1002591981307
   ```

3. Если токен неверный — обнови и перезапусти:
   ```bash
   nano .env
   docker compose restart alertmanager
   ```

### Решение В: Удалить Alertmanager (навсегда)
```bash
# Остановить
docker compose down alertmanager

# Удалить контейнер
docker rm -f antbot-alertmanager

# Закомментировать в docker-compose.yml
# alertmanager:
#   image: prom/alertmanager:latest
#   ...
```

---

## Проблема 2: Огромное количество логов

### Как смотреть только ошибки
```bash
# Только ERROR
docker compose logs bot | grep ERROR

# Только WARNING и ERROR
docker compose logs bot | grep -E "(ERROR|WARNING)"

# Последние 50 строк
docker compose logs bot --tail=50

# В реальном времени, только ошибки
docker compose logs -f bot 2>&1 | grep --line-buffered ERROR
```

### Как уменьшить уровень логирования

**Вариант А: Изменить в main.py**
```python
# Строка ~132 в main.py
logging.basicConfig(
    level=logging.INFO,  # ← Было
    level=logging.WARNING,  # ← Стало (только предупреждения и ошибки)
)
```

**Вариант Б: Фильтровать в docker compose**
```bash
# Создать скрипт для просмотра
echo '#!/bin/bash
docker compose logs bot --tail=100 | grep -E "(ERROR|WARNING|CRITICAL)"' > /usr/local/bin/bot-logs
chmod +x /usr/local/bin/bot-logs

# Использовать
bot-logs
```

### Настройка логирования в Docker

**docker-compose.yml:**
```yaml
services:
  bot:
    ...
    logging:
      driver: "json-file"
      options:
        max-size: "10m"    # Максимум 10MB на лог
        max-file: "3"      # Хранить 3 файла
```

---

## Проблема 3: Логи не пишутся

### Проверить место на диске
```bash
df -h
```

### Проверить права на logs/
```bash
ls -la logs/
chmod 755 logs/
```

### Перезапустить с очисткой логов
```bash
# Очистить старые логи
docker compose logs --tail=0

# Перезапустить бота
docker compose restart bot
```

---

## Рекомендуемые настройки

### 1. Остановить Alertmanager (если не используется)
```bash
docker compose stop alertmanager prometheus grafana
```

### 2. Настроить логирование
```bash
# Смотреть только ошибки
docker compose logs -f bot 2>&1 | grep --line-buffered -E "(ERROR|CRITICAL)"
```

### 3. Автоматическая ротация логов

**/etc/logrotate.d/antbot:**
```
/app/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    notifempty
    create 0644 root root
}
```

---

## Быстрые команды

```bash
# Остановить спам Alertmanager
docker compose stop alertmanager

# Посмотреть последние ошибки
docker compose logs bot --tail=100 | grep ERROR

# Проверить место под логи
du -sh /var/lib/docker/containers/*

# Очистить старые логи Docker
docker compose logs --tail=0
```
