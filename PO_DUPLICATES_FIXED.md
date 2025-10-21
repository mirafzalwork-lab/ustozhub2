# ✅ Исправление дубликатов в .po файлах

## 🔍 Проблема:

В русском `.po` файле были **дублирующие переводы** - одни и те же тексты определялись дважды:

1. Для `teacher_detail.html` (строки 575-625)
2. Для `teacher_profile.html` (строки 745-788)

### Ошибки:
```
duplicate message definition at line 746 (first at 577) - "лет опыта"
duplicate message definition at line 750 (first at 581) - "отзывов"
duplicate message definition at line 754 (first at 585) - "Онлайн"
duplicate message definition at line 758 (first at 589) - "Не указаны"
duplicate message definition at line 762 (first at 593) - "Верифицирован"
duplicate message definition at line 766 (first at 597) - "Топ преподаватель"
duplicate message definition at line 770 (first at 601) - "О преподавателе"
duplicate message definition at line 774 (first at 609) - "Предметы и цены"
duplicate message definition at line 778 (first at 613) - "Пробный урок бесплатно"
duplicate message definition at line 782 (first at 617) - "Предметы не указаны"
duplicate message definition at line 786 (first at 621) - "Образование"
```

---

## ✅ Решение:

Удалены дублирующие записи из строк 745-788.

### ❌ БЫЛО (неправильно):
```po
#: templates/logic/teacher_detail.html:1112
msgid "лет опыта"
msgstr "лет опыта"

# ... другие переводы ...

#: templates/logic/teacher_profile.html:649
msgid "лет опыта"  # ❌ ДУБЛИКАТ!
msgstr "лет опыта"
```

### ✅ СТАЛО (правильно):
```po
#: templates/logic/teacher_detail.html:1112
#: templates/logic/teacher_profile.html:649
msgid "лет опыта"
msgstr "лет опыта"
```

**ИЛИ** просто оставить одну запись, а `makemessages` автоматически добавит все ссылки.

---

## 📊 Статус файлов:

### ✅ `/locale/ru/LC_MESSAGES/django.po`
- **Было:** 11 дубликатов
- **Исправлено:** Все дубликаты удалены
- **Статус:** ✅ Готов

### ✅ `/locale/uz/LC_MESSAGES/django.po`
- **Дубликатов:** Нет
- **Статус:** ✅ Готов

### ✅ `/locale/en/LC_MESSAGES/django.po`
- **Дубликатов:** Нет
- **Статус:** ✅ Готов

---

## 🚀 Теперь можно запускать:

```bash
# Обновить переводы
python manage.py makemessages -l ru -l uz -l en

# Скомпилировать
python manage.py compilemessages
```

---

## 💡 Как избежать дубликатов в будущем:

1. **Не копируйте** переводы вручную между файлами
2. **Используйте makemessages** - он автоматически найдет все использования текста
3. **Если текст используется в нескольких местах**, Django автоматически добавит все ссылки:
   ```po
   #: templates/file1.html:10
   #: templates/file2.html:20
   #: templates/file3.html:30
   msgid "Текст"
   msgstr "Перевод"
   ```

4. **Проверяйте дубликаты** перед коммитом:
   ```bash
   msgfmt --check locale/ru/LC_MESSAGES/django.po
   ```

---

**Дата исправления:** 19 октября 2025  
**Статус:** ✅ ВСЕ ДУБЛИКАТЫ УДАЛЕНЫ
