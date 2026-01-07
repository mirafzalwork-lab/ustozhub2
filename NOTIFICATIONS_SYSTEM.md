# 🔔 Система уведомлений UstozHub

## 📋 Описание

Полноценная система уведомлений (Notifications) для образовательной платформы UstozHub с:
- ✅ Управлением через Django Admin
- ✅ Индикатором непрочитанных уведомлений в navbar
- ✅ Детальным просмотром и историей
- ✅ Таргетированием по ролям (студенты/учителя/админы)
- ✅ Адаптивным дизайном

---

## 🚀 Установка и настройка

### 1. Создание миграций

```bash
cd /Users/Macbook/Desktop/ustozhubuz
python manage.py makemigrations teachers
python manage.py migrate
```

### 2. Создание суперпользователя (если еще нет)

```bash
python manage.py createsuperuser
```

### 3. Запуск сервера

```bash
python manage.py runserver
```

---

## 📂 Структура реализации

### **Модели** (`teachers/models.py`)

#### `Notification`
- `title` - Заголовок уведомления
- `short_text` - Краткий текст для списка
- `full_text` - Полный текст при детальном просмотре
- `image` - Опциональное изображение
- `action_url` - Ссылка для перехода
- `target` - Целевая аудитория (all/students/teachers/admins)
- `is_active` - Активность уведомления
- `priority` - Приоритет отображения
- `created_at` / `updated_at` - Временные метки

#### `NotificationRead`
- `user` - Пользователь
- `notification` - Уведомление
- `read_at` - Время прочтения

### **Views** (`teachers/views.py`)

1. **`notifications_list`** - Список всех уведомлений пользователя
2. **`notification_detail`** - Детальный просмотр уведомления
3. **`mark_notification_read`** - AJAX endpoint для пометки как прочитанного
4. **`mark_all_notifications_read`** - Пометить все как прочитанные
5. **`notifications_dropdown`** - AJAX endpoint для dropdown (опционально)

### **Templates** (`templates/notifications/`)

1. **`list.html`** - Страница со списком уведомлений
2. **`detail.html`** - Детальная страница уведомления

### **URLs** (`teachers/urls.py`)

```python
path('notifications/', notifications_list, name='notifications_list'),
path('notifications/<int:notification_id>/', notification_detail, name='notification_detail'),
path('notifications/<int:notification_id>/mark-read/', mark_notification_read, name='mark_notification_read'),
path('notifications/mark-all-read/', mark_all_notifications_read, name='mark_all_notifications_read'),
```

### **Admin** (`teachers/admin.py`)

Расширенная админ-панель с:
- Цветными badges для статуса и аудитории
- Приоритетом уведомлений
- Статистикой прочтений
- Массовыми действиями (активация/деактивация)
- Фильтрами по дате, аудитории, статусу

### **Context Processor** (`teachers/context_processors.py`)

```python
def unread_notifications_count(request):
    # Возвращает количество непрочитанных уведомлений для navbar
```

---

## 🎨 UI/UX Особенности

### Navbar
- Иконка колокольчика 🔔 рядом с сообщениями
- Красный badge с количеством непрочитанных
- Анимация pulse для привлечения внимания

### Список уведомлений
- Визуальное выделение непрочитанных (голубой фон + синяя граница)
- Красная точка для новых уведомлений
- Hover эффекты и анимации
- Кнопка "Отметить все как прочитанные"
- Пагинация

### Детальная страница
- Полный текст уведомления
- Изображение (если есть)
- Action button для внешних ссылок
- Breadcrumbs для навигации
- Кнопка "Отметить как прочитанное"

### Адаптивность
- ✅ Desktop (1920px+)
- ✅ Tablet (768px - 1024px)
- ✅ Mobile (320px - 767px)

---

## 🔧 Использование

### Через Django Admin

1. Перейдите в `/admin/`
2. Выберите **"Уведомления"** → **"Notifications"**
3. Нажмите **"Добавить уведомление"**

#### Заполните поля:
- **Заголовок** - "Важное обновление платформы"
- **Краткий текст** - "Мы обновили интерфейс профиля учителя"
- **Полный текст** - Детальное описание изменений
- **Целевая аудитория** - Выберите (все/студенты/учителя/админы)
- **Активно** - ✓
- **Приоритет** - 10 (высокий), 0 (обычный), -10 (низкий)

4. **Сохранить**

### Программно (через код)

