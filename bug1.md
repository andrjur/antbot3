ПРОСТО ОПИШИ ЧТО ПРОИСХОДИТ МАКСИМАЛЬНО ПОДРОБНО. С УПОМИНАНИЕМ ВСЕХ ФУНКЦИЙ КОТОРЫЕ СРАБАТЫВАЮТ И ЭФФЕКТОВ КОТОРЫЕ ОНИ ДАЮТ. уЧИТЫВАЯ И АССИНХРОННЫЕ - ТАЙМЕРЫ И Т.П.

ПРЕДЛОЖИ ЧТО ТЫ ПЛАНИРУЕШЬ ДЕЛАТЬ НО ПОКА НЕ ДЕЛАЙ. ПРОСТО ОБСУДИМ

случился глюк. ОПЯТЬ

10:55:23 341 [_polling] - Run polling for bot @antbot_hook_bot id=7473862113 - ‘antbot_hook_bot’ INFO
antbot | 10:55:34 6026 [cmd_start] - !!! CMD_START ВЫЗВАН для пользователя 954230772 !!! INFO
antbot | 10:55:34 6030 [cmd_start] - cmd_start user_id=954230772, ADMIN_IDS_CONF=[182643037, 954230772], is_admin=True INFO
antbot | 10:55:34 6037 [cmd_start] - cmd_start: user_exists = (954230772,) INFO
antbot | 10:55:34 6050 [cmd_start] - cmd_start: User 954230772 is admin, checking for active course… INFO
antbot | 10:55:34 6068 [cmd_start] - cmd_start: admin current_course query result = (‘sprint2’, 1, ‘v2’, ‘sprint2 basic’, ‘coach’, ‘active’, ‘pending’) INFO
antbot | 10:55:34 6078 [cmd_start] - cmd_start: Admin 954230772 has active course sprint2, showing admin test mode INFO
antbot | 10:55:34 172 [feed_update] - Update id=841441387 is handled. Duration 203 ms by bot id=7473862113 INFO
antbot | 10:55:39 5783 [cb_stop_current_course] - cb_stop_current_course START: user_id=954230772, stop_course_id=sprint2 (numeric: 7) INFO
antbot | 10:55:39 1116 [stop_lesson_schedule_task] - Остановлена задача проверки расписания уроков для пользователя 954230772. INFO
antbot | 10:55:39 5793 [cb_stop_current_course] - cb_stop_current_course SUCCESS: курс sprint2 деактивирован для user 954230772 INFO
antbot | 10:55:39 6751 [cb_select_other_course] - Пользователь 954230772 нажал ‘Выбрать другой курс’ / ‘Все курсы’ INFO
antbot | 10:55:39 172 [feed_update] - Update id=841441388 is handled. Duration 322 ms by bot id=7473862113 INFO
antbot | 10:55:46 8393 [handle_text] - handle_text: text=‘sprint2pro’ user_id=954230772 INFO
antbot | 10:55:46 8400 [handle_text] - handle_text: active_course=None INFO
antbot | 10:55:46 8466 [handle_homework] - новый обработчик и текстовой домашки и фото 17-04 user_id=954230772 INFO
antbot | 10:55:46 7380 [get_user_course_data] - 776 user_course_data=None INFO
antbot | 10:55:46 7382 [get_user_course_data] - Нет активного курса для пользователя 954230772 WARNING
antbot | 10:55:46 8470 [handle_homework] - строка 4162 user_course_data=None INFO
antbot | 10:55:46 738 [activate_course] - Попытка активации: user_id=954230772, code=sprint2pro -> course_id=‘sprint2’, version_id=‘v2’ (с проверкой) INFO
antbot | 10:55:46 802 [activate_course] - Повторная активация курса ‘sprint2’ с тарифом ‘v2’ для user_id=954230772. Предыдущие статусы были неактивны. INFO
antbot | 10:55:46 834 [activate_course] - activation_log_details=“Активирован/возобновлен курс ‘sprint2’ с тарифом ‘v2’. Прогресс сброшен.” user_message=‘? Курс «sprint2 basic» с тарифом «с проверкой» успешно активирован (или возобновлен)! Прогресс начнется заново.’ INFO
antbot | 10:55:46 1868 [log_action] - Лог действия: user_id=954230772, action=COURSE_ACTIVATION, course=sprint2, lesson=None, old=None, new=v2, details=Активирован/возобновлен курс ‘sprint2’ с тарифом ‘v2’. Прогресс сброшен. INFO
antbot | 10:55:46 1108 [start_lesson_schedule_task] - 500 start_lesson_schedule_task Запущена задача проверки расписания уроков для пользователя 954230772. INFO
antbot | 10:55:46 1002 [check_lesson_schedule] - Отправка урока 1 для user_id=954230772 INFO
antbot | 10:55:46 1904 [send_lesson_to_user] - ?? send_lesson_to_user: user_id=954230772, course_id=sprint2, lesson_num=1, repeat=False, user_course_level=1 INFO
antbot | 10:55:46 1935 [send_lesson_to_user] - Для курса ‘sprint2’ найдено 27 уроков. Запрошен урок 1. INFO
antbot | 10:55:46 2022 [_send_lesson_parts] - Отправка частей урока 1 (3 шт.) для sprint2, user_level=1 INFO
antbot | 10:55:46 8481 [handle_homework] - 444 is_activated now INFO
antbot | 10:55:46 8507 [handle_homework] - message_interval = 12 INFO
antbot | 10:55:46 6384 [send_course_description] - send_course_description START: user_id=954230772, course_id_str=‘base’ INFO
antbot | 10:55:46 6407 [send_course_description] - Найдено описание для курса ‘base’ как урок 0 в ‘group_messages’. INFO
antbot | 10:55:46 6455 [send_course_description] - Описание для ‘base’ (длина 1323) успешно отправлено пользователю 954230772. INFO
antbot | 10:55:46 8516 [handle_homework] - 3 перед созданием клавиатурыcourse_numeric_id=3 INFO
antbot | 10:55:46 8532 [handle_homework] - 3332 welcome_message=‘Добро пожаловать, Andrew\n\nВы успешно активировали base\nВаш тариф: Группа\nИнтервал между уроками: 12 ч\n\nЖелаем удачи в прохождении курса’ INFO
antbot | 10:55:46 2029 [_send_lesson_parts] - Пропуск части 2 урока 1 (уровень сообщения 2 > уровня пользователя 1) INFO
antbot | 10:55:46 172 [feed_update] - Update id=841441389 is handled. Duration 520 ms by bot id=7473862113 INFO
antbot | 10:55:46 2068 [_send_lesson_parts] - Часть 3 урока 1 является ДЗ типа: None INFO
antbot | 10:55:46 2070 [_send_lesson_parts] - Обработано/отправлено 2 из 3 частей урока 1. INFO
antbot | 10:55:46 2097 [_update_user_course_after_lesson] - Новый урок 1 отправлен для 954230772. Время: 2026-02-25 07:55:46. Это ДЗ: True INFO
antbot | 10:55:51 1870 [log_action] - Ошибка логирования действия LESSON_SENT для user_id=954230772: database is locked ERROR
antbot | 10:55:51 8919 [send_main_menu] - send_main_menu START: user_id=954230772, course_id=‘sprint2’, lesson_num=1, version_id=‘v2’, homework_pending=True, hw_type=‘None’, level=1 INFO
antbot | 10:55:51 9081 [send_main_menu] - send_main_menu END: Успешно отправлено меню для user_id=954230772, course_id=‘sprint2’, menu_message_id=6143 INFO
antbot | 10:55:51 1986 [send_lesson_to_user] - ? Обработка для урока 1 курса ‘sprint2’ (user 954230772) завершена. INFO
antbot | 10:55:55 8393 [handle_text] - handle_text: text=‘тарелка’ user_id=954230772 INFO
antbot | 10:55:55 8400 [handle_text] - handle_text: active_course=(‘sprint2’,) INFO
antbot | 10:55:55 8403 [handle_text] - handle_text: отправляем в handle_homework INFO
antbot | 10:55:55 8466 [handle_homework] - новый обработчик и текстовой домашки и фото 17-04 user_id=954230772 INFO
antbot | 10:55:55 7380 [get_user_course_data] - 776 user_course_data=(7, 1, ‘v2’) INFO
antbot | 10:55:55 8470 [handle_homework] - строка 4162 user_course_data=(7, 1, ‘v2’) INFO
antbot | 10:55:55 8588 [handle_homework] - ДЗ для урока 1 уже одобрено — игнорируем повторную отправку INFO
antbot | 10:55:55 172 [feed_update] - Update id=841441390 is handled. Duration 102 ms by bot id=7473862113 INFO
andrjur@etppjmdtxr:~/antbot4$

