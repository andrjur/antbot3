logger.info(f"init_db ")

await conn.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT COLLATE NOCASE,
    first_name TEXT COLLATE NOCASE,
    last_name TEXT COLLATE NOCASE,
    is_active INTEGER DEFAULT 1,
    is_banned INTEGER DEFAULT 0,
    registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# User profiles with additional info
await conn.execute('''
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id INTEGER PRIMARY KEY,
    username TEXT COLLATE NOCASE,
    first_name TEXT COLLATE NOCASE,
    last_name TEXT COLLATE NOCASE,
    alias TEXT COLLATE NOCASE,
    tokens INTEGER DEFAULT 0,
    referrer_id INTEGER,
    birthday TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
)
''')

# User states
await conn.execute('''
CREATE TABLE IF NOT EXISTS user_states (
    user_id INTEGER PRIMARY KEY,
    current_course_id TEXT, -- ID текущего курса
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (current_course_id) REFERENCES courses(course_id)
)
''')

# Courses
await conn.execute('''
CREATE TABLE IF NOT EXISTS courses (
    course_id TEXT PRIMARY KEY,
    title TEXT NOT NULL COLLATE NOCASE,
    description TEXT COLLATE NOCASE,
    total_lessons INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    channel_id INTEGER, -- ID Telegram-канала с контентом
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Course activation codes
await conn.execute('''
CREATE TABLE IF NOT EXISTS course_activation_codes (
    code_word TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    course_type TEXT NOT NULL,
    price_rub INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses(course_id)
)
''')

# Course versions (different tiers/packages)
await conn.execute('''
CREATE TABLE IF NOT EXISTS course_versions (
    course_id TEXT,
    version_id TEXT,
    title TEXT NOT NULL COLLATE NOCASE,
    price REAL DEFAULT 0,
    activation_code TEXT,
    homework_check_type TEXT DEFAULT 'admin', -- 'admin' или 'self'
    PRIMARY KEY (course_id, version_id),
    FOREIGN KEY (course_id) REFERENCES courses(course_id)
)
''')

# User courses (enrollments)
await conn.execute('''
CREATE TABLE IF NOT EXISTS user_courses (
    user_id INTEGER,
    course_id TEXT,
    version_id TEXT,
    current_lesson INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending', -- pending, active, completed
    activation_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expiry_date TIMESTAMP,
    is_completed INTEGER DEFAULT 0,
    next_lesson_date TIMESTAMP,  -- <--- Добавьте эту строку
    last_lesson_date TIMESTAMP,
    PRIMARY KEY (user_id, course_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (course_id, version_id) REFERENCES course_versions(course_id, version_id)
)
''')

# Homework submissions
await conn.execute('''
CREATE TABLE IF NOT EXISTS homework (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    course_id TEXT,
    lesson_num INTEGER,
    message_id INTEGER,
    status TEXT DEFAULT 'pending', -- pending, approved, rejected
    feedback TEXT,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (user_id, course_id) REFERENCES user_courses(user_id, course_id)
)
''')

# SAVE ALL COURSES INFO
await conn.execute('''
CREATE TABLE IF NOT EXISTS group_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    content_type TEXT NOT NULL,
    text TEXT,
    file_id TEXT,
    is_forwarded BOOLEAN DEFAULT FALSE,
    forwarded_from_chat_id INTEGER,
    forwarded_message_id INTEGER,
    level integer DEFAULT 1,
    lesson_num integer,
    is_bouns BOOLEAN DEFAULT FALSE,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
''')

# Lesson content mapping
await conn.execute('''
CREATE TABLE IF NOT EXISTS lesson_content_map (
    course_id TEXT,
    lesson_num INTEGER,
    start_message_id INTEGER,
    end_message_id INTEGER,
    snippet TEXT COLLATE NOCASE, -- Сниппет урока todo: 
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (course_id, lesson_num)
)
''')

# Promo codes
await conn.execute('''
CREATE TABLE IF NOT EXISTS promo_codes (
    code TEXT PRIMARY KEY,
    course_id TEXT COLLATE NOCASE,
    discount_percent INTEGER,
    uses_limit INTEGER,
    uses_count INTEGER DEFAULT 0,
    expiry_date TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (course_id) REFERENCES courses(course_id)
)
''')

# Advertisements
await conn.execute('''
CREATE TABLE IF NOT EXISTS advertisements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
''')

# Token transactions
await conn.execute('''
CREATE TABLE IF NOT EXISTS token_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    amount INTEGER,
    reason TEXT COLLATE NOCASE,
    transaction_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
)
''')

# User activity log
await conn.execute('''
CREATE TABLE IF NOT EXISTS user_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT COLLATE NOCASE,
    details TEXT COLLATE NOCASE,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
)
''')
