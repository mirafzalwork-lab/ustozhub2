# 📝 Шпаргалка по кэшированию UstozHub

## Быстрый старт

### Проверить Redis
```bash
redis-cli ping
# Должен вернуть: PONG
```

### Очистить весь кэш
```python
from django.core.cache import cache
cache.clear()
```

## Ключи кэша

| Метод | Ключ | TTL |
|-------|------|-----|
| Категория → предметы | `category_subjects_count_{id}` | 1 час |
| Предмет → учителя | `subject_teachers_count_{id}` | 5 мин |
| Учитель → просмотры | `teacher_views_{id}_{period}` | 1 мин |
| Учитель → уникальные | `teacher_unique_views_{id}_{period}` | 1 мин |
| Учитель → мин. цена | `teacher_min_price_{id}` | 5 мин |
| Ученик → просмотры | `student_views_{id}_{period}` | 1 мин |
| Ученик → уникальные | `student_unique_views_{id}_{period}` | 1 мин |

## Очистка кэша

### Конкретный учитель
```python
teacher = TeacherProfile.objects.get(id=123)
teacher.clear_cache()
```

### Конкретный ученик
```python
student = StudentProfile.objects.get(id=456)
student.clear_cache()
```

### Все просмотры учителя
```python
for period in ['all', 'day', 'week', 'month']:
    cache.delete(f'teacher_views_123_{period}')
    cache.delete(f'teacher_unique_views_123_{period}')
```

## Отладка

### Проверить значение
```python
from django.core.cache import cache
value = cache.get('teacher_views_123_all')
print(value)  # None если не в кэше
```

### Установить значение
```python
cache.set('my_key', 'value', timeout=300)  # 5 минут
```

### Удалить значение
```python
cache.delete('my_key')
```

## Мониторинг Redis

```bash
# Подключиться к Redis
redis-cli

# Количество ключей
> DBSIZE

# Статистика
> INFO stats

# Все ключи (осторожно на проде!)
> KEYS *

# Поиск по паттерну
> KEYS teacher_*

# Получить значение
> GET ustozhub:1:teacher_views_123_all

# Удалить ключ
> DEL ustozhub:1:teacher_views_123_all

# Время жизни ключа
> TTL ustozhub:1:teacher_views_123_all
```

## Troubleshooting

### Redis не запущен
```bash
# macOS (Homebrew)
brew services start redis

# Linux
sudo systemctl start redis

# Проверить
redis-cli ping
```

### Очистить Redis полностью
```bash
redis-cli FLUSHDB
```

### Посмотреть все ключи UstozHub
```bash
redis-cli KEYS "ustozhub:*"
```

## Когда кэш инвалидируется автоматически

✅ При сохранении TeacherProfile  
✅ При сохранении StudentProfile  
✅ При сохранении TeacherSubject  
✅ При удалении TeacherSubject  
✅ При сохранении ProfileView

## Примеры использования

### Получить статистику учителя
```python
teacher = TeacherProfile.objects.get(id=123)

# Все просмотры (из кэша)
total = teacher.get_views_count('all')

# За неделю (из кэша)
week = teacher.get_views_count('week')

# Уникальные за день (из кэша)
unique = teacher.get_unique_viewers_count('day')

# Минимальная цена (из кэша)
min_price = teacher.get_min_price()
```

### Обновить данные и сбросить кэш
```python
teacher = TeacherProfile.objects.get(id=123)
teacher.bio = "Новое описание"
teacher.save()  # Автоматически вызовет clear_cache()
```

## Настройки TTL

```python
CACHE_TTL = 300         # 5 минут - стандарт
CACHE_TTL_SHORT = 60    # 1 минута - счетчики
CACHE_TTL_LONG = 3600   # 1 час - справочники
```

## Полезные команды Django

```bash
# Открыть shell
python manage.py shell

# Очистить кэш
python manage.py shell -c "from django.core.cache import cache; cache.clear()"
```