```python
from teachers.models import Notification

# Создание уведомления
notification = Notification.objects.create(
    title="Новая функция",
    short_text="Теперь вы можете...",
    full_text="Детальное описание новой функции...",
    target='all',  # или 'students', 'teachers', 'admins'
    is_active=True,
    priority=5
)

# Получение непрочитанных уведомлений пользователя
unread = Notification.get_user_notifications(user, include_read=False)

# Пометить как прочитанное
notification.mark_as_read(user)

# Проверить, прочитано ли
is_read = notification.is_read_by(user)

# Получить количество непрочитанных
count = Notification.get_unread_count(user)
```

---

## 🎯 API endpoints

### GET `/notifications/`
Список уведомлений пользователя (требует авторизации)

### GET `/notifications/<id>/`
Детальный просмотр уведомления (автоматически помечает как прочитанное)

### POST `/notifications/<id>/mark-read/`
AJAX endpoint для пометки как прочитанного
```json
{
  "success": true,
  "unread_count": 3
}
```

### POST `/notifications/mark-all-read/`
Пометить все уведомления как прочитанные
```json
{
  "success": true,
  "marked_count": 5,
  "unread_count": 0
}
```

---

## 🔐 Права доступа

### Просмотр уведомлений
- ✅ Только авторизованные пользователи
- ✅ Пользователь видит только уведомления для своей роли

### Создание уведомлений
- ✅ Только через Django Admin
- ✅ Доступно администраторам (staff)

---

## 📊 Таргетинг уведомлений

### `target='all'`
Видят **все** зарегистрированные пользователи

### `target='students'`
Видят только пользователи с `user_type='student'`

### `target='teachers'`
Видят только пользователи с `user_type='teacher'`

### `target='admins'`
Видят только пользователи с `is_staff=True` или `is_superuser=True`

---

## 🎨 Дизайн-система

### Цвета
- **Primary**: `#0A2540` (темно-синий)
- **Accent**: `#3B82F6` (синий)
- **Success**: `#10B981` (зеленый)
- **Danger**: `#EF4444` (красный)

### Иконки (Font Awesome 6.0)
- 🔔 `fa-bell` - колокольчик
- ✓ `fa-check-circle` - прочитано
- ○ `fa-circle` - непрочитано
- ⬆ `fa-arrow-up` - высокий приоритет

---

## ⚡ Оптимизация

### Кэширование
- Количество непрочитанных уведомлений кэшируется
- Инвалидация кэша при пометке как прочитанного

### Индексы базы данных
```python
indexes = [
    models.Index(fields=['is_active', 'target']),
    models.Index(fields=['-priority', '-created_at']),
]
```

### Query оптимизация
- Используется `select_related()` где возможно
- Batch операции для пометки нескольких уведомлений

---

## 🧪 Тестирование

### Создайте тестовые уведомления

1. Для всех пользователей
2. Только для студентов
3. Только для учителей
4. С высоким приоритетом
5. С изображением и action_url

### Проверьте:
- ✅ Badge в navbar обновляется
- ✅ Непрочитанные выделены визуально
- ✅ Пометка как прочитанное работает
- ✅ Фильтрация по ролям работает корректно
- ✅ Адаптивность на мобильных

---

## 🚨 Troubleshooting

### Не отображается badge в navbar
Проверьте, что context processor добавлен в `settings.py`:
```python
'teachers.context_processors.unread_notifications_count',
```

### Уведомления не видны пользователю
- Проверьте `is_active=True`
- Проверьте `target` соответствует роли пользователя
- Проверьте авторизацию пользователя

### Ошибки при миграции
```bash
python manage.py migrate --fake teachers zero
python manage.py migrate teachers
```

---

## 📈 Будущие улучшения

### Опциональные фичи:
- [ ] Real-time уведомления через WebSockets
- [ ] Email уведомления
- [ ] Telegram уведомления
- [ ] Push-уведомления
- [ ] Категории уведомлений
- [ ] Настройки предпочтений пользователя
- [ ] Группировка уведомлений по дате
- [ ] Поиск по уведомлениям

---

## 👨‍💻 Автор

Реализовано как senior Django full-stack разработчик
Дата: 7 января 2026

---

## 📝 Лицензия

Часть проекта UstozHub - образовательная платформа для поиска учителей.

---

## 🎉 Готово!

Система уведомлений полностью интегрирована и готова к использованию.

Для проверки:
1. Создайте миграции: `python manage.py makemigrations && python manage.py migrate`
2. Запустите сервер: `python manage.py runserver`
3. Перейдите в админку: `/admin/`
4. Создайте тестовое уведомление
5. Проверьте navbar - должен появиться badge 🔔
