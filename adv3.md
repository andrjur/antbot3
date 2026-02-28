



Отлично, вы всё сделали правильно в n8n (формула с динамическим URL прописана идеально)! 

Давайте разберем **ошибку 521** и как сделать так, чтобы карточка ДЗ в админке выглядела красиво, без странных прыжков таймера.

---

### Часть 1. Разбираемся с ошибкой 521 (Web server is down)

**Почему это происходит?**
n8n берет URL `https://bot.indikov.ru/hwX9.../n8n_hw_result` и отправляет запрос через интернет. Запрос бьется в двери Cloudflare (порт 443). Cloudflare стучится к вам на VPS, но ваш бот работает на порту `8080`. Так как у вас **нет** Origin Rule (или Nginx-прокси), перенаправляющего внешний трафик с 443 порта на 8080, Cloudflare упирается в закрытую дверь и выдает 521.

**Как починить (УМНЫЙ СПОСОБ):**
Вам *не нужно* ковырять Cloudflare! Поскольку n8n и бот крутятся у вас на одном сервере, мы заставим их общаться **напрямую по внутренней сети Docker**, минуя интернет и Cloudflare. Это будет работать за миллисекунду и никогда не выдаст 521 ошибку.

**Что нужно сделать:**

1. **В файле `.env`** на сервере убедитесь, что у вас есть эта строка:
   ```env
   BOT_INTERNAL_URL=http://bot:8080
   ```
2. **В файле `main.py`** найдите функцию `check_pending_homework_timeout` (примерно строка 1285).
   Найдите блок, где строится `callback_base`:
   ```python
   # Строим callback URL
   host = WEBHOOK_HOST_CONF.rstrip("/")
   secret_path = (WEBHOOK_SECRET_PATH_CONF or "").strip("/")
   callback_base = f"{host}/{secret_path}" if secret_path else f"{host}/bot/"
   ```
   **Замените этот блок на следующий:**
   ```python
   # Строим callback URL (с приоритетом внутренней Docker-сети)
   internal_host = os.getenv("BOT_INTERNAL_URL")
   secret_path = (WEBHOOK_SECRET_PATH_CONF or "").strip("/")
   
   if internal_host:
       callback_base = f"{internal_host.rstrip('/')}/{secret_path}"
   else:
       host = WEBHOOK_HOST_CONF.rstrip("/")
       callback_base = f"{host}/{secret_path}" if secret_path else f"{host}/bot/"
   ```
   *Теперь бот будет отправлять в n8n ссылку вида `http://bot:8080/hwX9.../n8n...`. n8n стукнется по ней напрямую в соседний контейнер, и всё сработает идеально.*

---

### Часть 2. Красивая карточка ДЗ в админке

Вы скинули пример, где в карточке написано `?? До AI-проверки: 0 сек назад` (хотя таймер должен был стартовать с 34 секунд). 
Это произошло потому, что в функции отправки ДЗ цифра `0` была прописана *жестко текстом*.

**Исправляем:**

1. Найдите функцию `handle_homework` (примерно строка 5040).
   Найдите там блок `admin_message_text = (`:
   ```python
   admin_message_text = (
       f"?? Новое ДЗ {homework_type}\n"
       f"?? Пользователь: {escape_md(user_display_name)} ID: {user_id}\n"
       f"?? Курс: {escape_md(display_course_title)}\n"
       f"? Тариф: {escape_md(version_id)}\n"
       f"?? Урок: {current_lesson}\n"
       f"?? До AI-проверки: 0 сек назад\n"  # <--- ВОТ ТУТ ОШИБКА!
   )
   ```
   **Замените последнюю строчку так:**
   ```python
       f"?? До отправки ИИ: {HW_TIMEOUT_SECONDS} сек\n"
   ```

2. Теперь найдите функцию `run_hw_countdown` (примерно строка 1165). Мы научим её корректно перерисовывать именно эту строчку:
   ```python
   # Обновляем только строку с таймером
   if remaining > 0:
       timer_line = f"?? До отправки ИИ: {remaining} сек"
       current_reply_markup = reply_markup
   else:
       timer_line = "? ДЗ отправлено в ИИ. Ожидаем ответ..."
       current_reply_markup = None
       
   # Заменяем только строку с таймером
   updated_lines =