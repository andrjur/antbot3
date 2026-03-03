
# AntBot v4 - История разработки и архитектурные решения

Этот файл содержит лог принятых решений, исправленных багов и инфраструктурных особенностей проекта.
**ОБЯЗАТЕЛЬНО К ПРОЧТЕНИЮ** перед внесением изменений в код или архитектуру.

---

## 🤖 n8n Workflow — Исправление ошибок мультимодальной проверки ДЗ (01.03.2026)

### Fix 1: Ошибка «Нет бинарных данных» в Process Photo

**Проблема:**
Нода **Process Photo** падала с ошибкой:
```
Нет бинарных данных [line 5]
```

**Диагностика:**
В INPUT ноды **Get a file** видны только метаданные файла:
- `file_id`: AgACAgIA...
- `file_size`: 75569
- `file_path`: photos/file-1.jpg

Но **binary data** отсутствует!

**Причина:**
В ноде **Get a file** не была включена опция **Download**. Без этой опции Telegram Node возвращает только метаданные файла, но не загружает само бинарное содержимое.

**Решение (вручную в n8n):**

1. Открыть ноду **Get a file** в воркфлоу
2. Включить переключатель **Download** (должен стать зелёным)
3. Исправить выражение в поле **File ID**:
   - ❌ Было: `{{ $json.homework_file_id }}`
   - ✅ Стало: `{{ $('Edit Fields').item.json.hw_file_id }}`
4. Сохранить ноду

**Почему `$('Edit Fields').item.json.hw_file_id`:**
- Нода **Edit Fields** создаёт поле `hw_file_id` из `$json.body.homework_file_id`
- В потоке данных после **Edit Fields** нужно обращаться к созданному полю
- Синтаксис `$('NodeName').item.json.fieldName` берёт данные напрямую из указанной ноды

**Результат:**
После включения **Download** нода **Get a file** начинает загружать файл из Telegram и помещает его в `binary.data`. Нода **Process Photo** успешно конвертирует в Base64.

---

### Fix 2: Ошибка «JSON parameter needs to be valid JSON» в OpenRouter AI

**Проблема:**
Нода **OpenRouter AI** показывала ошибку валидации JSON.

**Причина:**
Попытка вставить сложный объект (`messages_content` array) напрямую в JSON Body через `{{ $json.messages_content }}` приводила к `[object Object]`.

**Решение:**
Добавлена промежуточная Code нода **Prepare OpenRouter JSON** которая готовит полный объект запроса:

```javascript
// Code нода: Prepare OpenRouter JSON
const studentName = $('Edit Fields').item.json.student_name || 'Студент';
const lessonDesc = $('Edit Fields').item.json.lesson_desc || 'Оценить ДЗ';
const messagesContent = $input.item.json.messages_content || [];

return [{
  json: {
    openrouter_request: {
      model: 'google/gemini-2.5-flash',
      response_format: { type: 'json_object' },
      messages: [
        {
          role: 'system',
          content: `Ты мудрый наставник... ${studentName}... ${lessonDesc}`
        },
        {
          role: 'user',
          content: messagesContent
        }
      ]
    }
  }
}];
```

В ноде **OpenRouter AI** используется простое выражение:
```javascript
={{ $json.openrouter_request }}
```

**Схема:**
```
Prepare AI Messages → Prepare OpenRouter JSON → OpenRouter AI
                        (Code нода)           (={{ $json.openrouter_request }})
```

---

### Fix 3: Credential Type не выбран в OpenRouter AI

**Проблема:**
В ноде **OpenRouter AI** не выбирался Credential Type в выпадающем списке.

**Причина:**
В параметрах ноды использовалось `genericAuthType: "openRouterApi"` вместо `nodeCredentialType: "openRouterApi"`.

**Решение:**
Изменено с `genericAuthType` на `nodeCredentialType`:

```json
"authentication": "predefinedCredentialType",
"nodeCredentialType": "openRouterApi",
"credentials": {
  "openRouterApi": {
    "id": "BT6u6hYxUcBltOkv",
    "name": "OpenRouter account"
  }
}
```

**Важно:**
- `genericAuthType` — для простых типов (httpHeaderAuth, httpBasicAuth)
- `nodeCredentialType` — для специальных типов (openRouterApi, oAuth2Api, OAuth)

---

### Fix 4: Prepare Text Messages шёл напрямую в OpenRouter AI

**Проблема:**
Нода **Prepare Text Messages** была подключена напрямую к **OpenRouter AI**, минуя **Prepare OpenRouter JSON**. Это приводило к ошибке потому что **Prepare Text Messages** создаёт `messages_content`, но не создаёт полный объект запроса `openrouter_request`.

**Решение:**
Переключить connection:
- ❌ Было: **Prepare Text Messages** → **OpenRouter AI**
- ✅ Стало: **Prepare Text Messages** → **Prepare OpenRouter JSON** → **OpenRouter AI**

Аналогично для ветки с файлами:
**Prepare AI Messages** → **Prepare OpenRouter JSON** → **OpenRouter AI**

---

### Fix 5: Добавлена поддержка голосовых сообщений (voice) и аудио (02.03.2026)

**Проблема:**
Студенты не могли отправить голосовое сообщение (voice) или аудиофайл как домашнее задание. Бот отвечал "Неподдерживаемый тип контента".

**Причина:**
В функции `handle_homework()` не было обработки `message.voice` и `message.audio`.

**Решение (в main.py):**

1. **Добавлена обработка voice/audio в определении типа контента:**
```python
elif message.voice:
    homework_type = "Голосовое сообщение"
    text = message.caption or ""
    file_id = message.voice.file_id
    admin_message_content = f"🎤 Голосовое: {file_id}\n✏️ Описание: {md.quote(text)}"
elif message.audio:
    homework_type = "Аудио"
    text = message.caption or ""
    file_id = message.audio.file_id
    admin_message_content = f"🎵 Аудио: {file_id}\n✏️ Описание: {md.quote(text)}"
```

2. **Добавлена отправка voice/audio в ADMIN_GROUP_ID:**
```python
elif message.voice:
    sent_admin_message = await bot.send_voice(
        chat_id=ADMIN_GROUP_ID,
        voice=message.voice.file_id,
        caption=caption_with_description,
        reply_markup=admin_keyboard,
        parse_mode=None
    )
elif message.audio:
    sent_admin_message = await bot.send_audio(
        chat_id=ADMIN_GROUP_ID,
        audio=message.audio.file_id,
        caption=caption_with_description,
        reply_markup=admin_keyboard,
        parse_mode=None
    )
```

3. **Добавлена проверка типа ДЗ:**
```python
# Если ожидается текст, но прислали медиа с подписью — это ок
if expected_hw_type == "text" and submitted_content_type in ["photo", "video", "document", "animation", "voice", "audio"] and message.caption:
    is_type_allowed = True

# Если ожидается audio, то voice тоже подходит (и наоборот)
if expected_hw_type in ["audio", "voice"] and submitted_content_type in ["audio", "voice"]:
    is_type_allowed = True
```

**Результат:**
- Студенты могут отправлять голосовые сообщения (🎤 voice) как ДЗ
- Студенты могут отправлять аудиофайлы (🎵 audio) как ДЗ
- Голосовые/аудио отправляются в ADMIN_GROUP_ID с кнопками "Принять/Отклонить"
- n8n получает `homework_file_id` и может обработать через OpenRouter AI (Gemini поддерживает audio)

**Важно:**
- Голосовые сообщения приходят в формате `voice` (ogg/opus)
- Аудиофайлы приходят в формате `audio` (mp3, m4a, и т.д.)
- Оба типа поддерживают `caption` (текстовое описание)
- Для голосовых caption не отображается в Telegram клиентах, но API его принимает

---

### Fix 6: Исправление ошибок в логах n8n (02.03.2026)

**Проблема:**
В логах n8n наблюдались ошибки:

```bash
ValidationError: The 'X-Forwarded-For' header is set but the Express 'trust proxy' setting is false
Error fetching feature flags Error [PostHogFetchHttpError]: HTTP error while fetching PostHog: 504
Failed to start Python task runner in internal mode. because Python 3 is missing
```

**Диагностика:**

1. **X-Forwarded-For / trust proxy:**
   - Cloudflare передаёт заголовок `X-Forwarded-For` с IP клиента
   - n8n по умолчанию не доверяет прокси (`trust proxy = false`)
   - express-rate-limit не может корректно идентифицировать пользователей

2. **PostHog 504:**
   - n8n пытается отправить телеметрию в PostHog (аналитика использования)
   - Сервер недоступен или таймаут
   - Это только "шум" в логах, на работу не влияет

3. **Python runner:**
   - n8n пытается запустить Python task runner в internal mode
   - В контейнере нет Python 3
   - Это warning, на работу не влияет (если не используешь Python код)

**Решение:**

Добавлены переменные окружения в `docker-compose.yml`:

```yaml
environment:
  - N8N_TRUST_PROXY=true              # Уже было ✅
  - N8N_DIAGNOSTICS_ENABLED=false     # Отключает диагностику
  - N8N_TELEMETRY_ENABLED=false       # Отключает телеметрию PostHog
  - N8N_VERSION_NOTIFICATIONS_ENABLED=false  # Отключает проверку версий
```

