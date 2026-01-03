# 🚀 Кэширование в UstozHub

## Обзор

В приложение добавлено комплексное кэширование для оптимизации производительности. Используется Django cache framework.

## Настройки кэширования

### Константы TTL (Time To Live)

```python
CACHE_TTL = 300          # 5 минут (по умолчанию)
CACHE_TTL_SHORT = 60     # 1 минута (часто меняющиеся данные)
CACHE_TTL_LONG = 3600    # 1 час (редко меняющиеся данные)
```

## Кэшируемые методы

### 1. SubjectCategory.get_subjects_count()
**Ключ кэша**: `category_subjects_count_{category_id}`  
**TTL**: 1 час (CACHE_TTL_LONG)  
**Описание**: Количество активных предметов в категории  
**Инвалидация**: При создании/удалении предмета в категории

### 2. Subject.get_teachers_count()
**Ключ кэша**: `subject_teachers_count_{subject_id}`  
**TTL**: 5 минут (CACHE_TTL)  
**Описание**: Количество активных учителей, преподающих предмет  
**Инвалидация**: При сохранении/удалении TeacherSubject

### 3. TeacherProfile.get_views_count(period)
**Ключ кэша**: `teacher_views_{teacher_id}_{period}`  
**TTL**: 1 минута (CACHE_TTL_SHORT)  
**Описание**: Количество просмотров профиля за период  
**Периоды**: `all`, `day`, `week`, `month`  
**Инвалидация**: При сохранении ProfileView

### 4. TeacherProfile.get_unique_viewers_count(period)
**Ключ кэша**: `teacher_unique_views_{teacher_id}_{period}`  
**TTL**: 1 минута (CACHE_TTL_SHORT)  
**Описание**: Количество уникальных просмотров (по IP)  
**Инвалидация**: При сохранении ProfileView

### 5. TeacherProfile.get_min_price()
**Ключ кэша**: `teacher_min_price_{teacher_id}`  
**TTL**: 5 минут (CACHE_TTL)  
**Описание**: Минимальная цена за час у учителя  
**Инвалидация**: При сохранении/удалении TeacherSubject

### 6. StudentProfile.get_views_count(period)
**Ключ кэша**: `student_views_{student_id}_{period}`  
**TTL**: 1 минута (CACHE_TTL_SHORT)  
**Описание**: Количество просмотров профиля ученика  
**Инвалидация**: При сохранении ProfileView

### 7. StudentProfile.get_unique_viewers_count(period)
**Ключ кэша**: `student_unique_views_{student_id}_{period}`  
**TTL**: 1 минута (CACHE_TTL_SHORT)  
**Описание**: Количество уникальных просмотров профиля ученика  
**Инвалидация**: При сохранении ProfileView

## Автоматическая инвалидация кэша

### TeacherProfile
```python
def clear_cache(self):
    """Очистить весь кэш связанный с профилем учителя"""
    for period in ['all', 'day', 'week', 'month']:
        cache.delete(f'teacher_views_{self.id}_{period}')
        cache.delete(f'teacher_unique_views_{self.id}_{period}')
    cache.delete(f'teacher_min_price_{self.id}')
```

Вызывается автоматически при `save()`

### StudentProfile
```python
def clear_cache(self):
    """Очистить весь кэш связанный с профилем ученика"""
    for period in ['all', 'day', 'week', 'month']:
        cache.delete(f'student_views_{self.id}_{period}')
        cache.delete(f'student_unique_views_{self.id}_{period}')
```

Вызывается автоматически при `save()`

### TeacherSubject
При сохранении:
- Инвалидирует `teacher_min_price_{teacher_id}`
- Инвалидирует `subject_teachers_count_{subject_id}`

При удалении - аналогично

### ProfileView
При сохранении автоматически вызывает:
- `teacher_profile.clear_cache()` если это просмотр учителя
- `student_profile.clear_cache()` если это просмотр ученика

## Ручная очистка кэша

### Через Django shell
```python
from django.core.cache import cache

# Очистить весь кэш
cache.clear()

# Очистить конкретный ключ
cache.delete('teacher_views_123_all')

# Очистить кэш учителя
from teachers.models import TeacherProfile
teacher = TeacherProfile.objects.get(id=123)
teacher.clear_cache()

# Очистить кэш ученика
from teachers.models import StudentProfile
student = StudentProfile.objects.get(id=456)
student.clear_cache()
```

### Через команду управления
```bash
python manage.py shell
>>> from django.core.cache import cache
>>> cache.clear()
```

## Мониторинг производительности

### Проверить попадания в кэш
```python
import logging
logger = logging.getLogger(__name__)

# В методе с кэшированием
cache_key = f'teacher_views_{self.id}_{period}'
count = cache.get(cache_key)
if count is not None:
    logger.debug(f"Cache HIT: {cache_key}")
    return count
else:
    logger.debug(f"Cache MISS: {cache_key}")
```

### Статистика кэша (если используется Redis)
```bash
redis-cli
> INFO stats
> DBSIZE
```

## Рекомендации

### 1. Выбор TTL
- **Короткий TTL (60s)**: Для часто меняющихся данных (просмотры, счетчики)
- **Средний TTL (300s)**: Для умеренно меняющихся данных (цены, списки)
- **Длинный TTL (3600s)**: Для редко меняющихся данных (категории, справочники)

### 2. Ключи кэша
- Используйте понятные имена: `{model}_{field}_{id}_{param}`
- Включайте все параметры, влияющие на результат
- Избегайте очень длинных ключей (макс 250 символов)

### 3. Инвалидация
- Всегда инвалидируйте связанный кэш при изменении данных
- Используйте методы `clear_cache()` для комплексной очистки
- При массовых операциях очищайте кэш пакетно

### 4. Мониторинг
- Отслеживайте hit/miss ratio
- Проверяйте размер кэша
- Анализируйте производительность запросов

## Настройка бэкенда кэша

### settings.py
```python
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        },
        'KEY_PREFIX': 'ustozhub',
        'TIMEOUT': 300,  # По умолчанию 5 минут
    }
}
```

## Troubleshooting

### Кэш не работает
1. Проверьте, что Redis запущен: `redis-cli ping`
2. Проверьте настройки CACHES в settings.py
3. Проверьте, что импортирован cache: `from django.core.cache import cache`

### Устаревшие данные в кэше
1. Проверьте, что методы save()/delete() переопределены
2. Проверьте, что вызывается clear_cache()
3. Очистите кэш вручную: `cache.clear()`

### Низкая производительность
1. Увеличьте TTL для редко меняющихся данных
2. Проверьте hit/miss ratio
3. Оптимизируйте запросы к БД
4. Рассмотрите использование select_related/prefetch_related

## Преимущества текущей реализации

✅ Автоматическая инвалидация при изменении данных  
✅ Гибкие TTL для разных типов данных  
✅ Понятная структура ключей кэша  
✅ Методы для ручной очистки  
✅ Безопасная обработка отсутствующих данных  
✅ Логирование для отладки  
✅ Поддержка различных бэкендов (Redis, Memcached, Database)
