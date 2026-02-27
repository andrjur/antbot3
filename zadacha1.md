Задача: Унификация ключей в JSON-payload при отправке вебхука в n8n.
Суть проблемы:
У нас есть два сценария отправки данных о домашке (ДЗ) в n8n:
Штатная отправка (сразу после того, как студент сдал ДЗ).
Отправка по таймауту (через фоновую задачу check_pending_homework_timeout, когда ДЗ зависло).
Сейчас эти два сценария отправляют словари (payload) с разными названиями ключей.
В штатном режиме ID сообщения в админке уходит под ключом "original_admin_message_id".
А в функции таймаута этот же ID уходит под ключом "admin_message_id".
Из-за этого n8n-воркфлоу ломается, так как ожидает единую структуру данных. Также в таймауте отправляется ключ "student_name", а n8n ждет "user_fullname".
Что нужно сделать:
Найти в main.py функцию фоновой проверки таймаутов (скорее всего она называется check_pending_homework_timeout или подобным образом).
Найти внутри нее формирование словаря payload (или payload_for_n8n_timeout), который уходит через send_data_to_n8n.
Строго переименовать ключи в этом словаре, чтобы они полностью соответствовали штатной отправке.
Как должно стать (пример кода для payload в функции таймаута):
code
Python
# ВАЖНО: Ключи должны называться именно так!

payload_for_n8n_timeout = {
    "action": "check_homework", # или "check_homework_timeout", если n8n это как-то разделяет
    "student_user_id": student_user_id,
    
    # ИСПРАВЛЕНО 1: n8n ожидает user_fullname, а не student_name
    "user_fullname": student_name_from_db, 
    
    "course_numeric_id": course_numeric_id,
    "course_title": course_title,
    "lesson_num": lesson_num,
    
    # ИСПРАВЛЕНО 2: Переименовали admin_message_id в original_admin_message_id
    "original_admin_message_id": admin_message_id, 
    
    "student_message_id": student_message_id,
    
    # ИСПРАВЛЕНО 3: Убедиться, что передается текст домашки, иначе ИИ нечего будет проверять
    "homework_text": text_of_homework_from_db, 
    
    # Опционально: можно передавать пустые поля для консистентности, если n8n их ждет
    "homework_content_type": "text", 
    "expected_homework_type": "any",
    "admin_group_id": ADMIN_GROUP_ID,
    
    "timeout_minutes": 7
}
Ожидаемый результат:
При срабатывании таймаута Python-бот должен отправить в n8n JSON, который по структуре ключей на 100% идентичен тому JSON'у, который отправляется в функции handle_homework при обычной сдаче ДЗ.