**Результат:**
- ✅ n8n доверяет Cloudflare (правильный rate limiting)
- ✅ Нет ошибок PostHog 504 в логах
- ✅ Чище логи, меньше шума

**Важно:**
- `N8N_TRUST_PROXY=true` — критично для правильной работы за Cloudflare
- Остальные переменные — опциональны, для чистоты логов
- Python runner warning можно игнорировать (если не используешь Python код в n8n)

---

### Fix 7: Гибкая проверка типов ДЗ (02.03.2026)

**Проблема:**
Студенты не могли сдать ДЗ "более крутым" способом. Например:
- Ожидается текст → нельзя сдать фото с решением
- Ожидается фото → нельзя сдать видео
- Ожидается аудио → нельзя сдать голосовое

**Причина:**
Жёсткая проверка типа: `submitted_content_type == expected_hw_type`

**Решение:**

Введена **иерархия "крутости"** типов контента:

```
text < photo, document, voice, audio, video, animation
photo < video (видео "круче" фото)
audio ≈ voice ≈ video (кружочек)
```

**Новая логика:**

| Ожидается | Можно сдать |
|-----------|--------------|
| `text` | текст, фото, документ, голосовое, аудио, видео, анимация |
| `photo` | фото, видео (видео "круче") |
| `audio` | аудио, голосовое, видео (кружочек) |
| `voice` | голосовое, аудио, видео (кружочек) |
| `video` | видео (с проверкой размера) |
| `document` | документ, фото (скан) |

**Проверка размера видео:**
- Максимум: **10 МБ**
- Если больше → предупреждение с указанием размера

**Код:**
```python
# Проверка размера видео
if submitted_content_type == "video":
    video_size = message.video.file_size
    MAX_VIDEO_SIZE = 10 * 1024 * 1024  # 10 МБ
    if video_size > MAX_VIDEO_SIZE:
        await message.reply(
            f"⚠️ Видео слишком большое ({video_size / 1024 / 1024:.1f} МБ).\n"
            f"Максимальный размер: 10 МБ."
        )
        return

# Гибкая проверка
if expected_hw_type == "text":
    # Текст — самый гибкий, медиа "круче"
    if submitted_content_type in MEDIA_TYPES:
        logger.info(f"📚 ДЗ текстовое, но студент прислал {submitted_content_type} — это даже лучше!")
```

**Результат:**
- ✅ Студенты могут сдавать ДЗ "более крутым" способом
- ✅ Фото рукописного решения → ок для текстового ДЗ
- ✅ Голосовое с размышлениями → ок для текстового ДЗ
- ✅ Видео-кружочек → ок для аудио ДЗ
- ✅ Видео > 10 МБ → вежливое предупреждение

**Логирование:**
```
📚 ДЗ текстовое, но студент прислал photo — это даже лучше!
📸 ДЗ фото, но студент прислал видео — отлично!
🎵 ДЗ аудио, студент прислал voice — ок!
```

---

### Fix 8: n8n 2.8+ сломал синтаксис — items[0] → $input.first() (03.03.2026)

**Проблема:**
В n8n версии 2.8+ изменился API для Code нод. Старый синтаксис `items[0]` больше не работает.

**Симптомы:**
```
Error in node 'Process Photo'
Cannot find name 'items'
```

**Причина:**
n8n 2.8+ ввёл новый API:
- ❌ `items[0]` → больше не работает
- ✅ `$input.first()` → новый способ получить первый item
- ✅ `$input.all()` → вернуть все items
- ✅ `$input.item.json` → работа с текущим item

**Что сломалось в воркфлоу:**

1. **Process Photo:**
```javascript
// ❌ БЫЛО:
const binaryData = items[0].binary?.data;
items[0].json.base64_image = binaryData.data;
return items;

// ✅ СТАЛО:
const binaryData = $input.first().binary?.data;
$input.first().json.base64_image = binaryData.data;
return $input.all();
```

2. **Process Audio:**
```javascript
// ❌ БЫЛО:
items[0].json.audio_data = binaryData.data;
return items;

// ✅ СТАЛО:
$input.first().json.audio_data = binaryData.data;
return $input.all();
```

3. **Process Video:**
```javascript
// ❌ БЫЛО:
items[0].json.audio_data = binaryData.data;
return items;

// ✅ СТАЛО:
$input.first().json.audio_data = binaryData.data;
return $input.all();
```

**Решение (пошагово):**

### Шаг 1: Обнови все Code ноды в n8n_flow.json

Замени в каждой Code ноде:

| Было | Стало |
|------|-------|
| `items[0].binary` | `$input.first().binary` |
| `items[0].json` | `$input.first().json` |
| `return items` | `return $input.all()` |

**Ноды которые нужно исправить:**
- Process Photo
- Process Audio
- Process Video

### Шаг 2: Проверь остальные Code ноды

Остальные ноды используют `$input.item.json` — это **правильно**, их менять не нужно!

**Проверь:**
- Prepare Text Messages ✅ (использует `$input.item.json`)
- Route File Type ✅ (использует `$input.item.json`)
- Prepare AI Messages ✅ (использует `$input.item.json`)
- Prepare OpenRouter JSON ✅ (использует `$input.item.json`)
- Parse JSON Response ✅ (использует `$input.item.json`)

### Шаг 3: Отправь на сервер и обнови воркфлоу

```bash
# На сервере:
cd ~/antbot4
git pull origin main

# В n8n:
# 1. Открой воркфлоу
# 2. Import From File → выбери n8n_flow.json
# 3. Сохрани (Ctrl+S)
# 4. Активируй воркфлоу
```

### Шаг 4: Протестируй

1. Отправь ДЗ с фото
2. Проверь что **Process Photo** выполняется без ошибок
3. Проверь что **binary data** передаётся дальше

**Ожидаемый результат:**
```
📸 Фото: 67327 байт, MIME: image/jpeg
```

---

## 🧪 Гипотезы и отладка

### Гипотеза 1: binary data теряется в Route File Type

**Проблема:** Process Photo не получает binary data.

**Причина:** Route File Type создаёт новый json объект, но не передаёт binary.

**Решение:**
```javascript
// В Route File Type:
return [{
  json: {
    ...$input.item.json,
    file_type: fileType,
    mime_type: mimeType,
    route_output: output
  },
  binary: $input.item.json.binary  // ← Сохраняем binary!
}];
```

### Гипотеза 2: Get a file не скачивает файл

**Проблема:** Get a file возвращает только метаданные, без binary.

**Проверка:**
1. Открой ноду **Get a file**
2. Проверь параметр **Download** → должен быть `true` (зелёный)

**Решение:**
```json
{
  "resource": "file",
  "fileId": "={{ $('Edit Fields').item.json.hw_file_id }}",
  "download": true,  // ← Обязательно!
  "additionalFields": {}
}
```

### Гипотеза 3: Неверное выражение File ID

**Проблема:** Get a file получает `undefined` вместо file_id.

**Причина:** Используется `$json.homework_file_id`, но поле называется `hw_file_id` и создаётся в Edit Fields.

**Решение:**
```javascript
// ❌ НЕПРАВИЛЬНО:
{{ $json.homework_file_id }}

// ✅ ПРАВИЛЬНО:
{{ $('Edit Fields').item.json.hw_file_id }}
```

### Гипотеза 4: Ошибка "Cannot find name 'items'"

**Проблема:** n8n 2.8+ не знает `items`.

**Решение:** Заменить все `items[0]` на `$input.first()` (см. выше).

---

## 📋 Чек-лист отладки binary data

Если Process Photo/Audio/Video не получает binary data:

1. ✅ **Get a file** → `download: true`
2. ✅ **Get a file** → File ID: `{{ $('Edit Fields').item.json.hw_file_id }}`
3. ✅ **Route File Type** → передаёт `binary: $input.item.json.binary`
4. ✅ **Process Photo/Audio/Video** → использует `$input.first().binary`
5. ✅ **Process Photo/Audio/Video** → возвращает `$input.all()`

---

## 🎯 Итоговая таблица изменений n8n 2.8+

| Конструкция | До 2.8 | 2.8+ |
|-------------|--------|------|
| Первый item | `items[0]` | `$input.first()` |
| Все items | `items` | `$input.all()` |
| Текущий item | N/A | `$input.item` |
| JSON item | `items[0].json` | `$input.first().json` |
| Binary data | `items[0].binary` | `$input.first().binary` |
| Return items | `return items` | `return $input.all()` |

---

### Fix 9: Правильная маршрутизация через Switch ноду (03.03.2026)

**Проблема:**
Все 3 ноды (Process Photo, Audio, Video) запускались одновременно, даже когда студент отправлял только фото.

**Причина:**
Code нода **Route File Type** не разделяет потоки физически. Если от одной ноды отходят 3 связи к другим нодам, n8n запускает **все три одновременно**, передавая им одни и те же данные.

