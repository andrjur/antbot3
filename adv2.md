


Отличная задача! Мы сделаем так, чтобы бот работал как часы, не ломал карточки ДЗ, красиво выводил время в админке и **сам страховал n8n**, одобряя домашку, если ИИ "задумался" слишком надолго (3х таймаута).

### В чем была проблема с "коротким" сообщением?
В функции `check_pending_homework_timeout` был кусок кода, который при отправке в n8n **жестко затирал весь текст сообщения** и ставил вместо него короткую фразу: `?? ИИ-ассистент проверяет ДЗ... {student_name} Ожидайте...`. 
Мы это удалим. Функция `run_hw_countdown` уже отлично умеет менять **только одну строчку** с таймером и убирать кнопки.

Ниже пошаговые изменения, которые нужно внести в `main.py`.

---

### Шаг 1. Функция форматирования времени
Добавьте эту маленькую функцию где-нибудь вверху файла `main.py` (например, после импортов или рядом с `get_lesson_plural`):

```python
def format_time_duration(seconds: int) -> str:
    """Форматирует секунды в читаемый вид (секунды или минуты и секунды)"""
    if seconds < 60:
        return f"{seconds} сек"
    m = seconds // 60
    s = seconds % 60
    if s == 0:
        return f"{m} мин"
    return f"{m} мин и {s} сек"
```

---

### Шаг 2. Обновляем текст в Админ-меню
Найдите функцию `cmd_start` (в блоке, где формируется сообщение `?? АДМИН-МЕНЮ`, это примерно 5100-5200 строки) и функцию `callback_admin_menu`.
Замените строчку про таймаут в **обеих** функциях на эту:

**Было:**
`f"• /set_hw_timeout <сек> — таймаут AI-проверки\n"`
**Стало:**
```python
f"• /set_hw_timeout <сек> — таймаут AI-проверки (сейчас {format_time_duration(HW_TIMEOUT_SECONDS)})\n"
```

---

### Шаг 3. Полностью обновляем логику фоновой проверки (Самое важное!)
Найдите вашу функцию `check_pending_homework_timeout()` (строка ~1210). Мы её перепишем: 
1. Уменьшим шаг проверки с 60 до **10 секунд** (иначе при таймауте 34 сек цикл в 60 сек всё испортит).
2. Добавим логику Авто-Одобрения (3 * HW_TIMEOUT).
3. **Уберем** кусок, который ломал текст карточки.

Замените всю функцию на этот код:

