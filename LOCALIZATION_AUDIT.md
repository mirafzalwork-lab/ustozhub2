# Аудит локализации проекта UstozHub

## Статус: В процессе

### HTML файлы с поддержкой i18n (8/17):
✅ base.html
✅ home.html
✅ student_profile.html
✅ students_list.html
✅ teacher_profile.html
✅ teacher_register_step1.html
✅ teacher_register_step2.html
✅ teacher_register_step3.html

### HTML файлы БЕЗ поддержки i18n (9/17):
❌ login.html
❌ logout.html
❌ register_choose.html
❌ register_student.html
❌ student_detail.html
❌ student_profile_edit.html
❌ teacher_detail.html
❌ teacher_profile_edit.html
❌ teacher_register_complete.html

### Python файлы для проверки:
- teachers/forms.py (нужно проверить label и help_text)
- teachers/views.py (нужно проверить messages и тексты)
- teachers/models.py (нужно проверить verbose_name и choices)

### План действий:
1. ✅ Добавить {% load i18n %} во все HTML без поддержки
2. ✅ Обернуть все тексты в {% trans %} или {% blocktrans %}
3. ⏳ Проверить forms.py на переводы
4. ⏳ Проверить views.py на использование gettext
5. ⏳ Проверить models.py на verbose_name
6. ⏳ Запустить makemessages
7. ⏳ Заполнить переводы для RU/EN/UZ
8. ⏳ Скомпилировать переводы

### Найденные тексты для перевода (примеры из login.html):
- "Вход в UstozHub"
- "Войдите чтобы продолжить"
- "Забыли пароль?"
- "Войти"
- "или"
- "Нет аккаунта?"
- "Зарегистрироваться"
- "Вход..." (в JavaScript)