**Что происходило:**
1. Студент отправляет фото
2. **Route File Type** получает данные
3. n8n запускает одновременно:
   - **Process Photo** → ✅ срабатывает
   - **Process Audio** → ❌ падает с ошибкой (нет audio data)
   - **Process Video** → ❌ падает с ошибкой (нет video data)

**Решение:**
Использовать специальную ноду-**Switch** которая направляет поток **только по одной** ветке.

---

## 🔧 Как работает Switch маршрутизация

### Схема:

```
Get a file → Switch → (выход 0: image) → Process Photo
                      (выход 1: audio) → Process Audio
                      (выход 2: video) → Process Video
```

### Настройка Switch:

**Параметры:**
- **Data Type:** `String`
- **Value:** `={{ $json.mimeType }}`

**Routing Rules:**
1. Rule 1: `Starts with` → `image` (выход 0)
2. Rule 2: `Starts with` → `audio` (выход 1)
3. Rule 3: `Starts with` → `video` (выход 2)

---

## 📝 Изменения в Process Photo/Audio/Video

### Было (с Route File Type):
```javascript
// Process Photo
const fileType = $input.first().json.file_type;
if (fileType !== 'image') return [];  // ← Проверка внутри ноды

const binaryData = $input.first().binary?.data;
// ...
```

### Стало (со Switch):
```javascript
// Process Photo
const binaryData = $input.first().binary?.data;  // ← Без проверки fileType!
// ...
```

**Почему:**
Switch уже отфильтровал по типу файла — в Process Photo попадут **только image**!

---

## ✅ Результат:

**Теперь:**
- ✅ Прилетает фото → Switch видит `mimeType: image/jpeg` → запускает **ТОЛЬКО** Process Photo
- ✅ Прилетает аудио → Switch видит `mimeType: audio/ogg` → запускает **ТОЛЬКО** Process Audio
- ✅ Прилетает видео → Switch видит `mimeType: video/ogg` → запускает **ТОЛЬКО** Process Video

**Преимущества:**
- ❌ Нет лишних ошибок в логах
- ❌ Нет необходимости в "Continue On Fail"
- ✅ Чистая и понятная маршрутизация
- ✅ Работает надёжно

---

## 📋 Чек-лист настройки Switch:

1. ✅ **Switch** стоит после **Get a file**
2. ✅ **Value 1:** `={{ $json.mimeType }}`
3. ✅ **Rule 1:** `Starts with` → `image` (выход 0 → Process Photo)
4. ✅ **Rule 2:** `Starts with` → `audio` (выход 1 → Process Audio)
5. ✅ **Rule 3:** `Starts with` → `video` (выход 2 → Process Video)
6. ✅ В **Process Photo/Audio/Video** нет проверки `fileType` (не нужна!)

---

## 🧪 Тестирование:

**Отправь ДЗ с фото:**
1. ✅ **Get a file** → green check
2. ✅ **Switch** → green check (output 0)
3. ✅ **Process Photo** → green check
4. ⚪ **Process Audio** → не запускается (серая)
5. ⚪ **Process Video** → не запускается (серая)

**Отправь ДЗ с голосовым:**
1. ✅ **Get a file** → green check
2. ✅ **Switch** → green check (output 1)
3. ⚪ **Process Photo** → не запускается (серая)
4. ✅ **Process Audio** → green check
5. ⚪ **Process Video** → не запускается (серая)

---

## 📊 Сравнение подходов:

| Подход | Code нода | Switch нода |
|--------|-----------|-------------|
| Маршрутизация | ❌ Все 3 запускаются | ✅ Только одна |
| Ошибки | ❌ 2 из 3 падают | ✅ Нет ошибок |
| Continue On Fail | ⚠️ Нужно | ❌ Не нужно |
| Логи | ❌ Грязные | ✅ Чистые |
| Сложность | ⚠️ Средняя | ✅ Простая |

---

### Fix 10: Очистка run data + финальная проверка OpenRouter AI (03.03.2026)

**Проблема 1: Ноды не открываются (зависает интерфейс)**

**Причина:**
Когда студент присылает фото, нода Process Photo конвертирует его в Base64. Картинка превращается в **текст длиной 5–10 миллионов символов**.

Когда кликаешь на ноду, браузер пытается отрендерить этот гигантский кусок текста в боковой панели (OUTPUT) и **намертво зависает**.

**Решение:**
Очистить тестовые данные:
1. На верхней панели n8n найти иконку **Корзины 🗑️ (Clear run data)**
2. Нажать её
3. Данные гигантской картинки очистятся из памяти браузера
4. Все ноды снова будут открываться моментально

**Важно:**
- Делать это **после каждого тестирования** с фото/видео
- Иначе браузер будет зависать при попытке открыть ноду

---

**Проблема 2: Ошибка `JSON parameter needs to be valid JSON`**

**Причина:**
В ноде **OpenRouter AI** в поле **JSON** остался старый код:
```javascript
{{ JSON.stringify($json.messages_content) }}
```

**Решение:**
1. Очистить данные (иконкой корзины наверху)
2. Открыть ноду **OpenRouter AI**
3. В поле **JSON** удалить абсолютно всё
4. Вставить ровно одну строчку:
```javascript
={{ $json.openrouter_request }}
```

**Важно:**
- Обязательно **один знак равно** в начале: `={{`
- Нет `=={{` (два равно) — это ломает парсер
- Нет `JSON.stringify()` — объект уже готов

---

## ✅ Итоговая проверка воркфлоу:

**Перед тестированием:**
1. ✅ **Switch** настроен (3 правила: image, audio, video)
2. ✅ **Process Photo/Audio/Video** используют `const item` + `return []`
3. ✅ **OnError** = `stopWorkflow` (не `continueErrorOutput`)
4. ✅ **Prepare OpenRouter JSON** использует `.first()`
5. ✅ **OpenRouter AI** имеет `={{ $json.openrouter_request }}`
6. ✅ **Очищены run data** (корзина 🗑️)

**После тестирования:**
- ✅ Нажать **Корзина 🗑️** для очистки данных
- ✅ Иначе ноды будут зависать при открытии

---

### Fix 11: Ошибка импорта "Could not find property option" (03.03.2026)

**Проблема:**
При импорте воркфлоу в n8n ошибка:
```
Problem importing workflow
Could not find property option
```

**Причина:**
В JSON файле были пустые `"options": {}` в параметрах нод. n8n не может распарсить пустой объект options в некоторых версиях нод.

**Где было:**
```json
"parameters": {
  "authentication": "headerAuth",
  "options": {}  // ← ПУСТОЙ OBJECT ЛОМАЛ ИМПОРТ!
}
```

**Решение:**
Удалить все пустые `"options": {}` из JSON файла.

**Что удалено:**
- `Webhook-homework` → `"options": {}`
- `Edit Fields` → `"options": {}`
- `If` → `"options": {}`
- `Switch` → `"options": {}`
- `Code` ноды → `"options": {}`
- `HTTP Request` → `"options": {}`

**Результат:**
```json
"parameters": {
  "authentication": "headerAuth"
  // ← options удалён
}
```

**Как сделано:**
Python скрипт удалил все вхождения `, "options": {}` из JSON файла.

---

## 🏗️ Инфраструктура и Архитектура

### 1. Маршрутизация и Порты (Docker + Cloudflare)
**Проблема:** Бот и n8n находятся на одном сервере. n8n по умолчанию занимает порты 80 и 443. Телеграм отправляет вебхуки только на ограниченный список портов (443, 80, 8443, 88). Бот не мог получать обновления, так как порт 443 перехватывал n8n.
**Решение (Origin Rules):**
- Бот внутри Docker слушает **IPv4 `0.0.0.0`** на порту `8080`.
- В Cloudflare настроено **Origin Rule** для поддомена `bot.indikov.ru`: весь входящий трафик с порта 443 прозрачно перенаправляется сервером Cloudflare на порт `8080` сервера.
- Для n8n настроено аналогичное правило: `n8n.indikov.ru` перенаправляется на порт `5678`.
- В `.env` боту передается чистый URL `https://bot.indikov.ru` (без портов), так как Телеграм общается с Cloudflare по стандартному 443.

### 2. Настройки SSL
**Проблема:** Ошибка `525 SSL Handshake Failed` или `521 Web server is down`.
**Решение:** В Cloudflare для всего домена установлен режим SSL/TLS: **Flexible**. Это означает, что трафик от клиента до Cloudflare зашифрован (HTTPS), а от Cloudflare до сервера идет по HTTP, так как внутри Docker контейнеров (бот и n8n) нет SSL-сертификатов.

### 3. Производительность сервера (ОЗУ)
**Проблема:** Сервер зависал (CPU 100%) при запуске/обновлении тяжелых контейнеров (n8n).
**Решение:** На сервере добавлен Swap-файл (файл подкачки) на 2 ГБ. `vm.swappiness` установлен в 10. При деплое обязательно делать `docker image prune -f` для очистки места.

---

## 🐛 Исправленные Баги (Core Logic)

