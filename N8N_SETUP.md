# Настройка n8n workflow для проверки ДЗ

## ✅ Рабочая конфигурация (28.02.2026)

### 1. Бот (Docker + Polling режим)

**Файл `.env`:**
```bash
BOT_INTERNAL_URL=http://bot:8080
WEBHOOK_SECRET_PATH=hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL
HW_TIMEOUT_SECONDS=34
N8N_CALLBACK_SECRET=500
```

**Что происходит:**
- Бот слушает `/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL/n8n_hw_result`
- n8n отправляет callback на `http://bot:8080/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL/n8n_hw_result` (внутренняя сеть Docker)
- Ошибка 404 **НЕ возникает**

---

### 2. n8n Workflow

#### Узел 1: Webhook-homework
```json
{
  "HTTP Method": "POST",
  "Path": "aa46a723-619e-42e9-8e51-49ba51813718",
  "Authentication": "Header Auth"
}
```

#### Узел 2: Edit Fields
Собирает данные из webhook:
- `student_user_id`
- `course_numeric_id`
- `lesson_num`
- `student_name`
- `hw_text`
- `lesson_desc`

#### Узел 3: If (проверка файла)
```
Condition: hw_file_id is not empty
```

#### Узел 4: Get a file (Telegram)
Скачивает файл ДЗ из Telegram

#### Узел 5: Merge
Объединяет текст + файл

#### Узел 6: Agent (ИИ)
**Prompt:**
```
Ты — мудрый и добрый наставник...
Имя студента: {{ $('Edit Fields').item.json.student_name }}
Контекст: {{ $('Edit Fields').item.json.lesson_desc }}
Работа: {{ $('Edit Fields').item.json.hw_text }}
```

**Output:** JSON с полями:
- `is_approved` (boolean)
- `feedback_text` (string)

#### Узел 7: Code (парсинг JSON)
```javascript
const rawOutput = $('Agent').first().json.output;
let isApproved = true;
let feedbackText = `Агент вернул некорректный ответ...`;

const jsonMatch = rawOutput.match(/(\{[\s\S]*\})/);
if (jsonMatch && jsonMatch[1]) {
  try {
    const parsedData = JSON.parse(jsonMatch[1]);
    if (typeof parsedData.is_approved === 'boolean') isApproved = parsedData.is_approved;
    if (typeof parsedData.feedback_text === 'string') feedbackText = parsedData.feedback_text;
  } catch (e) {
    console.error("Parse error:", e.message);
  }
}

return {
  json: {
    is_approved: isApproved,
    feedback_text: feedback_text
  }
};
```

#### Узел 8: HTTP Request1 (СТАТУС PROCESSING) ⭐️

**Этот узел отправляет статус "ИИ начал проверку"**

**URL (Expression):**
```
{{ $('Webhook-homework').item.json.body.callback_webhook_url_result }}
```

**Authentication:**
- Generic Auth Type: `Header Auth`
- Header Auth: `N8N_WEBHOOK_SECRE`

**Headers:**
```json
{
  "X-CALLBACK-SIGNATURE": "500"
}
```

**Body (JSON):**
```json
{
  "status": "processing",
  "admin_message_id": "{{ $('Webhook-homework').item.json.body.original_admin_message_id || $('Webhook-homework').item.json.body.admin_message_id }}"
}
```

**Важно:** Этот узел **НЕ обязательный**. Бот сам управляет таймером. Но если хотите показывать админам уведомление "ИИ начал проверку" — оставьте его.

**Настройка:**
- Run Once for All Items: `true`
- Error Handling: `Continue On Fail` (чтобы ошибка не ломала основной workflow)

---

#### Узел 9: HTTP Request (ОТПРАВКА РЕЗУЛЬТАТА БОТУ) ⭐️

**КРИТИЧЕСКИ ВАЖНО:**

**URL (Expression):**
```
{{ $('Webhook-homework').item.json.body.callback_webhook_url_result }}
```

**НЕ ПРАВИЛЬНО:**
```
https://bot.indikov.ru/webhook/n8n_hw_result  ❌
http://bot:8080/n8n_hw_result  ❌
```

**ПРАВИЛЬНО:**
```
{{ $('Webhook-homework').item.json.body.callback_webhook_url_result }}  ✅
```

**Authentication:**
- Generic Auth Type: `Header Auth`
- Header Auth: `N8N_WEBHOOK_SECRE`

**Headers:**
```json
{
  "X-CALLBACK-SIGNATURE": "500"
}
```

**Body (JSON):**
```json
{
  "feedback_text": "{{ $('Code').item.json.feedback_text }}",
  "is_approved": "{{ $('Code').item.json.is_approved }}",
  "original_admin_message_id": "{{ $('Webhook-homework').item.json.body.original_admin_message_id || $('Webhook-homework').item.json.body.admin_message_id }}",
  "student_user_id": "{{ $('Edit Fields').item.json.student_user_id }}",
  "course_numeric_id": "{{ $('Webhook-homework').item.json.body.course_numeric_id }}",
  "lesson_num": "{{ $('Webhook-homework').item.json.body.lesson_num }}"
}
```

---

### 3. Таймер ДЗ

**Как работает:**
1. Студент отправляет ДЗ
2. Админ видит: `🤖 До AI-проверки: 34 сек`
3. Таймер: 34 → 24 → 14 → 4 → 0 сек
4. Когда 0: `⏳ ИИ проверяет ДЗ... (10 сек)`
5. ИИ отвечает → бот обновляет меню
6. Если ИИ не ответил через 102 сек (3×34) → авто-одобрение

---

### 4. Проверка работы

**Логи бота:**
```bash
docker compose logs bot | grep -E "n8n|callback|ДЗ"
```

**Ожидаемый вывод:**
```
📤 ДЗ #964 отправлено на n8n (возраст: 44 сек)
Отправка данных в n8n: URL=https://n8n.indikov.ru/webhook/..., callback_url=http://bot:8080/hwX.../n8n_hw_result
n8n OK. Статус: 200
Callback от n8n на /hwX.../n8n_hw_result с верным секретом
handle_homework_result: Запуск. approved=False
```

---

### 5. Частые ошибки

#### ❌ Ошибка 404
```
AxiosError: Request failed with status code 404
URL: https://bot.indikov.ru/hwX.../n8n_hw_result
```

**Причина:** Бот в polling режиме не слушал webhook пути

**Решение:** Добавлены маршруты в `main()`:
```python
app.router.add_post(f"/{WEBHOOK_SECRET_PATH_CONF.strip('/')}/n8n_hw_result", handle_n8n_hw_approval)
```

#### ❌ Ошибка "message is not modified"
```
Telegram server says - Bad Request: message is not modified
```

**Причина:** Попытка отправить то же самое сообщение

**Решение:** Игнорировать ошибку (нормальное поведение)

#### ❌ Некорректные ID в callback
```
Некорректные или нулевые ID в колбэке от n8n: {'admin_message_id': '123'}
```

**Причина:** Первый HTTP Request отправляет тестовые данные

**Решение:** Удалить первый HTTP Request или настроить правильно

---

### 6. Экспорт workflow

Файл: `AI_n8n.json`

Импортировать в n8n:
1. Settings → Import
2. Выбрать файл
3. Настроить credentials:
   - `N8N_WEBHOOK_SECRE` (Header Auth)
   - `TelegramApi` (для Get a file)
   - `OpenRouter account` (для Agent)

---

## 📚 Ссылки

- `CLAUDE.md` — важные договорённости
- `GOALS2.md` — история изменений (Fix 7, Fix 8)
- `README.md` — быстрый старт
