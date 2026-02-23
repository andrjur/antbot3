# n8n Workflows

Эта папка содержит workflow для n8n.

## Импорт workflow

1. Откройте n8n: `https://n8n.indikov.ru`
2. Меню (⋮) → Import from File
3. Выберите файл `.json`
4. Настройте credentials:
   - OpenRouter API (для AI)
   - Telegram Bot API
   - HTTP Header Auth (для callback в бот)

## Workflow

### AI_Agent.json

Проверка домашних заданий через AI (DeepSeek через OpenRouter).

**Webhook:** `/webhook/aa46a723-619e-42e9-8e51-49ba51813718`

**Требуемые credentials:**
- `openRouterApi` — OpenRouter API ключ
- `telegramApi` — Telegram Bot токен
- `httpHeaderAuth` — для аутентификации webhook от бота