### Fix 1: Ошибка `Character '.' is reserved` (MarkdownV2)
**Проблема:** При попытке отправить системное сообщение об отсутствии контента урока, бот падал с ошибкой Telegram API, так как в тексте использовался `parse_mode=ParseMode.MARKDOWN_V2`, но точки (`.`) не были экранированы.
**Решение:** В системных сообщениях (например, `_handle_missing_lesson_content` и `send_to_user`) отключен Markdown (`parse_mode=None`). Использовать MarkdownV2 стоит только там, где жестко контролируется экранирование функцией `escape_md()`.

### Fix 2: Критичная ошибка парсинга Markdown в админ-меню (27.02.2026)
**Проблема:** После добавления версии бота в админ-меню, команда `/start` перестала работать. Ошибка:
```
TelegramBadRequest: can't parse entities: Can't find end of the entity starting at byte offset 524
```
**Причина:** `GIT_VERSION` содержал спецсимволы (например, `/`, `-`, пробелы), которые ломали Markdown парсинг при использовании обратных кавычек `` ` ``.

**Решение:**
- Убраны обратные кавычки вокруг версии: `` `{GIT_VERSION}` `` → `{GIT_VERSION}`
- `parse_mode` изменён с `"Markdown"` на `None`
- Бот теперь работает даже если версия содержит спецсимволы

**Урок:** Использовать Markdown только там, где можно гарантировать отсутствие спецсимволов. Для версий и технических данных — `parse_mode=None`.

### Fix 3: Ошибка await в get_next_lesson_time() (27.02.2026)
**Проблема:** Функция `get_next_lesson_time()` вызывалась с `await`, но она не async.
```
TypeError: object str can't be used in 'await' expression
```
**Решение:** Убран `await` из всех вызовов:
- `send_main_menu()` (строка ~9396)
- `handle_homework()` (строка ~9025)
- `send_lesson_to_user()` (строка ~1017)

**Урок:** Проверять является ли функция async перед использованием await.

**Важно:** После фикса нужно **пересобрать бота** (`docker compose up -d --build`), иначе контейнер будет использовать закэшированную версию кода.

### Fix 4: Таймер ДЗ — обратный и прямой отсчёт (28.02.2026)
**Проблема:** В начальном сообщении было "0 сек назад", что сбивало с толку.

**Решение:** Функция `run_hw_countdown` переписана:
1. Начальное сообщение: "🤖 До AI-проверки: 34 сек"
2. Таймер уменьшается: 34 сек → 24 сек → 14 сек → 4 сек → 0 сек
3. Когда время вышло: "⏳ ИИ проверяет ДЗ... (10 сек)" и считает вверх
4. Кнопки убираются когда таймер достигает 0

**Важно:** 
- Таймер показывает **оставшееся время** до отправки в n8n
- После отправки показывает **время проверки ИИ**
- Шаг = 10 сек (синхронно с `check_pending_homework_timeout`)

### Fix 5: n8n callback через BOT_INTERNAL_URL (polling режим)
**Проблема:** Бот работает в режиме **POLLING** (`WEBHOOK_MODE=false`), поэтому не обрабатывает webhook запросы от n8n.

**Симптом:**
```
AxiosError: Request failed with status code 404
URL: https://bot.indikov.ru/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL/n8n_hw_result
```

**Решение (2 варианта):**

**Вариант A: Включить webhook режим (рекомендуется)**
В `.env` на сервере:
```bash
WEBHOOK_MODE=true
```
Перезапуск: `docker compose restart bot`

**Вариант B: Использовать внутренний Docker URL**
В `.env` на сервере:
```bash
BOT_INTERNAL_URL=http://bot:8080
```
Перезапуск: `docker compose restart bot`

Тогда n8n будет использовать `http://bot:8080/webhook/n8n_hw_result` (внутренняя сеть Docker).

**Урок:** Для webhook callback от n8n бот должен слушать webhook URL (WEBHOOK_MODE=true) ИЛИ использовать внутренний Docker URL.

### Fix 9: Разделение UI-операций при ответе n8n
**Проблема:** При возврате ответа от ИИ, бот падал с ошибкой `Bad Request: message is not modified` и не публиковал вердикт в админку. Причина: бот пытался удалить кнопки, которые уже были удалены функцией таймера.

**Решение (28.02.2026):** В `handle_homework_result` вызов `edit_message_reply_markup` обернут в отдельный `try...except pass`. Теперь бот игнорирует эту ошибку и гарантированно отправляет `send_message` с результатами проверки от ИИ.

### Fix 10: Обработка статуса `processing` от n8n — КРИТИЧНЫЙ ФИКС
**Проблема:** n8n отправляет `{'status': 'processing', ...}` без `is_approved`. Бот обрабатывал это как `is_approved=False` и **ОТКЛОНЯЛ ДЗ**!

**Причина:** Проверка `status` была ПОСЛЕ очистки данных, где `is_approved` устанавливается в `False` по умолчанию.

**Решение (28.02.2026):**
- Проверка `status == 'processing'` перемещена **В САМОЕ НАЧАЛО** функции `handle_n8n_hw_approval()`
- Добавлено 8 логов для отладки
- `admin_message_id` берётся из `admin_message_id` **ИЛИ** `original_admin_message_id`
- Бот **игнорирует** обработку ДЗ если это статус `processing`
- Возвращает `200 OK` сразу

**Важно:** Порядок проверки критичен! Сначала `status`, потом очистка данных!

**Настройка HTTP Request1 в n8n:**
```json
{
  "status": "processing",
  "admin_message_id": "{{ $('Webhook-homework').item.json.body.original_admin_message_id }}"
}
```

**Проблема с обновлением сервера:**
После коммита нужно выполнять `git reset --hard origin/main` + `docker compose up -d --build`, иначе контейнер использует закэшированный код.

**Размышления (28.02.2026):**
HTTP Request1 в n8n **НЕ НУЖЕН**. Бот сам управляет таймером через `run_hw_countdown`. Два ответа на один webhook создают проблемы:
1. Первый запрос (`status: processing`) приходит без `is_approved`
2. Бот обрабатывает как `is_approved=False` → отклоняет ДЗ
3. Второй запрос (`is_approved: True`) игнорируется: "Попытка повторной обработки"

**Решение:** Оставить ТОЛЬКО финальный HTTP Request в n8n. Деактивировать или удалить HTTP Request1.

### Fix 11: Текст ответа ИИ не отправляется студенту
**Проблема:** Студент получает "❌ ДЗ отклонено" вместо "✅ ДЗ одобрено" + текст от ИИ.

**Причина:** 
1. Первый callback от n8n (`status: processing`) обрабатывался как `is_approved=False`
2. `handle_homework_result` вызывался дважды (processing + final result)
3. Второй вызов игнорировался: "Попытка повторной обработки ДЗ. Игнорируем."

**Решение (28.02.2026):**
- Проверка `status` должна быть ДО любой обработки данных
- Логирование: `status=..., is_approved=...` для отладки
- Студент получает ПОЛНЫЙ текст ИИ (без урезания до 240 символов)
- Админ получает текст ИИ отдельным сообщением (reply на карточку ДЗ)
- Удаление кнопок вынесено в отдельный блок try-except

**Исправления в handle_homework_result:**
```python
# Студенту - ПОЛНЫЙ текст
if feedback_text:
    message += f"\n\n*Комментарий ИИ:*\n{escape_md(feedback_text)}"

# Админу - отдельное сообщение (reply на ДЗ)
await bot.send_message(
    chat_id=ADMIN_GROUP_ID,
    text=final_admin_notification,
    reply_to_message_id=message_id_to_process,
    parse_mode=None
)
```

**Файлы:** PATCH_MAIN.md, apply_patch.sh

**Размышления:**
Проблема `message is not modified` возникает когда Telegram видит что текст/кнопки не изменились. Решение — отправлять ОТДЕЛЬНОЕ сообщение вместо редактирования старого.

### Fix 12: Конфликт обработчиков (aiogram 3 Routing)
**Проблема:** Бот игнорировал команду `/upload_lesson` и другие команды админа. Команды перехватывались общим обработчиком `handle_text` (который ловит `F.text`), возвращали `UNHANDLED`, но дальше по цепочке не шли.
**Решение:**
1. Настроен строгий порядок регистрации обработчиков в файле (сверху вниз): Команды -> FSM -> Общие (text/media).
2. На общий `handle_text` и `handle_user_content` добавлены фильтры:
   - `StateFilter(None)` — срабатывает только если юзер не в режиме диалога.
   - `~F.text.startswith('/')` — игнорирует любые команды.

### Fix 4: Защита от дурака в `/add_course`
**Проблема:** Команда падала с ошибкой `invalid literal for int()`, если админ копировал ID группы с двумя тире (`--100...` вместо `-100...`). Также была ошибка отсутствия ключа `price`.
**Решение:** Добавлена очистка строки `raw_group_id.lstrip("-")` с принудительной подстановкой одного минуса. В `settings["activation_codes"]` жестко прописано добавление ключа `"price": 0`.

### Fix 5: Ошибка БД при ручной загрузке контента (`NOT NULL constraint failed`)
**Проблема:** При сохранении урока через `/upload_lesson` бот падал, так как таблица `group_messages` требует `message_id`, а он не передавался.
**Решение:** В SQL-запрос `INSERT INTO group_messages` добавлена передача `message.message_id` от исходного сообщения админа.