antbot_hook_bot, [25.02.2026 10:55]
текст напишите своё намерение на курс. Запрос.

antbot_hook_bot, [25.02.2026 10:55]
?? Курс: sprint2 basic
?? Тариф: с проверкой
?? Урок (отправлен): 1 из 27
?? Уровень: 1
? Интервал: 12 ч
?? Домашка к уроку 1: ожидается (любое)
?? Следующий урок: 22:55 (Ср, 25 февраля 2026)

Andrew Indikov, [25.02.2026 10:55]
тарелка

antbot_hook_bot, [25.02.2026 10:55]
? Домашка уже засчитана!

?? Следующий урок: 22:55 (Ср, 25 февраля 2026)

?? ПОДРОБНОЕ ОПИСАНИЕ ПРОБЛЕМЫ
?? Хронология событий (по логам):
Время	Событие	Функция	Параметры	Эффект
10:55:34	/start	cmd_start()	user_id=954230772	Показано меню админа
10:55:34	Клик “Остановить курс”	cb_stop_current_course()	course_id=sprint2	Курс деактивирован, шедулер остановлен
10:55:46	Ввод sprint2pro	handle_text() > handle_homework()	text=‘sprint2pro’	Запуск активации курса
10:55:46	Проверка курса	get_user_course_data()	user_id=954230772	Возврат None (нет активного)
10:55:46	Активация	activate_course()	code=‘sprint2pro’, v2	hw_status=‘none’ (сброс)
10:55:46	Запуск шедулера	start_lesson_schedule_task()	user_id=954230772	Создан asyncio.Task
10:55:46	Отправка урока	check_lesson_schedule() > send_lesson_to_user()	lesson_num=1	Урок отправлен, hw_status=‘pending’
10:55:46	ГОНКА!	send_course_description()	course_id_str=‘base’ ?	Отправлено описание base
10:55:46	Формирование меню	handle_homework()	course_numeric_id=3 ?	welcome_message=‘base’
10:55:51	Сохранение меню	send_main_menu()	hw_status=‘pending’ ?	hw_status=‘pending’ в БД
10:55:55	Ввод тарелка	handle_text() > handle_homework()	text=‘тарелка’	Отправка ДЗ
10:55:55	Проверка hw_status	handle_homework()	hw_status=‘approved’ ?	“ДЗ уже засчитана!”
?? ДВЕ ПРОБЛЕМЫ ОДНОВРЕМЕННО:
Проблема 1: Гонка данных (race condition)
10:55:46 activate_course() > course_id='sprint2' ?
10:55:46 send_course_description() > course_id_str='base' ?
ГДЕ: send_course_description() вызывается из handle_homework() (строка ~8598), но получает ‘base’ вместо ‘sprint2’.

