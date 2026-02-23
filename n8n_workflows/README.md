# n8n Workflows

Эта папка содержит workflow для n8n.

## Импорт workflow

1. Откройте n8n: `https://n8n.indikov.ru`
2. Меню (⋮) → Import from File
3. Выберите файл `.json`
4. Настройте credentials (см. ниже)
5. **Включите workflow** (переключатель в правом верхнем углу — должен стать зелёным)

## Credentials (учётные данные)

### 1. Telegram API
- **Name:** `Telegram account`
- **Bot Token:** из `.env` (`BOT_TOKEN`)

### 2. OpenRouter API
- **Name:** `OpenRouter account`
- **API Key:** из `.env` (`OPENROUTER_API_KEY`)

### 3. HTTP Header Auth (для webhook от бота)
- **Name:** `Python to n8n Auth`
- **Header:** `X-N8N-Signature`
- **Value:** из `.env` (`N8N_WEBHOOK_SECRET`)

### 4. HTTP Header Auth (для callback от n8n)
- **Name:** `n8n to Python Callback Auth`
- **Header:** `X-CALLBACK-SIGNATURE`
- **Value:** из `.env` (`N8N_CALLBACK_SECRET`)

## Workflow

### AI_Agent.json

Проверка домашних заданий через AI (DeepSeek через OpenRouter).

**Webhook URL:** `/webhook/aa46a723-619e-42e9-8e51-49ba51813718`

**Входящий payload (от бота):**
```json
{
  "action": "check_homework" | "check_homework_timeout",
  "student_user_id": 123456789,
  "user_fullname": "Имя Фамилия",
  "course_numeric_id": 1,
  "course_title": "Название курса",
  "lesson_num": 1,
  "lesson_assignment_description": "Текст задания",
  "expected_homework_type": "text",
  "homework_text": "Текст ДЗ от студента",
  "homework_file_id": "file_id_или_null",
  "admin_group_id": -1001234567890,
  "original_admin_message_id": 123,
  "callback_webhook_url_result": "https://bot.indikov.ru/webhook/n8n_hw_result",
  "telegram_bot_token": "bot_token"
}
```

**Исходящий payload (callback в бот):**
```json
{
  "student_user_id": 123456789,
  "course_numeric_id": 1,
  "lesson_num": 1,
  "is_approved": true,
  "feedback_text": "Отличная работа!",
  "original_admin_message_id": 123
}
```

## Настройка команды /set_hw_timeout

Админы могут менять время ожидания перед AI-проверкой:

```
/set_hw_timeout 3    # 3 минуты
/set_hw_timeout 5    # 5 минут
```

По умолчанию: 2 минуты. Можно задать через `.env`:
```
HW_TIMEOUT_MINUTES=2
```