### Fix 6: Ошибка `charset` в aiohttp (Мониторинг Prometheus)
**Проблема:** Контейнер сыпал 500 ошибками при запросе к `/metrics`: `ValueError: charset must not be in content_type argument`.
**Решение:** В файле `services/metrics.py` параметр `charset="utf-8"` вынесен из строки `content_type` в отдельный аргумент `web.Response`, согласно новым требованиям библиотеки `aiohttp`.

---

## 🤖 Интеграция с n8n (ИИ-проверка ДЗ)

### 1. Проблема с "грязным" JSON (Red Nodes)
**Проблема:** Узел *Agent* падал с ошибкой формулы, если Питон-бот не передавал имя пользователя (например, при запуске по таймауту). ИИ писал лишний текст (приветствия) помимо JSON.
**Решение:**
- Написан "бронебойный" промпт. В n8n добавлена конструкция фолбэка имени: `...user_fullname || ...student_name || 'Студент'`.
- Добавлена логика парсинга картинок: если узел `Merge` содержит файл, ИИ видит маркер `[ПРИКРЕПЛЕНО ИЗОБРАЖЕНИЕ]` и анализирует его.

### 2. Затирание сообщения в Telegram API
**Проблема:** При попытке обновить статус ДЗ на "ИИ начал проверку", n8n затирал всё сообщение с текстом домашки студента, потому что Telegram API при редактировании требует передавать весь текст целиком.
**Решение:** Узел изменения сообщения перенесен на сторону Python-бота. В функции `run_hw_countdown` бот сам меняет строку таймера на "ИИ проверяет..." и убирает кнопки (`reply_markup=None`), когда таймер истекает. Из n8n удален узел отправки в Telegram (он только думает и возвращает ответ).

### 3. Ошибка 404 при Callback от n8n
**Проблема:** n8n не мог вернуть результат проверки Питон-боту, так как пытался стучаться по внутреннему Docker URL (`http://bot:8080/webhook/...`), который не был маршрутизирован.
**Решение:** Python-бот теперь динамически формирует внешний URL для возврата ответа (`callback_webhook_url_result`) на основе переменных `.env` (`WEBHOOK_HOST` + `WEBHOOK_SECRET_PATH`) и отправляет его в n8n в теле вебхука. Узел *HTTP Request* в n8n просто берет этот URL и шлет POST-запрос.

### 4. Авто-одобрение при сбое ИИ (Auto-approve fallback)
**Проблема:** Если n8n или OpenRouter ложились, ДЗ висело вечно в статусе "ожидание проверки".
**Решение (реализовано 27.02.2026):**
- `check_pending_homework_timeout()` проверяет ДЗ каждые **10 секунд** (было 60).
- Если ДЗ висит **3 × HW_TIMEOUT_SECONDS** (например, 102 сек при таймауте 34 сек) → бот вызывает `handle_homework_result()` с `is_approved=True`.
- Студент получает: "✅ Ваше ДЗ принято."
- Админам отправляется уведомление: "⚠️ ДЗ @username одобрено АВТОМАТИЧЕСКИ (ИИ не ответил за X мин Y сек)."
- Форматирование времени: `format_time_duration()` → "34 сек", "4 мин 2 сек", "2 ч 15 мин".

### 5. Интервал между уроками из settings.json (не хардкод)
**Проблема:** Интервал между уроками был захардкожен (12 часов для обычных, 5 мин для тест-режима). Нельзя было быстро протестировать курс на обычных пользователях.

**Решение (27.02.2026):**
- `message_interval` читается из `settings.json`
- Форматирование в человекочитаемый вид:
  - `0.03` → "2 мин"
  - `1` → "1 час"
  - `24` → "24 ч"
- В админ-меню показывается: "🕐 Интервал между уроками: X мин/часов"
- В тест-режиме показывается актуальный интервал из settings

**Пример settings.json:**
```json
{
  "message_interval": 0.03  // 2 минуты между уроками для теста
}
```

**Преимущества:**
- Можно быстро протестировать курс (поставить 2 минуты)
- Не нужно перезапускать бота для изменения интервала
- Гибкая настройка для разных курсов

### 6. Ошибка 521 (Web server is down) в n8n
**Проблема:** n8n не может отправить результат проверки ДЗ обратно в бота. Cloudflare возвращает ошибку 521.

**Причины и решения (подробно):**

#### A. Неверный callback URL в payload
**Симптом:** n8n стучится не туда.

**Диагностика:**
```bash
docker compose logs bot | grep "callback_base"
```

**Ожидаемый вывод:**
```
callback_base (внешний): https://bot.indikov.ru/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL
n8n callback URL: https://bot.indikov.ru/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL/n8n_hw_result
```

**Если неправильно:** Проверь `.env`:
```bash
cat .env | grep WEBHOOK
```

**Должно быть:**
```
WEBHOOK_HOST=https://bot.indikov.ru
WEBHOOK_SECRET_PATH=hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL
WEBHOOK_PATH=/webhook
```

**В коде бота (main.py):**
```python
# Формирование callback URL
host = WEBHOOK_HOST_CONF.rstrip("/")
secret_path = (WEBHOOK_SECRET_PATH_CONF or "").strip("/")
callback_base = f"{host}/{secret_path}" if secret_path else f"{host}/bot/"
callback_url = f"{callback_base}/n8n_hw_result"
```

#### B. Статичный URL в ноде HTTP Request (n8n)
**Симптом:** n8n игнорирует payload и шлет на старый URL.

**Диагностика:**
1. Открой воркфлоу в n8n
2. Найди ноду **HTTP Request** (отправляет результат боту)
3. Проверь поле **URL**

**❌ НЕПРАВИЛЬНО:**
```
https://bot.indikov.ru/webhook/n8n_hw_result
```

**✅ ПРАВИЛЬНО (Expression):**
```javascript
{{ $('Webhook-homework').item.json.body.callback_webhook_url_result }}
```

**Исправление:**
1. Кликни на поле URL
2. Нажми `⚙️` → "Add Expression" или `{{}}`
3. Вставь формулу выше
4. Сохрани воркфлоу

#### C. Cloudflare Origin Rules отсутствует
**Симптом:** Cloudflare не знает куда перенаправлять трафик.

**Диагностика:**
1. Cloudflare Dashboard → Rules → Origin Rules
2. Проверь наличие правила для `bot.indikov.ru`

**Если нет — создай:**
- **Rule name:** `Bot Redirect`
- **If hostname:** `equals` → `bot.indikov.ru`
- **Destination port:** `Rewrite to` → `8080`

**Проверка:**
```bash
# С локальной машины:
curl -I https://bot.indikov.ru/health/live
# Должен вернуть HTTP/2 200
```

#### D. Бот слушает неправильный хост
**Симптом:** Docker не пробрасывает порт.

**Диагностика:**
```bash
docker compose logs bot | grep "Порт приложения"
```

**Ожидаемый вывод:**
```
Порт приложения: 8080
```

**Проверь `.env`:**
```
WEBAPP_HOST=0.0.0.0  # ОБЯЗАТЕЛЬНО! Не localhost, не 127.0.0.1
WEB_SERVER_PORT=8080
```

**В docker-compose.yml:**
```yaml
services:
  bot:
    ports:
      - "8080:8080"  # Проброс порта
```

#### E. N8N_CALLBACK_SECRET не совпадает
**Симптом:** Бот отклоняет запрос с 403 Forbidden.

**Диагностика:**
```bash
# В .env бота:
cat .env | grep N8N_CALLBACK_SECRET

# В n8n (нода HTTP Request → Headers):
X-CALLBACK-SIGNATURE: 500
```

**Должно совпадать!**

**Решение:**
1. В `.env` бота: `N8N_CALLBACK_SECRET=500`
2. В n8n HTTP Request → Headers:
   - Name: `X-CALLBACK-SIGNATURE`
   - Value: `=500` (или Expression: `={{ '500' }}`)

---

## 🧪 Тестирование callback от n8n

**Шаг 1: Проверь логи при отправке ДЗ**
```bash
docker compose logs -f bot | grep -E "callback|n8n"
```

**Ожидаемый вывод:**
```
📤 ДЗ #123 отправлено на n8n (возраст: 34 сек)
callback_base (внешний): https://bot.indikov.ru/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL
n8n callback URL: https://bot.indikov.ru/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL/n8n_hw_result
```

**Шаг 2: Проверь логи n8n**
```bash
docker compose logs n8n | grep -E "POST|webhook"
```

**Ожидаемый вывод:**
```
"POST /webhook/aa46a723-619e-42e9-8e51-49ba51813718" 200
"POST https://bot.indikov.ru/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL/n8n_hw_result" 200
```

**Шаг 3: Ручной тест webhook**
```bash
# С сервера (vps):
curl -X POST https://bot.indikov.ru/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL/n8n_hw_result \
  -H "Content-Type: application/json" \
  -H "X-CALLBACK-SIGNATURE: 500" \
  -d '{"feedback_text":"Тест","is_approved":true,"student_user_id":123}'

# Должен вернуть 200 OK
```

---