ПОЧЕМУ: Переменная course_id перетирается где-то между activate_course() и send_course_description().

Проблема 2: hw_status=‘approved’ через 5 секунд
10:55:51 send_main_menu() > hw_status='pending' ?
10:55:55 handle_homework() > hw_status='approved' ?
ГДЕ: За 4 секунды hw_status изменился с ‘pending’ на ‘approved’.

ВОЗМОЖНЫЕ ПРИЧИНЫ:

check_pending_homework_timeout() — фоновая задача, работает каждые 60 сек

Проверяет pending_admin_homework WHERE created_at < cutoff_time
Отправляет на n8n если прошло HW_TIMEOUT_SECONDS (120 сек)
НО: n8n может отвечать сразу и одобрять!
handle_n8n_hw_approval() — webhook от n8n

Получает is_approved=true от ИИ
Вызывает handle_homework_result() > update_homework_status('approved')
Тариф v1 (Соло) — авто-одобрение

Но у нас v2 (с проверкой)!
?? АСИНХРОННЫЕ ПРОЦЕССЫ:
Процесс	Когда запущен	Что делает	Когда срабатывает
check_lesson_schedule()	Каждую минуту	Проверяет время урока	При старте бота + каждую минуту
check_pending_homework_timeout()	Каждую минуту	Проверяет таймаут ДЗ	Через 60 сек после старта + каждую минуту
handle_n8n_hw_approval()	Webhook	Получает ответ от ИИ	Когда n8n отвечает (может быть сразу)
?? МОЙ ПЛАН ДЕЙСТВИЙ (ОБСУДИМ):
Шаг 1: Найти где course_id=‘base’
Что сделаю:

# Добавить логирование ПЕРЕД каждым вызовом send_course_description:
logger.info(f"CALLER: {inspect.stack()[1].function}")
logger.info(f"course_id={course_id}, type={type(course_id)}")
Где: В начале send_course_description()

Шаг 2: Найти где hw_status=‘approved’
Что сделаю:

# Добавить логирование в update_homework_status():
logger.info(f"UPDATE hw_status: {status}, caller={inspect.stack()[1].function}")
logger.info(f"STACK: {inspect.format_stack()}")
Где: В функции update_homework_status() (строка ~7792)

Шаг 3: Проверить n8n webhook
Что сделаю:

# В handle_n8n_hw_approval():
logger.info(f"n8n callback: is_approved={is_approved}, data={data}")
logger.info(f"CALL STACK: {inspect.format_stack()}")
Где: В начале handle_n8n_hw_approval() (строка ~1708)

Шаг 4: Временно отключить n8n
Что сделаю:

# Закомментировать отправку на n8n в check_pending_homework_timeout():
# success, response = await send_data_to_n8n(...)
logger.info(f"?? n8n отключен для отладки")
Цель: Исключить n8n из уравнения

Шаг 5: Добавить блокировку
Что сделаю:

# В handle_homework():
if user_id in HOMEWORK_BEING_PROCESSED:
    logger.info(f"ДЗ уже обрабатывается для user_id={user_id}")
    return
HOMEWORK_BEING_PROCESSED.add(user_id)
# ... обработка ...
HOMEWORK_BEING_PROCESSED.discard(user_id)
Цель: Предотвратить гонку данных