```python
async def check_pending_homework_timeout():
    """
    Периодически проверяет ДЗ. 
    Если прошло HW_TIMEOUT_SECONDS -> отправляет в n8n.
    Если прошло 3 * HW_TIMEOUT_SECONDS -> авто-аппрув внутри бота.
    """
    global HW_TIMEOUT_SECONDS
    logger.info(f"check_pending_homework_timeout START: HW_TIMEOUT_SECONDS={HW_TIMEOUT_SECONDS}")

    while True:
        try:
            await asyncio.sleep(10) # Проверяем каждые 10 секунд (важно для коротких таймаутов)
            
            async with aiosqlite.connect(DB_FILE) as conn:
                now = datetime.now(pytz.utc)
                
                # Забираем ВСЕ ожидающие ДЗ
                cursor = await conn.execute('''
                    SELECT admin_message_id, admin_chat_id, student_user_id,
                           course_numeric_id, lesson_num, student_message_id, created_at,
                           homework_text
                    FROM pending_admin_homework
                ''')
                pending_rows = await cursor.fetchall()

                for row in pending_rows:
                    admin_msg_id, admin_chat_id, student_user_id, course_numeric_id, lesson_num, student_msg_id, created_at_str, homework_text = row
                    
                    # Считаем возраст ДЗ в секундах
                    created_at = datetime.strptime(created_at_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.utc)
                    age_seconds = (now - created_at).total_seconds()

                    # --- ЛОГИКА 1: АВТО-АППРУВ (если ИИ завис / n8n сломался) ---
                    if age_seconds >= (3 * HW_TIMEOUT_SECONDS):
                        logger.warning(f"? ДЗ #{admin_msg_id} висит уже {age_seconds} сек. Авто-одобрение (n8n не ответил).")
                        course_id_str = await get_course_id_str(course_numeric_id)
                        
                        # Вызываем штатную функцию обработки результата
                        await handle_homework_result(
                            user_id=student_user_id,
                            course_id=course_id_str,
                            course_numeric_id=course_numeric_id,
                            lesson_num=lesson_num,
                            admin_id=0, # 0 = Система/ИИ
                            feedback_text="Принято.", # Коротко для клиента
                            is_approved=True,
                            callback_query=None,
                            original_admin_message_id_to_delete=admin_msg_id
                        )
                        # Добавим примечание админам, что это авто-аппрув по таймауту
                        try:
                            await bot.send_message(
                                chat_id=ADMIN_GROUP_ID,
                                text=f"?? ДЗ выше одобрено АВТОМАТИЧЕСКИ (ИИ не ответил за {format_time_duration(3 * HW_TIMEOUT_SECONDS)}).",
                                reply_to_message_id=admin_msg_id,
                                parse_mode=None
                            )
                        except Exception:
                            pass
                        
                        # Удаляем из списка отправленных в n8n
                        homework_sent_to_n8n.discard(admin_msg_id)
                        continue # Переходим к следующему ДЗ

                    # --- ЛОГИКА 2: ОТПРАВКА В n8n (обычный таймаут ручной проверки) ---
                    if age_seconds >= HW_TIMEOUT_SECONDS and admin_msg_id not in homework_sent_to_n8n:
                        if not N8N_HOMEWORK_CHECK_WEBHOOK_URL:
                            continue
                            
                        # Получаем данные студента и курса для отправки
                        cursor_student = await conn.execute("SELECT username, first_name FROM users WHERE user_id = ?", (student_user_id,))
                        student_info = await cursor_student.fetchone()
                        student_name = f"{student_info[1]} (@{student_info[0]})" if student_info else f"User {student_user_id}"
                        
                        course_id_str = await get_course_id_str(course_numeric_id)
                        course_title = await get_course_title(course_id_str)
                        
                        # Получаем текст задания
                        cursor_lesson = await conn.execute(
                            """SELECT text FROM group_messages WHERE course_id = ? AND lesson_num = ? AND is_homework = 0 AND content_type = 'text' ORDER BY id ASC""",
                            (course_id_str, lesson_num)
                        )
                        lesson_description = "\n".join([r[0] for r in await cursor_lesson.fetchall() if r[0]])
                        
                        # Строим callback URL
                        host = WEBHOOK_HOST_CONF.rstrip("/")
                        secret_path = (WEBHOOK_SECRET_PATH_CONF or "").strip("/")
                        callback_base = f"{host}/{secret_path}" if secret_path else f"{host}/{WEBHOOK_PATH_CONF.strip('/')}"

                        payload = {
                            "action": "check_homework",
                            "student_user_id": student_user_id,
                            "user_fullname": student_name,
                            "course_numeric_id": course_numeric_id,
                            "course_id": course_id_str,
                            "course_title": course_title,
                            "lesson_num": lesson_num,
                            "lesson_assignment_description": lesson_description,
                            "homework_text": homework_text or "",
                            "homework_content_type": "text",
                            "expected_homework_type": "any",
                            "original_admin_message_id": admin_msg_id,
                            "admin_group_id": ADMIN_GROUP_ID,
                            "student_message_id": student_msg_id,
                            "callback_webhook_url_result": f"{callback_base}/n8n_hw_result",
                            "callback_webhook_url_error": f"{callback_base}/n8n_hw_processing_error",
                            "telegram_bot_token": BOT_TOKEN,
                            "timeout_seconds": HW_TIMEOUT_SECONDS
                        }
                        
                        # Отправляем в n8n (не блокируя цикл)
                        asyncio.create_task(send_data_to_n8n(N8N_HOMEWORK_CHECK_WEBHOOK_URL, payload))
                        homework_sent_to_n8n.add(admin_msg_id)
                        logger.info(f"ДЗ #{admin_msg_id} отправлено на n8n.")
                        
                        # ЗАМЕТЬТЕ: Мы БОЛЬШЕ НЕ ДЕЛАЕМ bot.edit_message_caption здесь!
                        # Карточку будет аккуратно менять run_hw_countdown, оставляя текст ДЗ на месте.

        except asyncio.CancelledError:
            logger.info("check_pending_homework_timeout: задача отменена (shutdown)")
            return
        except Exception as e:
            logger.error(f"check_pending_homework_timeout: необработанная ошибка: {e}", exc_info=True)
            await asyncio.sleep(10)
```

---

### Что изменится после этих правок?
1. **Внешний вид ДЗ:** Когда таймер дойдет до нуля, текст в группе изменится так, как вы хотели (исчезнут кнопки, строка таймера превратится в `? ИИ-ассистент начал проверку ДЗ, подождите...`), но **все данные студента, курс и текст домашки останутся на экране!**
2. **Страховка:** Если n8n зависнет, выдаст ошибку 521, или OpenRouter ляжет, ровно через `3 * HW_TIMEOUT_SECONDS` бот скажет: *"Ладно, я ждал слишком долго"* и **автоматически примет** эту домашку. Студенту уйдет "Принято", а админам придет уведомление.
3. **Меню:** Вы и админы будете четко видеть текущий таймаут в часах/минутах/секундах по команде `/start` или `/admin_menu`.





ноду Telegram в n8n (Edit a text message) **нужно вообще удалить**. 

Давайте разберем почему, и почему Python-бот справится с этим надежнее, чем n8n.



### Идеальное разделение обязанностей:
1. **Бот (Python)** отвечает за **интерфейс** (кнопки, таймеры, "Ожидайте", фото/не фото). Он отправил сообщение, он же его и редактирует, когда таймер выходит.
2. **n8n** отвечает за **мозги (ИИ)**. Он принимает текст, думает через OpenRouter и возвращает результат обратно в бота.



Всё! В n8n останется только логика "Принять вебхук -> Скормить ИИ -> Отправить HTTP Request обратно в бота". 

А то, как карточка визуально меняется с "Осталось 10 сек" на "⏳ ИИ-ассистент начал проверку", возьмет на себя функция `run_hw_countdown` в самом Python-коде бота. Это максимально надежно, ничего не залагает и не перепутается!