## 📝 TODO / Открытые вопросы
- [ ] Протестировать все 4 сценария (текст, фото, аудио, видео)
- [ ] Рефакторинг main.py — вынести функции в отдельные модули
- [ ] Исправить конфликт вебхуков n8n (деактивировать дубль)
- [ ] Проверить баланс OpenRouter API

---

## 🔄 Мультимодальная проверка ДЗ — Update 01.03.2026 (v3)

### Критичные исправления воркфлоу (после импорта в n8n):

#### 5. Credential ошибка — httpHeaderAuth → openRouterApi
**Проблема:**
```
Credential with ID "BT6u6hYxUcBltOkv" does not exist for type "httpHeaderAuth".
```

**Причина:** Использовался `httpHeaderAuth` для OpenRouter, но нужен специальный `openRouterApi`.

**Решение:**
```json
"authentication": "predefinedCredentialType",
"genericAuthType": "openRouterApi",
"credentials": {
  "openRouterApi": {
    "id": "BT6u6hYxUcBltOkv",
    "name": "OpenRouter account"
  }
}
```

**Важно:** n8n имеет встроенный тип credentials для OpenRouter — нужно использовать его.

---

#### 6. Красный Expression в JSON Body — добавлена Prepare AI Messages нода
**Проблема:**
```json
"messages": "=[\n  {\n    \"role\": \"user\",\n    \"content\": [\n      {{ $json.base64_image ? ', {...}' : '' }}\n    ]\n  }\n]"
```
**Симптом:** Красное подчёркивание, ошибка синтаксиса.

**Причина:** Слишком сложная формула с условными выражениями внутри JSON строки.

**Решение:** Добавлена Code нода **Prepare AI Messages** (позиция 200, 480):

```javascript
// Подготовка messages content для OpenRouter AI
const hwText = $input.item.json.hw_text || $('Edit Fields').item.json.hw_text;
const audioWarning = $input.item.json.audio_warning || '';
const fileWarning = $input.item.json.file_warning || '';
const base64Image = $input.item.json.base64_image || null;
const mimeType = $input.item.json.mime_type || '';
const fileType = $input.item.json.file_type || '';
const audioData = $input.item.json.audio_data || null;

// Формируем text content
let textContent = `РАБОТА СТУДЕНТА:\n${hwText}`;
if (audioWarning) textContent += `\n\n⚠️ ${audioWarning}`;
if (fileWarning) textContent += `\n\n⚠️ ${fileWarning}`;

// Формируем messages content array
const messagesContent = [
  { type: "text", text: textContent }
];

// Добавляем image если есть
if (base64Image && mimeType) {
  messagesContent.push({
    type: "image_url",
    image_url: { url: `data:${mimeType};base64,${base64Image}` }
  });
}

// Добавляем audio если есть
if (audioData && (fileType === 'audio' || fileType === 'video')) {
  const audioFormat = mimeType.includes('ogg') ? 'ogg' : 'wav';
  messagesContent.push({
    type: "input_audio",
    input_audio: { data: audioData, format: audioFormat }
  });
}

return [{
  json: {
    ...$input.item.json,
    messages_content: messagesContent
  }
}];
```

**Схема:**
```
Process Photo ─┐
Process Audio ─┼→ Prepare AI Messages → OpenRouter AI
Process Video ─┘
```

---

#### 7. Нет messages_content в текстовой ветке — добавлена Prepare Text Messages
**Проблема:**
```
If (нет файла) → OpenRouter AI
                 ↓
            $json.messages_content = undefined ❌
```

**Симптом:** Ошибка при отправке текстового ДЗ — `messages_content` не определён.

**Решение:** Добавлена Code нода **Prepare Text Messages** (позиция -336, 640):

```javascript
// Текстовое ДЗ (без файлов) - подготовка messages content
const hwText = $input.item.json.hw_text || $('Edit Fields').item.json.hw_text;

const messagesContent = [
  { type: "text", text: `РАБОТА СТУДЕНТА:\n${hwText}` }
];

return [{
  json: {
    ...$input.item.json,
    messages_content: messagesContent
  }
}];
```

**Схема:**
```
If (нет файла) → Prepare Text Messages → OpenRouter AI
```

---

#### 8. JSON parameter needs to be valid JSON — правильный JavaScript-объект
**Проблема:**
```javascript
={
  "messages": [
    { "content": "{{ $json.messages_content }}" }  // ❌
  ]
}
```

**Симптом:** 
- Красное подчёркивание в JSON Body
- Ошибка: `JSON parameter needs to be valid JSON`
- Кавычки в hw_text ломают структуру JSON

**Причина:** 
- `={{ ... }}` с вложенными `{{ ... }}` создаёт конфликт парсинга
- Строковая конкатенация не экранирует спецсимволы

**Решение:** Использовать JavaScript-объект с интерполяцией:

```javascript
={{
  {
    "model": "google/gemini-2.5-flash",
    "response_format": { "type": "json_object" },
    "messages": [
      {
        "role": "system",
        "content": `Ты мудрый наставник на онлайн-курсе. Имя студента: ${$('Edit Fields').item.json.student_name}.\n\nКонтекст задания:\n${$('Edit Fields').item.json.lesson_desc}\n\nТВОИ ПРАВИЛА:\n1. Оцени работу...\n\nОтвет СТРОГО в формате JSON: {"is_approved": boolean, "feedback_text": "string"}`
      },
      {
        "role": "user",
        "content": $json.messages_content
      }
    ]
  }
}}
```

**Ключевые изменения:**
| Было | Стало |
|------|-------|
| `={ ... }` | `={{ { ... } }}` |
| `"{{ $json.value }}"` | `${$('Node').item.json.value}` |
| `{{ JSON.stringify(...) }}` | `$json.messages_content` (напрямую) |
| Строковая конкатенация | JavaScript интерполяция в `` ` `` |

**Важно:** 
- `={{ ... }}` — n8n парсит как JavaScript-объект
- `${...}` — безопасная интерполяция (экранирует кавычки)
- `$json.messages_content` — передаётся как объект (не строка)

---

### Архитектура (актуальная v3):

```
Webhook-homework → Edit Fields → If (hw_file_id не пуст?)
                                     ├─ ДА → Get a file → Route File Type
                                     │                    ├─ image → Process Photo ─┐
                                     │                    ├─ audio → Process Audio ─┤
                                     │                    └─ video → Process Video ─┤
                                     │                                              ↓
                                     │                              Prepare AI Messages
                                     │                              (messages_content)
                                     │                                              ↓
                                     └─ НЕТ → Prepare Text Messages ───────────────┴
                                              (messages_content)                    ↓
                                                                           OpenRouter AI
                                                                           (Gemini 2.5 Flash)
                                                                                    ↓
                                                                           Parse JSON Response
                                                                           (fallback на ошибку)
                                                                                    ↓
                                                                           HTTP Request → Боту
```

**Ключевые ноды:**
1. **Prepare Text Messages** — текстовое ДЗ → `messages_content`
2. **Prepare AI Messages** — фото/аудио/видео → `messages_content`
3. **OpenRouter AI** — `={{ { "messages": [...] } }}` (JavaScript-объект)

---

### Тесты (результаты):

#### ✅ Тест 1: Текст + фото (ДЗ #1041)
```
14:46:57 - 📤 ДЗ #1041 отправлено на n8n
14:47:50 - ✅ Получен callback от n8n
14:47:50 - 🔹 is_approved=False, feedback_text="Andrew, привет!..."
14:47:51 - ✅ ДЗ отклонено, студент получил вердикт
```

**Результат:** ✅ Работает! ИИ увидел фото, дал вердикт.

#### ❌ Тест 2: Фото (ДЗ #1043) — таймаут
```
15:17:04 - 📤 ДЗ #1043 отправлено на n8n
15:18:05 - ⚠️ ДЗ #1043 висит 104 сек → авто-одобрение
```

**Причина:** n8n не вернул callback.

**Возможные причины:**
1. OpenRouter API не ответил (баланс = 0?)
2. Ошибка парсинга JSON от Gemini
3. Process Photo не сохранил Base64

**Диагностика:**
```bash
docker compose logs n8n | grep -E "📸|❌|Error"
```

---

### Следующие шаги:

1. **Проверить баланс OpenRouter** — если 0, пополнить
2. **Проверить логи n8n** — есть ли ошибка от OpenRouter
3. **Тест с аудио** — проверить Process Audio ноду
4. **Тест с видео** — проверить Process Video ноду
5. **Тест текстового ДЗ** — после исправления #7

---

### Исправле��ия (после первых тестов):

#### 1. specifyBody: "useJson" → bodyParameters
**Проблема:** `useJson` ломал экспорт воркфлоу — n8n не сохранял JSON Body.

**Решение:** Переписал с `useJson` на `bodyParameters` (как в старом воркфлоу n8n-text-flow.json):

```json
"sendBody": true,
"bodyParameters": {
  "parameters": [
    {
      "name": "model",
      "value": "google/gemini-2.5-flash"
    },
    {
      "name": "response_format.type",
      "value": "json_object"
    },
    {
      "name": "messages",
      "value": "=[...JSON array...]"
    }
  ]
}
```

**Важно:** Теперь JSON передаётся как **Expression** (через `=` в начале строки).

#### 2. Process Audio/Video не сохраняли binary data
**Проблема:** Ноды сохраняли только метаданные (file_size, mime_type), но не сами данные для отправки в OpenRouter.

**Симптом:** `input_audio.data` был пустым → Gemini не получала аудио.

**Решение:** Добавил сохранение binary data:
```javascript
// Process Audio
items[0].json.audio_data = binaryData.data; // ← ДОБАВИТЬ

