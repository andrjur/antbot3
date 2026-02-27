# Feature: Course Description Management

## Problem
Currently, course descriptions are managed manually through:
1. `settings.json` file (editing JSON)
2. Database `courses.description` field (manual SQL)
3. No convenient way to edit descriptions through the bot interface

## Proposed Solution
Add functionality for admins to view and edit course descriptions directly through the bot.

## User Stories

### Story 1: View Course Description
As an admin, I want to view the current description of any course so I can see what students see.

**Acceptance Criteria:**
- Command `/course_desc <course_id>` shows current description
- Shows both from database and group_messages (fallbacks)
- Shows which source is being used

### Story 2: Edit Course Description
As an admin, I want to edit a course description through the bot so I don't need to access files directly.

**Acceptance Criteria:**
- Command `/edit_desc <course_id>` opens description editor
- Supports markdown formatting
- Can upload photo/video as description media
- Preview before saving
- Confirm/cancel buttons

### Story 3: Set Description Media
As an admin, I want to set a photo or video as the course cover/description so it's shown when students activate the course.

**Acceptance Criteria:**
- Command `/set_course_media <course_id>` accepts photo/video
- Media is shown in course description message
- Works with existing text descriptions (combines text + media)

## Technical Implementation

### New Commands
1. `/course_desc <course_id>` - View current description
2. `/edit_desc <course_id>` - Edit description (text)
3. `/set_course_media <course_id>` - Set media (photo/video)

### Database Changes
- Use existing `courses.description` field
- Optionally add `courses.media_file_id` for cover media
- Or use `group_messages` with special `lesson_num = 0` for media storage

### UI/UX
```
/course_desc base

📚 Описание курса "base":

[Текст описания]

📸 Медиа: [есть/нет]
📝 Источник: БД / group_messages / Не найдено

[✏️ Редактировать] [🖼️ Изменить медиа]
```

```
/edit_desc base

Текущее описание:
[текст]

Отправьте новое описание (поддерживается Markdown):
(или "отмена" для отмены)
```

```
/set_course_media base

Отправьте фото или видео для обложки курса:
(или "удалить" чтобы убрать текущее)
```

## Priority
**Medium** - Important for content management but not blocking current functionality

## Estimated Effort
- 2-3 hours development
- 30 minutes testing

## Dependencies
- Existing admin authentication system
- Existing database schema
- Course management functionality

## Notes
- Descriptions can be stored in:
  1. `courses.description` (text field) - PREFERRED
  2. `group_messages` with `lesson_num = 0` (existing pattern)
  
- Media should be stored as `file_id` in database
- Should support markdown formatting for rich text
- Need to handle fallback: if no description in DB, check group_messages

## Future Enhancements
- Rich text editor with buttons (bold, italic, links)
- Multi-language descriptions
- A/B testing different descriptions
- Analytics: which description converts better