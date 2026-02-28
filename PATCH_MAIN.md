# Исправления для main.py (строка ~8672)

## ЗАМЕНИТЬ:

```python
if message_id_to_process:
    try:
        await bot.edit_message_reply_markup(chat_id=ADMIN_GROUP_ID, message_id=message_id_to_process)
        await bot.send_message(ADMIN_GROUP_ID, final_admin_notification,
                               reply_to_message_id=message_id_to_process, parse_mode=None)
        logger.info(f"{log_prefix} Единое уведомление в админ-чат отправлено.")
    except Exception as e_admin_notify:
        logger.error(f"{log_prefix} Не удалось отправить уведомление в админ-чат: {e_admin_notify}")
```

## НА:

```python
if message_id_to_process:
    # Сначала пробуем убрать кнопки (если таймер ещё не убрал)
    try:
        await bot.edit_message_reply_markup(
            chat_id=ADMIN_GROUP_ID,
            message_id=message_id_to_process,
            reply_markup=None
        )
        logger.info(f"{log_prefix} Кнопки удалены")
    except Exception as e_rm:
        logger.debug(f"{log_prefix} Не удалось удалить кнопки: {e_rm}")

    # Отправляем ОТДЕЛЬНОЕ сообщение с результатом (reply на карточку ДЗ)
    try:
        await bot.send_message(
            chat_id=ADMIN_GROUP_ID,
            text=final_admin_notification,
            reply_to_message_id=message_id_to_process,
            parse_mode=None
        )
        logger.info(f"{log_prefix} Результат отправлен в админ-чат")
    except Exception as e_admin_notify:
        logger.error(f"{log_prefix} Ошибка отправки: {e_admin_notify}")
```

## ТАКЖЕ ЗАМЕНИТЬ (строка ~8669):

```python
if feedback_text:
    final_admin_notification += f"\n\n*Комментарий:*\n_{escape_md(feedback_text)}_"
```

## НА:

```python
if feedback_text:
    # Урезаем до 240 символов для админа
    feedback_short = feedback_text[:240] + "..." if len(feedback_text) > 240 else feedback_text
    final_admin_notification += f"\n\n*Комментарий ИИ:*\n_{escape_md(feedback_short)}_"
```