// Process Video  
items[0].json.audio_data = binaryData.data; // ← ДОБАВИТЬ
```

#### 3. Убрал лишние заголовки (Cloudflare не нужен)
**Проблема:** Заголовки `HTTP-Referer` и `X-Title` не нужны для internal calls.

**Решение:** Оставил только:
```json
"headerParameters": [
  {"name": "Authorization", "value": "Bearer {{$credentials.openRouterApi.apiKey}}"},
  {"name": "Content-Type", "value": "application/json"}
]
```

**Важно:** Бот и n8n общаются внутри Docker сети — Cloudflare не участвует!

#### 4. N8N_TRUST_PROXY=true
**Проблема:** Cloudflare передаёт `X-Forwarded-For`, n8n не доверяет.

**Решение:** Добавил в `docker-compose.yml`:
```yaml
services:
  n8n:
    environment:
      - N8N_TRUST_PROXY=true
```

---

### Архитектура (актуальная):

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker Network (internal)                │
│                                                             │
│  ┌─────────────┐         ┌─────────────┐                   │
│  │    Bot      │ ──────→ │     n8n     │                   │
│  │  :8080      │ ←────── │   :5678     │                   │
│  └─────────────┘  HTTP   └─────────────┘                   │
│       ↑  ↑          POST       ↑                           │
│       │  └─────────────────────┘                           │
│       │           Callback                                 │
│       │                                                     │
└───────┼─────────────────────────────────────────────────────┘
        │
        │ Cloudflare (только для внешних запросов)
        │
    ┌───┴────┐      ┌────────────┐
    │Telegram│      │   OpenRouter│
    │  API   │      │  (Gemini)   │
    └────────┘      └─────────────┘
```

**Важно:** 
- Telegram → Bot: через Cloudflare (внешний webhook)
- Bot → n8n: внутри Docker (прямой HTTP)
- n8n → OpenRouter: внешний HTTPS (API запрос)
- n8n → Bot: внутри Docker (callback на `http://bot:8080`)

---

### Тесты (результаты):

#### ✅ Тест 1: Текст + фото (ДЗ #1041)
```
14:46:57 - 📤 ДЗ #1041 отправлено на n8n
14:47:50 - ✅ Получен callback от n8n
14:47:50 - 🔹 is_approved=False, feedback_text="Andrew, привет!..."
14:47:51 - ✅ ДЗ отклонено, студент получил вердикт
```

**Результат:** ✅ Работает! ИИ увидел фото, дал вердикт.

#### ❌ Тест 2: Фото (ДЗ #1043) — таймаут
```
15:17:04 - 📤 ДЗ #1043 отправлено на n8n
15:18:05 - ⚠️ ДЗ #1043 висит 104 сек → авто-одобрение
```

**Причина:** n8n не вернул callback.

**Возможные причины:**
1. OpenRouter API не ответил (баланс = 0?)
2. Ошибка парсинга JSON от Gemini
3. Process Photo не сохранил Base64

**Диагностика:**
```bash
docker compose logs n8n | grep -E "📸|❌|Error"
```

---

### Следующие шаги:

1. **Проверить баланс OpenRouter** — если 0, пополнить
2. **Проверить логи n8n** — есть ли ошибка от OpenRouter
3. **Тест с аудио** — проверить Process Audio ноду
4. **Тест с видео** — проверить Process Video ноду

---

### Проблема
Старый воркфлоу использовал ноду **Agent** (LangChain) + **OpenRouter Chat Model**, что не позволяло:
1. Реально анализировать изображения (только текстовый маркер `[ПРИКРЕПЛЕНО ИЗОБРАЖЕНИЕ]`)
2. Обрабатывать голосовые сообщения и «кружочки»
3. Контролировать формат запроса к ИИ

### Решение: Прямой HTTP Request к OpenRouter API

**Архитектура нового воркфлоу:**

```
Webhook-homework → Edit Fields → If (есть файл?)
                                     ├─ Нет → OpenRouter AI (только текст)
                                     └─ Да → Get a file → Switch (по mimeType)
                                                         ├─ image/* → Process Photo (Base64) →┐
                                                         ├─ audio/* → Process Audio (проверка) →├→ OpenRouter AI
                                                         └─ video/* → Process Video (кружочки) →┘
                                                                    ↓
                                                        Parse JSON Response → HTTP Request (бот)
```

### Ключевые изменения

#### 1. Удалены ноды LangChain
- ❌ **Agent** — абстрактный слой, не поддерживает мультимодальность
- ❌ **OpenRouter Chat Model** — используется только через HTTP Request
- ❌ **Merge** — заменен на прямой поток данных
- ❌ **Code** (парсинг) — перемещен после HTTP Request

#### 2. Добавлена маршрутизация (Switch)
**Нода:** `Switch` (после `Get a file`)

**Правила:**
| Выход | Условие | Тип файла | Обработка |
|-------|---------|-----------|-----------|
| 0 | `mimeType.startsWith('image')` | Фото | Process Photo → Base64 |
| 1 | `mimeType.startsWith('audio')` | Аудио | Process Audio → проверка размера |
| 2 | `mimeType.startsWith('video')` | Видео/кружочки | Process Video → проверка |
| 3 | Fallback | Другое | Прямой текст |

#### 3. Обработка файлов (Code ноды)

**Process Photo:**
```javascript
// Конвертация в Base64 для OpenRouter
const base64Image = binaryData.data;
items[0].json.base64_image = base64Image;
items[0].json.mime_type = mimeType;
```

**Process Audio:**
```javascript
// Проверка размера >10 МБ
const MAX_AUDIO_SIZE = 10 * 1024 * 1024;
if (fileSize > MAX_AUDIO_SIZE) {
  items[0].json.audio_warning = "Аудио слишком большое...";
}
```

**Process Video:**
```javascript
// Обработка кружочков (video/ogg)
// Gemini поддерживает аудио напрямую
```

#### 4. Прямой запрос к OpenRouter API

**Нода:** `OpenRouter AI` (HTTP Request)

**Метод:** POST  
**URL:** `https://openrouter.ai/api/v1/chat/completions`

**Заголовки:**
```
Authorization: Bearer <OPENROUTER_KEY>
Content-Type: application/json
HTTP-Referer: https://n8n.indikov.ru
X-Title: AntBot Homework Checker
```

**Тело запроса (JSON):**
```json
{
  "model": "google/gemini-2.5-flash",
  "response_format": { "type": "json_object" },
  "messages": [
    {
      "role": "system",
      "content": "Ты мудрый наставник..."
    },
    {
      "role": "user",
      "content": [
        { "type": "text", "text": "РАБОТА СТУДЕНТА: ..." },
        { "type": "image_url", "image_url": { "url": "data:image/jpeg;base64,..." }},
        { "type": "input_audio", "input_audio": { "data": "...", "format": "ogg" }}
      ]
    }
  ]
}
```

**Важно:** Динамическая вставка изображений/аудио через формулы n8n:
```javascript
{{ $json.base64_image ? ', {"type": "image_url", ...}' : '' }}
{{ $json.file_type === 'audio' ? ', {"type": "input_audio", ...}' : '' }}
```

#### 5. Модель: Google Gemini 2.5 Flash

**Почему не Qwen:**
- Qwen на OpenRouter часто не поддерживает Vision API
- Gemini 2.5 Flash поддерживает: текст + фото + аудио + видео
- Контекст: 1M токенов
- Цена: $0.30/M input, $2.50/M output, $1/M audio

**Альтернативы:**
- `google/gemini-2.5-flash-lite` — дешевле, но слабее
- `openai/gpt-4o-mini` — хорошее зрение, но нет аудио
- `openrouter/auto` — бесплатно, но случайная модель

#### 6. Обработка ошибок

**Уровни защиты:**

1. **Retry на HTTP Request:**
   - 3 попытки
   - Задержка: 2с, 4с, 8с (exponential backoff)
   - Таймаут: 60 секунд

2. **Parse JSON Response (Code):**
   ```javascript
   try {
     const parsedData = JSON.parse(jsonMatch[1]);
     // Валидация полей
   } catch (error) {
     // Fallback: авто-одобрение с сообщением
     return {
       is_approved: true,
       feedback_text: "⚠️ Возникла техническая проблема..."
     };
   }
   ```

3. **HTTP Request (Error Fallback):**
   - Срабатывает если OpenRouter недоступен
   - Отправляет авто-одобрение с уведомлением

4. **Sticky Notes:**
   - Визуальные подсказки в интерфейсе n8n
   - Логи и инструкции по отладке

#### 7. Логирование

