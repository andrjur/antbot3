
# Admin command to reply to user
@dp.message(Command("adm_message_user"), F.chat.id == ADMIN_GROUP_ID)
async def adm_message_user(message: Message):

@dp.message(Command("homework"))
@db_exception_handler
async def cmd_homework(message: types.Message):

# функция для активации курса
@db_exception_handler
async def activate_course(user_id, course_id, course_type, price_rub):

@dp.message(CommandStart())
@db_exception_handler
async def cmd_start(message: types.Message):

#help
@dp.message(Command("help"))
async def cmd_help(message: Message):

@dp.message(Command("select_course"))
@db_exception_handler
async def select_course(message: Message):

# Создает тикет в службу поддержки # Пересылает сообщение администраторам
@dp.message(Command("support"))
async def cmd_support(message: Message):

# Активация курса по кодовому слову. Записывает пользователя на курс
@dp.message(Command("activate"))
async def cmd_activate(message: Message):

@dp.message(Command("mycourses")) # Предоставляет кнопки для продолжения или повторного просмотра
@db_exception_handler # Показывает список активных и завершенных курсов # Разделяет курсы на активные и завершенные
async def cmd_mycourses(message: Message):

@dp.message(Command("adm_approve_course"), F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler # Админ-команда для одобрения курса
async def approve_course(message: Message):

@dp.message(Command("lesson"))
@db_exception_handler
async def cmd_lesson(message: types.Message):


@dp.message(Command("progress"))
@db_exception_handler # Обработчик для команды просмотра прогресса по всем курсам
async def cmd_progress(message: Message):


@dp.callback_query(lambda c: c.data.startswith("start_lesson:"))
@db_exception_handler # функция для отправки урока пользователю
async def start_lesson_callback(callback: CallbackQuery):

@dp.callback_query(lambda c: c.data.startswith("lesson_complete:"))
@db_exception_handler # # Обрабатывает нажатие "Урок изучен" Обработчик для колбэков от кнопок Проверяет необходимость домашнего задания
async def complete_lesson_callback(callback_query: CallbackQuery, course_id, lesson_num):

@dp.message(F.text, F.chat.id == ADMIN_GROUP_ID)
@db_exception_handler
async def process_rejection_reason(message: Message):

@dp.callback_query(lambda c: c.data.startswith("approve_hw:") or c.data.startswith("reject_hw:"))
@db_exception_handler
async def handle_homework_decision(callback_query: CallbackQuery):



@dp.message(lambda message: message.chat.id in COURSE_GROUPS)
@db_exception_handler
async def handle_group_message(message: Message):

@dp.message(Command("mycourses"))
@db_exception_handler
async def cmd_mycourses(message: Message):

@dp.message(Command("completed_courses")) # Показывает список завершенных курсов # Реализует пагинацию уроков
@db_exception_handler # Позволяет просматривать уроки с сниппетами
async def cmd_completed_courses(message: Message):

@dp.callback_query(lambda c: c.data.startswith("view_completed_course:"))
@db_exception_handler
async def view_completed_course(callback_query: CallbackQuery):

@dp.callback_query(lambda c: c.data.startswith("view_completed_lesson:"))
@db_exception_handler
async def view_completed_lesson(callback_query: CallbackQuery):

@dp.callback_query(lambda c: c.data.startswith("show_full_lesson:"))
@db_exception_handler
async def show_full_lesson(callback_query: CallbackQuery):

@dp.callback_query(lambda c: c.data.startswith("review_course:"))
@db_exception_handler
async def review_course_callback(callback_query: CallbackQuery):

@dp.callback_query(lambda c: c.data.startswith("review_lesson:"))
@db_exception_handler
async def review_lesson_callback(callback_query: CallbackQuery):

@dp.callback_query(lambda c: c.data.startswith("review_prev:") or c.data.startswith("review_next:"))
@db_exception_handler
async def review_navigation_callback(callback_query: CallbackQuery):

@dp.callback_query(lambda c: c.data.startswith("submit_homework:"))
@db_exception_handler # обработка отправки ДЗ
async def submit_homework_callback(callback_query: CallbackQuery, course_id, lesson_num):

# обработка содержимого ДЗ
@db_exception_handler
async def process_homework_submission(message: Message):



@dp.message(Command("help"))
async def help_command(message: types.Message):

@dp.message(F.text)
@db_exception_handler
async def process_activation_code(message: Message):


@dp.message(F.text)  # Обработчик текстовых сообщений
async def check_activation_code(message: types.Message):


@dp.callback_query(F.data == "menu_current_lesson")
@db_exception_handler
async def process_current_lesson(callback: CallbackQuery):

# Обработчик последний - чтобы не мешал другим обработчикам работать. Порядок имеет значение
@dp.message(F.text)  # Фильтр только для текстовых сообщений
async def process_message(message: types.Message):