**Формат логов:**
```
📸 Фото: 245678 байт, MIME: image/jpeg
🎧 Аудио: 1234567 байт (1.18 МБ), MIME: audio/ogg
🎥 Видео/кружочек: 987654 байт (0.94 МБ), MIME: video/ogg
🤔 Сырой ответ ИИ: {"is_approved":true,"feedback_text":"Отлично..."}
❌ Ошибка парсинга: Unexpected token...
```

**Просмотр:**
```bash
docker compose logs n8n | grep -E "📸|🎧|🎥|🤔|❌"
```

### Ограничения и компромиссы

#### 1. Сжатие фото (1080p)
**Проблема:** n8n не имеет встроенных средств для ресайза изображений.

**Решение:** Telegram автоматически сжимает фото при загрузке. Дополнительно не требуется.

**Если нужно:** Использовать внешнюю функцию (Python/ffmpeg) через Execute Command:
```bash
ffmpeg -i input.jpg -vf scale=1080:-1 output.jpg
```

#### 2. Обрезка аудио >10 МБ
**Проблема:** Gemini имеет лимит на размер аудио.

**Решение:** Предупреждение студента, что ИИ обработает только часть.

**Если нужно:** ffmpeg в ноде Execute Command:
```bash
ffmpeg -i input.ogg -t 60 output.ogg
```

#### 3. Аудио напрямую vs транскрибация
**Почему не Whisper:**
- Gemini 2.5 Flash поддерживает аудио напрямую
- Не нужен отдельный API ключ OpenAI
- Дешевле ($1/M токенов аудио)
- Проще архитектура (одна нода вместо двух)

### Тестирование

**Сценарии:**

1. **Только текст:**
   ```
   Студент: "Выполнил задание"
   → If (нет файла) → OpenRouter AI → Парсинг → Бот
   ```

2. **Фото + текст:**
   ```
   Студент: "Заклеил диод" + [фото]
   → Get a file → Switch (image) → Process Photo → OpenRouter AI
   → ИИ видит фото → "Вижу заклеенный синий светодиод!"
   ```

3. **Голосовое + текст:**
   ```
   Студент: "Рассказываю как сделал" + [audio.ogg]
   → Get a file → Switch (audio) → Process Audio → OpenRouter AI
   → Gemini "слушает" аудио → анализ
   ```

4. **Кружочек:**
   ```
   Студент: [video.ogg] (кружочек)
   → Get a file → Switch (video) → Process Video → OpenRouter AI
   → Gemini обрабатывает как аудио
   ```

5. **Файл >10 МБ:**
   ```
   Студент: [audio 15 МБ]
   → Process Audio → audio_warning = "Аудио слишком большое"
   → OpenRouter AI → предупреждение в ответе
   ```

### Настройка в n8n

**Шаги импорта:**

1. Открыть n8n → Settings → Import
2. Выбрать `n8n-flow.json`
3. Проверить credentials:
   - **OpenRouter account** — API ключ от OpenRouter
   - **N8N_WEBHOOK_SECRE** — секрет для callback (500)
   - **Antbot_api** — токен Telegram бота
4. Активировать воркфлоу

**Проверка:**

```bash
# 1. Логи при отправке ДЗ
docker compose logs -f bot | grep "callback"

# 2. Логи n8n
docker compose logs -f n8n | grep -E "📸|🎧|🎥"

# 3. Тест webhook
curl -X POST https://bot.indikov.ru/webhook/aa46a723-619e-42e9-8e51-49ba51813718 \
  -H "Content-Type: application/json" \
  -H "X-CALLBACK-SIGNATURE: 500" \
  -d '{"student_user_id":123,"homework_text":"Тест","callback_webhook_url_result":"https://bot.indikov.ru/hwX9kLmPqR7tUvW2yZ5aBcDeFgHiJkL/n8n_hw_result"}'
```

### Сравнение: До и После

| Характеристика | Старый (Agent) | Новый (HTTP Request) |
|---------------|----------------|---------------------|
| **Фото** | ❌ Только маркер | ✅ Реальный анализ |
| **Аудио** | ❌ Не поддерживалось | ✅ Прямая обработка |
| **Кружочки** | ❌ Не поддерживалось | ✅ Как видео/аудио |
| **Контроль запроса** | ❌ LangChain | ✅ Полный JSON |
| **Обработка ошибок** | ⚠️ Частичная | ✅ 3 уровня + fallback |
| **Логирование** | ❌ Минимальное | ✅ Детальное (эмодзи) |
| **Модель** | Qwen (без vision) | Gemini 2.5 Flash |
| **Стоимость** | $0.30/M | $0.30/M + $1/M audio |

### Файлы

- `n8n-flow.json` — новый воркфлоу
- `GOALS2.md` — документация (этот раздел)

---


***




## 🛠 Инструкция по настройке сервера (DevOps & Server Setup)

В этом разделе задокументированы все настройки сервера (Debian 12), Docker и Cloudflare, необходимые для стабильной работы проекта.

### 1. Настройка Cloudflare (Маршрутизация и SSL)

Поскольку на одном IP-адресе работают два веб-сервиса (Бот и n8n), и мы не используем Nginx/Traefik в качестве реверс-прокси, маршрутизация настроена **на уровне Cloudflare**.

**A. DNS Records:**
- `bot.indikov.ru` -> A-запись на IP сервера (Proxy status: 🟠 Proxied)
- `n8n.indikov.ru` -> A-запись на IP сервера (Proxy status: 🟠 Proxied)

**B. SSL/TLS:**
- **Режим:** `Flexible` (ОБЯЗАТЕЛЬНО!). 
- *Причина:* Cloudflare общается с клиентами по зашифрованному HTTPS (порт 443), но на сам сервер передает незашифрованный HTTP трафик. Внутри Docker-контейнеров нет SSL-сертификатов. Если поставить "Full", сервер будет отдавать ошибку `525 SSL Handshake Failed`.

**C. Origin Rules (Правила происхождения):**
*(Находится в меню: Rules -> Origin Rules)*
Cloudflare перенаправляет трафик со стандартного порта 443 на внутренние порты Docker.
1. **Rule "Bot Redirect":** If `Hostname` equals `bot.indikov.ru` -> Destination Port: `Rewrite to 8080`.
2. **Rule "N8N Redirect":** If `Hostname` equals `n8n.indikov.ru` -> Destination Port: `Rewrite to 5678`.

---

### 2. Настройка Сервера (Debian)

**A. Настройка Firewall (UFW):**
По умолчанию нестандартные порты могут быть закрыты. Включен UFW:
```bash
sudo apt install ufw -y
sudo ufw allow 22/tcp    # SSH (Обязательно, чтобы не потерять доступ)
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw allow 8080/tcp  # Бот (внешний)
sudo ufw allow 5678/tcp  # n8n (внешний)
sudo ufw enable
```

**B. Оптимизация памяти (Swap-файл):**
*Проблема:* На сервере с 1 ГБ ОЗУ при старте/обновлении n8n возникала пиковая нагрузка (CPU 100%), сервер зависал и падал с OOM (Out Of Memory).
*Решение:* Создан файл подкачки на 2 ГБ.
```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# Использовать swap только при крайней необходимости:
sudo sysctl vm.swappiness=10
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
```

---

### 3. Специфика `.env` переменных

**A. Настройки хоста:**
- `WEBAPP_HOST=0.0.0.0` — Критически важно для Docker. Если не указать, aiohttp будет слушать `::1` (IPv6 localhost), и проброс портов из Docker-compose работать не будет (`curl: (52) Empty reply from server`).
- `WEBHOOK_HOST=https://bot.indikov.ru` — Без указания портов! Телеграм стучится в Cloudflare по стандартному 443 порту.

**B. Интеграция с n8n:**
- `N8N_CALLBACK_SECRET` — пароль, который n8n передает боту в заголовке `X-CALLBACK-SIGNATURE` для авторизации результатов проверки ДЗ.

---

### 4. Устранение частых неполадок (Troubleshooting)

**A. Ошибка `OSError:[Errno 28] No space left on device`**
Сервер забился логами и старыми образами Docker.
*Решение:* 
```bash
docker system prune -a -f    # Удаляет старые образы и кэш
sudo apt-get clean           # Очистка кэша пакетов
sudo journalctl --vacuum-time=1d # Очистка системных логов
```

**B. Упала база данных n8n (`SQLITE_ERROR: no such column...`)**
Происходит, если серверу не хватило места/памяти во время миграции БД n8n.
*Решение (сброс БД, воркфлоу удалятся, если не забэкаплены):*
```bash
docker-compose down
sudo rm -rf n8n_data/*
docker-compose up -d
```

**C. Ошибка прав доступа к папке n8n (`Permission denied`)**
Папка `n8n_data` должна принадлежать пользователю внутри контейнера (ID 1000).
*Решение:*
```bash
sudo chown -R 1000:1000 n8n_data
sudo chmod -R 777 n8n_data
```

**D. Бот отвечает "Урок №X недоступен", хотя он есть в базе**
Обычно связано с тем, что админ загрузил урок, находясь в состоянии тестирования (как студент). 
*Решение:* Выполнить `/admin_reset` в личке бота, чтобы удалить себя из таблицы `user_courses` и сбросить таймеры.







