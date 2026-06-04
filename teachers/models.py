# models.py
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from django.core.validators import MinValueValidator, MaxValueValidator
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.core.cache import cache
from PIL import Image
import math
import uuid
from datetime import timedelta, time as dt_time
import os
import logging

# Глобальный logger для моделей
logger = logging.getLogger(__name__)

# Константы для кэширования
CACHE_TTL = 300  # 5 минут по умолчанию
CACHE_TTL_SHORT = 60  # 1 минута для часто меняющихся данных
CACHE_TTL_LONG = 3600  # 1 час для редко меняющихся данных

# Константа для дней недели (используется в TeacherProfile и StudentProfile)
WEEKDAYS_MAP = {
    '1': 'Пн', '2': 'Вт', '3': 'Ср', '4': 'Чт',
    '5': 'Пт', '6': 'Сб', '7': 'Вс'
}


def _filter_views_by_period(views_qs, period):
    """Фильтрует queryset просмотров по периоду."""
    if period == 'day':
        return views_qs.filter(viewed_at__gte=timezone.now() - timedelta(days=1))
    elif period == 'week':
        return views_qs.filter(viewed_at__gte=timezone.now() - timedelta(weeks=1))
    elif period == 'month':
        return views_qs.filter(viewed_at__gte=timezone.now() - timedelta(days=30))
    return views_qs

class User(AbstractUser):
    """Расширенная модель пользователя"""
    USER_TYPES = [
        ('student', _('Ученик')),
        ('teacher', _('Учитель')),
    ]

    GENDER_CHOICES = [
        ('male', _('Мужской')),
        ('female', _('Женский')),
    ]
    
    user_type = models.CharField(max_length=10, choices=USER_TYPES, default='student')
    phone = models.CharField(max_length=20, blank=True, null=True)
    age = models.PositiveIntegerField(validators=[MinValueValidator(10), MaxValueValidator(100)], null=True, blank=True)
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, blank=True, null=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta(AbstractUser.Meta):
        constraints = [
            # Case-insensitive уникальность email для НЕпустых значений.
            # Пустой email ('') разрешён множеству пользователей — он исключён условием.
            models.UniqueConstraint(
                Lower('email'),
                condition=~Q(email=''),
                name='uniq_user_email_ci',
            ),
        ]

    def save(self, *args, **kwargs):
        # Пере-сжимаем аватар ТОЛЬКО когда он реально поменялся — иначе на каждом
        # save() (правка профиля, обновление счётчиков и т.п.) шёл синхронный
        # декод/энкод JPEG в request-потоке.
        update_fields = kwargs.get('update_fields')
        avatar_changed = True
        if update_fields is not None and 'avatar' not in update_fields:
            avatar_changed = False
        elif self.pk and not self._state.adding:
            try:
                old_name = type(self).objects.only('avatar').get(pk=self.pk).avatar.name or ''
                avatar_changed = old_name != ((self.avatar.name or '') if self.avatar else '')
            except type(self).DoesNotExist:
                avatar_changed = True

        super().save(*args, **kwargs)

        if avatar_changed and self.avatar and hasattr(self.avatar, 'path') and os.path.exists(self.avatar.path):
            try:
                img = Image.open(self.avatar.path)
                if img.height > 300 or img.width > 300:
                    img.thumbnail((300, 300))
                    img.save(self.avatar.path)
            except (IOError, OSError) as e:
                # Логируем ошибку, но не прерываем сохранение пользователя
                logger.warning(f"Error processing avatar for user {self.username}: {e}", exc_info=True)

def normalize_search_text(*parts) -> str:
    """Нормализует набор строк в одно search_text-поле.
    Lowercase + collapse whitespace. Используется для быстрого LIKE-поиска.
    На PostgreSQL это поле станет основой GIN-индекса (pg_trgm).
    """
    cleaned = []
    for p in parts:
        if not p:
            continue
        s = str(p).strip().lower()
        if s:
            cleaned.append(s)
    text = ' '.join(cleaned)
    import re as _re
    return _re.sub(r'\s+', ' ', text)


class SubjectCategory(models.Model):
    """Категории предметов для удобной группировки"""
    name = models.CharField(max_length=100, unique=True, verbose_name=_('Название категории'))
    description = models.TextField(blank=True, verbose_name=_('Описание'))
    icon = models.CharField(max_length=50, blank=True, help_text=_("CSS класс иконки (например, fas fa-calculator)"))
    color = models.CharField(max_length=7, default='#3B82F6', help_text=_("Цвет в формате HEX (#3B82F6)"))
    order = models.PositiveIntegerField(default=0, help_text=_("Порядок сортировки"))
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = _('Категория предметов')
        verbose_name_plural = _('Категории предметов')

    def __str__(self):
        return self.name
    
    def get_subjects_count(self):
        """Количество активных предметов в категории (с кэшированием)"""
        cache_key = f'category_subjects_count_{self.id}'
        count = cache.get(cache_key)
        if count is None:
            count = self.subjects.filter(is_active=True).count()
            cache.set(cache_key, count, CACHE_TTL_LONG)
        return count


class Subject(models.Model):
    """Модель предметов"""
    category = models.ForeignKey(
        SubjectCategory, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='subjects',
        verbose_name=_('Категория')
    )
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text=_("CSS класс иконки"))
    is_active = models.BooleanField(default=True)
    is_popular = models.BooleanField(default=False, help_text=_("Популярный предмет (показывать в топе)"))
    search_text = models.TextField(
        blank=True,
        default='',
        help_text=_('Нормализованный текст для быстрого поиска (lowercase: name + description)')
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = _('Предмет')
        verbose_name_plural = _('Предметы')
        indexes = [
            models.Index(fields=['category', 'is_active']),
            models.Index(fields=['is_popular', 'is_active']),
            models.Index(fields=['search_text']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.search_text = normalize_search_text(self.name, self.description)
        super().save(*args, **kwargs)

    def get_teachers_count(self):
        """Количество учителей, преподающих этот предмет (с кэшированием)"""
        cache_key = f'subject_teachers_count_{self.id}'
        count = cache.get(cache_key)
        if count is None:
            count = self.teachersubject_set.filter(teacher__is_active=True).count()
            cache.set(cache_key, count, CACHE_TTL)
        return count

class City(models.Model):
    """Модель городов"""
    name = models.CharField(max_length=100, unique=True)
    country = models.CharField(max_length=100, default='Узбекистан')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = _('Город')
        verbose_name_plural = _('Города')

    def __str__(self):
        return f"{self.name}, {self.country}"

class Certificate(models.Model):
    """Модель сертификатов учителей"""
    name = models.CharField(max_length=200)
    issuer = models.CharField(max_length=200, help_text=_("Кто выдал сертификат"))
    file = models.FileField(upload_to='certificates/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Сертификат')
        verbose_name_plural = _('Сертификаты')

    def __str__(self):
        return f"{self.name} - {self.issuer}"

class TeacherProfile(models.Model):
    """Профиль учителя"""
    EDUCATION_LEVELS = [
        ('bachelor', _('Бакалавр')),
        ('master', _('Магистр')),
        ('phd', 'PhD'),
        ('other', _('Другое')),
    ]

    TEACHING_FORMATS = [
        ('online', _('Онлайн')),
        ('offline', _('Офлайн')),
        ('both', _('Онлайн и офлайн')),
    ]
    TEACHING_LANGUAGES = [
        ('uz', _('Узбекский')),
        ('ru', _('Русский')),
        ('en', _('Английский')),
        ('tr', _('Турецкий')),
        ('de', _('Немецкий')),
        ('fr', _('Французский')),
        ('other', _('Другой')),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='teacher_profile')
    
    # Основная информация
    bio = models.TextField(max_length=1000, blank=True, null=True, help_text=_("Краткое описание о себе"))
    education_level = models.CharField(max_length=20, blank=True, null=True, choices=EDUCATION_LEVELS)
    university = models.CharField(max_length=200, blank=True, null=True)
    specialization = models.CharField(max_length=200, blank=True, null=True)
    
    # Опыт работы
    experience_years = models.PositiveIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(50)],
        help_text=_("Лет опыта преподавания")
    )
    
    # Предметы и локация
    subjects = models.ManyToManyField(Subject, through='TeacherSubject')
    city = models.ForeignKey(City, on_delete=models.SET_NULL, null=True, blank=True)
    teaching_format = models.CharField(max_length=10, choices=TEACHING_FORMATS, default='both')
    
    # Контакты и доступность
    telegram = models.CharField(max_length=100, blank=True)
    whatsapp = models.CharField(max_length=20, blank=True)

    teaching_languages = models.CharField(
        max_length=100,
        blank=True,
        default='ru',
        help_text=_("Коды языков через запятую (uz,ru,en)")
    )
    # Время работы
    available_from = models.TimeField(default=dt_time(9, 0))
    available_to = models.TimeField(default=dt_time(21, 0))
    available_weekdays = models.CharField(max_length=20, default='1,2,3,4,5,6,7',
                                        help_text=_("Дни недели через запятую (1-7)"))
    
    # Индивидуальное расписание для каждого дня (JSON)
    # Новый формат (мультиинтервалы): {"monday": [{"from": "09:00", "to": "12:00"}, {"from": "15:00", "to": "18:00"}], ...}
    # Старый формат (один интервал, поддерживается на чтение): {"monday": {"from": "09:00", "to": "18:00"}, ...}
    weekly_schedule = models.JSONField(
        null=True,
        blank=True,
        default=dict,
        help_text=_("Индивидуальное расписание для каждого дня недели")
    )
    
    # Рейтинг и статус
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0.00)
    total_reviews = models.PositiveIntegerField(default=0)
    total_students = models.PositiveIntegerField(default=0)
    is_featured = models.BooleanField(default=False, help_text=_("Рекомендуемый учитель"))
    is_active = models.BooleanField(default=True)

    # Ранжирование: приоритет в выдаче (0-100, больше = выше)
    ranking_score = models.PositiveIntegerField(
        default=0,
        help_text=_("Приоритет в выдаче (0-100). Рассчитывается автоматически.")
    )
    
    # Видео-визитка (хранится в облачном хранилище, только URL)
    video_url = models.URLField(
        max_length=500,
        null=True,
        blank=True,
        help_text=_("URL видео-визитки в облачном хранилище")
    )

    # Денормализованный текст для быстрого поиска.
    # Конкатенация first_name + last_name + bio + university + specialization
    # в lowercase. Перезаписывается на каждом save() и при изменении связанного User.
    search_text = models.TextField(
        blank=True,
        default='',
        help_text=_('Нормализованный текст для быстрого поиска (заполняется автоматически)')
    )

    # Сертификаты
    certificates = models.ManyToManyField(Certificate, blank=True)
    
    # Даты
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    MODERATION_STATUS = [
        ('pending', _('На модерации')),
        ('approved', _('Одобрено')),
        ('rejected', _('Отклонено')),
    ]

    moderation_status = models.CharField(
        max_length=20,
        choices=MODERATION_STATUS,
        default='pending',
        verbose_name=_('Статус модерации')
    )

    moderation_comment = models.TextField(
        blank=True,
        verbose_name=_('Комментарий модератора'),
        help_text=_('Причина отклонения или рекомендации')
    )

    moderation_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Дата модерации')
    )

    moderated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='moderated_teachers',
        verbose_name=_('Проверил')
    )

    class Meta:
        verbose_name = _('Профиль учителя')
        verbose_name_plural = _('Профили учителей')
        ordering = ['-is_featured', '-ranking_score', '-rating', '-created_at']
        indexes = [
            models.Index(fields=['-is_featured', '-ranking_score', '-rating']),  # Основная сортировка
            models.Index(fields=['-rating', '-created_at']),  # Для сортировки на главной
            models.Index(fields=['is_active', 'moderation_status']),  # Для фильтров
            models.Index(fields=['city', 'is_active']),  # Для фильтра по городу
            models.Index(fields=['teaching_format']),  # Для фильтра формата
            models.Index(fields=['experience_years']),  # Для фильтра опыта
            models.Index(fields=['search_text']),  # Для быстрого LIKE-поиска
        ]
    
    def update_ranking_score(self):
        """
        Рассчитывает и обновляет приоритет учителя в выдаче.
        Формула: featured_bonus + rating_score + reviews_score + completeness_score
        Диапазон: 0-100
        """
        score = 0

        # Featured-бонус: +40 баллов (гарантирует топ выдачи)
        if self.is_featured:
            score += 40

        # Рейтинг: до 25 баллов (rating 5.0 = 25)
        score += int(float(self.rating) * 5)

        # Отзывы: до 15 баллов (логарифмическая шкала)
        if self.total_reviews > 0:
            score += min(15, int(math.log2(self.total_reviews + 1) * 5))

        # Полнота профиля: до 20 баллов
        completeness = 0
        if self.bio and len(self.bio) > 50:
            completeness += 5
        if self.university:
            completeness += 3
        if self.city:
            completeness += 3
        if self.user.avatar:
            completeness += 4
        if self.subjects.exists():
            completeness += 3
        if self.certificates.exists():
            completeness += 2
        score += min(20, completeness)

        self.ranking_score = min(100, score)
        self.save(update_fields=['ranking_score'])
        return self.ranking_score

    def approve(self, moderator, comment=''):
        """Одобрить профиль учителя"""
        self.moderation_status = 'approved'
        self.moderation_comment = comment
        self.moderation_date = timezone.now()
        self.moderated_by = moderator
        self.save()
        # Notification is created automatically in save() via _create_approval_notification()

    def reject(self, moderator, comment=''):
        """Отклонить профиль учителя"""
        self.moderation_status = 'rejected'
        self.moderation_comment = comment
        self.moderation_date = timezone.now()
        self.moderated_by = moderator
        self.save()
        # Notification is created automatically in save() via _create_rejection_notification()
    
    def _create_approval_notification(self, moderator, comment=''):
        """Вспомогательный метод для создания уведомления об одобрении"""
        try:
            teacher_name = self.user.get_full_name() or self.user.username
            short_text = "Поздравляем! Ваш профиль учителя успешно одобрен администратором."

            full_text = f"""Здравствуйте, {teacher_name}!

Рады сообщить вам, что ваш профиль учителя успешно прошёл модерацию и был одобрен администратором {moderator.get_full_name() or moderator.username}.

Теперь ваш профиль виден всем пользователям платформы, и ученики смогут находить вас и связываться с вами!

🎉 Желаем вам успехов в преподавании и много благодарных учеников!

Рекомендации для успешного старта:
• Регулярно проверяйте сообщения от учеников
• Отвечайте оперативно на запросы
• Поддерживайте актуальность информации в профиле
• Будьте пунктуальны и профессиональны

С уважением,
Команда UstozHub"""

            if comment:
                full_text += f"\n\nКомментарий модератора: {comment}"

            Notification.objects.create(
                title="Ваш профиль одобрен!",
                short_text=short_text,
                full_text=full_text,
                target='specific_user',
                target_user=self.user,
                is_active=True,
                priority=10,
                category=Notification.Category.SUCCESS,
                created_by=moderator
            )

            logger.info(f"Approval notification created for teacher: {self.user.username}")

        except Exception as e:
            logger.error(f"Failed to create approval notification: {e}", exc_info=True)
    
    def _create_rejection_notification(self, moderator, comment=''):
        """Вспомогательный метод для создания уведомления об отклонении"""
        try:
            teacher_name = self.user.get_full_name() or self.user.username
            short_text = "К сожалению, ваш профиль учителя не был одобрен администратором."

            full_text = f"""Здравствуйте, {teacher_name}!

К сожалению, ваш профиль учителя не прошёл модерацию.

"""

            if comment:
                full_text += f"""Причина отклонения:
{comment}

"""

            full_text += """Что делать дальше?
• Внимательно изучите комментарий модератора
• Исправьте указанные недостатки в профиле
• Обновите информацию и отправьте профиль на повторную проверку
• При необходимости обратитесь в службу поддержки

Мы всегда рады видеть качественных преподавателей на нашей платформе!

С уважением,
Команда UstozHub"""

            Notification.objects.create(
                title="Профиль не одобрен",
                short_text=short_text,
                full_text=full_text,
                target='specific_user',
                target_user=self.user,
                is_active=True,
                priority=10,
                category=Notification.Category.WARNING,
                created_by=moderator
            )

            logger.info(f"Rejection notification created for teacher: {self.user.username}")

        except Exception as e:
            logger.error(f"Failed to create rejection notification: {e}", exc_info=True)
    
    def get_teaching_languages_list(self):
        """Получить список названий языков преподавания"""
        languages_dict = dict(self.TEACHING_LANGUAGES)
        codes = self.teaching_languages.split(',')
        return [languages_dict.get(code.strip(), code) for code in codes if code.strip()]

    def get_teaching_languages_display(self):
        """Получить названия языков преподавания через запятую"""
        return ', '.join(self.get_teaching_languages_list())

    def get_views_count(self, period='all'):
        """Получить количество просмотров профиля (с кэшированием).
        Суммирует views_count (после дедупликации одна строка = N просмотров за день).
        """
        cache_key = f'teacher_views_{self.id}_{period}'
        count = cache.get(cache_key)
        if count is not None:
            return count
        qs = _filter_views_by_period(self.profile_views.all(), period)
        count = qs.aggregate(total=models.Sum('views_count'))['total'] or 0
        cache.set(cache_key, count, CACHE_TTL_SHORT)
        return count

    def get_unique_viewers_count(self, period='all'):
        """Уникальные зрители (по viewer_user/IP) — каждая строка после дедупа уже уникальна
        в рамках дня, поэтому достаточно посчитать distinct (viewer_user, viewer_ip)."""
        cache_key = f'teacher_unique_views_{self.id}_{period}'
        count = cache.get(cache_key)
        if count is not None:
            return count
        qs = _filter_views_by_period(self.profile_views.all(), period)
        count = qs.values('viewer_user_id', 'viewer_ip').distinct().count()
        cache.set(cache_key, count, CACHE_TTL_SHORT)
        return count

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.get_subjects_display()}"

    def get_subjects_display(self):
        # ⚡ ОПТИМИЗАЦИЯ: Используем select_related для избежания N+1
        subjects = self.teachersubject_set.select_related('subject').all()[:3]
        return ", ".join([ts.subject.name for ts in subjects])

    def get_min_price(self):
        """Получить минимальную цену (с кэшированием)"""
        cache_key = f'teacher_min_price_{self.id}'
        min_price = cache.get(cache_key)
        if min_price is not None:
            return min_price
        
        # ⚡ ОПТИМИЗАЦИЯ: Используем aggregate для более быстрого запроса
        min_price = self.teachersubject_set.aggregate(
            min_price=models.Min('hourly_rate')
        )['min_price']
        result = min_price or 0
        cache.set(cache_key, result, CACHE_TTL)
        return result

    def get_completeness(self):
        """Возвращает прогресс заполнения профиля учителя.

        Используется в teacher_profile.html для виджета «Профиль заполнен на N%»
        с конкретными подсказками что добавить для роста.

        Returns: dict {
            'percent': int 0-100,
            'missing': list of {'label', 'url_name', 'boost'},
            'completed': int,
            'total': int,
        }
        """
        checks = [
            {
                'done': bool(self.user.avatar),
                'label': 'Загрузить аватар',
                'boost': 25,  # %, +X запросов потенциально
                'url_name': 'teacher_profile_edit',
                'anchor': 'section-personal',
            },
            {
                'done': bool(self.bio and len(self.bio) >= 50),
                'label': 'Написать «О себе» (≥50 символов)',
                'boost': 30,
                'url_name': 'teacher_profile_edit',
                'anchor': 'section-professional',
            },
            {
                'done': bool(self.video_url),
                'label': 'Добавить видео-визитку',
                'boost': 40,
                'url_name': 'teacher_profile_edit',
                'anchor': 'section-video',
            },
            {
                'done': self.teachersubject_set.exists(),
                'label': 'Указать предметы и цены',
                'boost': 100,  # без этого нельзя бронировать
                'url_name': 'teacher_profile_edit',
                'anchor': 'section-subjects',
            },
            {
                'done': self.has_schedule(),
                'label': 'Задать расписание',
                'boost': 80,  # без этого нет слотов
                'url_name': 'teacher_calendar',
                'anchor': '',
            },
            {
                'done': bool(self.city_id),
                'label': 'Указать город',
                'boost': 15,
                'url_name': 'teacher_profile_edit',
                'anchor': 'section-format',
            },
            {
                'done': bool(self.university),
                'label': 'Добавить образование',
                'boost': 12,
                'url_name': 'teacher_profile_edit',
                'anchor': 'section-professional',
            },
            {
                'done': bool(self.user.phone),
                'label': 'Указать телефон',
                'boost': 10,
                'url_name': 'teacher_profile_edit',
                'anchor': 'section-personal',
            },
        ]
        total = len(checks)
        completed = sum(1 for c in checks if c['done'])
        percent = int(round(completed / total * 100)) if total else 0
        # Топ-3 missing, отсортированных по самому большому boost
        missing = sorted(
            [c for c in checks if not c['done']],
            key=lambda c: c['boost'],
            reverse=True,
        )[:3]
        return {
            'percent': percent,
            'completed': completed,
            'total': total,
            'missing': missing,
        }

    def calculate_match_score(self, student):
        """Возвращает совместимость учителя с конкретным студентом (0-100).

        Returns: dict {
            'score': int 0-100,
            'factors': list of {'icon', 'label', 'matched', 'weight'},
            'matched_subjects': list of Subject objects,
        }

        Логика:
          subjects (40): пересечение desired_subjects ∩ teacher.subjects
          budget   (20): teacher.min_price вмещается в [budget_min, budget_max]
          format   (15): совпадение online/offline/both
          city     (10): один город (для offline/both)
          rating   (10): rating × 2 (max 10), 5 нейтрально без отзывов
          featured ( 5): is_featured
        """
        score = 0
        factors = []
        matched_subjects = []

        # ─── Subjects (40) ─────────────────────────────────────────
        try:
            desired_ids = set(student.desired_subjects.values_list('id', flat=True))
            teacher_subject_ids = set(self.teachersubject_set.values_list('subject_id', flat=True))
            inter_ids = desired_ids & teacher_subject_ids
        except Exception:
            inter_ids = set()
        subjects_matched = bool(inter_ids)
        if subjects_matched:
            from .models import Subject as _Subject
            matched_subjects = list(_Subject.objects.filter(id__in=inter_ids).only('id', 'name'))
            score += 40
        factors.append({
            'icon': '📚',
            'label': (
                'Преподаёт ' + ', '.join(s.name for s in matched_subjects)
                if subjects_matched else 'Другие предметы'
            ),
            'matched': subjects_matched,
            'weight': 40,
        })

        # ─── Budget (20) ────────────────────────────────────────────
        try:
            min_price = self.get_min_price() or 0
        except Exception:
            min_price = 0
        budget_max = student.budget_max
        budget_min = student.budget_min
        budget_ok = True
        if budget_max and min_price > budget_max:
            budget_ok = False
        if budget_ok:
            score += 20
            if budget_max:
                budget_label = f'В вашем бюджете (до {int(budget_max):,} сум/час)'.replace(',', ' ')
            else:
                budget_label = 'Цена обсуждаема'
        else:
            budget_label = f'Дороже бюджета ({int(min_price):,} сум/час)'.replace(',', ' ')
        factors.append({
            'icon': '💰',
            'label': budget_label,
            'matched': budget_ok,
            'weight': 20,
        })

        # ─── Format (15) ────────────────────────────────────────────
        sf = (student.learning_format or 'both')
        tf = (self.teaching_format or 'both')
        format_match = (
            sf == 'both' or tf == 'both' or sf == tf
        )
        if format_match:
            score += 15
        fmap = {'online': 'Онлайн', 'offline': 'Офлайн', 'both': 'Любой формат'}
        factors.append({
            'icon': '🏠' if tf == 'online' else ('📍' if tf == 'offline' else '🌐'),
            'label': f'{fmap.get(tf, tf)}' + (' — как вы хотите' if format_match else ''),
            'matched': format_match,
            'weight': 15,
        })

        # ─── City (10) ──────────────────────────────────────────────
        wants_offline = sf in ('offline', 'both')
        city_match = bool(
            wants_offline and self.city_id and student.city_id
            and self.city_id == student.city_id
        )
        if city_match:
            score += 10
            factors.append({
                'icon': '🏙️',
                'label': f'В вашем городе ({self.city.name})',
                'matched': True,
                'weight': 10,
            })

        # ─── Rating (10) ────────────────────────────────────────────
        rating_val = float(self.rating or 0)
        if self.total_reviews > 0:
            r_pts = min(10, int(round(rating_val * 2)))
            score += r_pts
            if rating_val >= 4.5:
                factors.append({
                    'icon': '⭐',
                    'label': f'Высокий рейтинг {rating_val:.1f} ({self.total_reviews} отзывов)',
                    'matched': True,
                    'weight': 10,
                })

        # ─── Featured (5) ──────────────────────────────────────────
        if self.is_featured:
            score += 5
            factors.append({
                'icon': '✨',
                'label': 'Рекомендованный учитель',
                'matched': True,
                'weight': 5,
            })

        score = max(0, min(100, score))
        return {
            'score': score,
            'factors': factors,
            'matched_subjects': matched_subjects,
        }

    @classmethod
    def get_smart_matches(cls, student, limit=5):
        """Топ-N учителей для студента, отсортированных по match score.

        Returns: list of dicts:
            [{'teacher': TeacherProfile, 'score': int, 'factors': [...], 'matched_subjects': [...]}]

        Логика отбора:
          1. Кандидаты — активные approved учителя, у которых есть хотя бы
             один совпадающий предмет с desired_subjects студента.
          2. Если у студента нет desired_subjects — fallback: топ-учителя
             по рейтингу и featured (без factors).
          3. Считаем match_score для каждого кандидата, сортируем по score desc.
        """
        try:
            desired_ids = list(student.desired_subjects.values_list('id', flat=True))
        except Exception:
            desired_ids = []

        candidates = cls.objects.filter(
            is_active=True,
            moderation_status='approved',
        ).select_related('user', 'city').prefetch_related('teachersubject_set__subject')

        if desired_ids:
            candidates = candidates.filter(subjects__id__in=desired_ids).distinct()
        else:
            # fallback: показать топовых независимо
            top = list(candidates.order_by('-is_featured', '-rating', '-total_students')[:limit])
            return [
                {'teacher': t, 'score': None, 'factors': [], 'matched_subjects': []}
                for t in top
            ]

        # Берём с запасом (для разнообразия отсева), считаем score и сортируем
        pool = list(candidates[:60])
        scored = []
        for t in pool:
            data = t.calculate_match_score(student)
            data['teacher'] = t
            scored.append(data)
        scored.sort(key=lambda d: (-d['score'], -float(d['teacher'].rating or 0)))
        return scored[:limit]

    # ────────────────────────────────────────────────────────────────────
    # Phase 9 — Teacher Activity Dashboard
    # ────────────────────────────────────────────────────────────────────
    @staticmethod
    def _period_to_since(period):
        """'7d'/'30d'/'all' → datetime since (или None для 'all')."""
        if period == '7d':
            return timezone.now() - timedelta(days=7)
        if period == '30d':
            return timezone.now() - timedelta(days=30)
        if period == '24h':
            return timezone.now() - timedelta(hours=24)
        return None  # all time

    @staticmethod
    def _period_label(period):
        return {
            '24h': 'за 24 часа',
            '7d': 'за 7 дней',
            '30d': 'за 30 дней',
            'all': 'за всё время',
        }.get(period, period)

    def get_activity_stats(self, period='7d'):
        """Активность учителя за период (views, viewers, conversations, bookings).

        Используется в teacher_profile.html для дашборда «свой профиль».
        Кэшируется на 1 минуту — данные часто меняются.
        """
        cache_key = f'teacher_activity_{self.id}_{period}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        since = self._period_to_since(period)
        # Views
        views_qs = self.profile_views.all()
        if since:
            views_qs = views_qs.filter(viewed_at__gte=since)
        views_total = views_qs.aggregate(t=models.Sum('views_count'))['t'] or 0
        viewers_unique = views_qs.values('viewer_user_id', 'viewer_ip').distinct().count()

        # Conversations (учитель — получатель сообщений → conversations.teacher = self)
        conv_qs = self.conversations.all()
        if since:
            conv_qs = conv_qs.filter(created_at__gte=since)
        conversations_count = conv_qs.count()

        # Bookings (через slot)
        from .models import Booking as _Booking
        bookings_qs = _Booking.objects.filter(slot__teacher=self)
        if since:
            bookings_qs = bookings_qs.filter(created_at__gte=since)
        bookings_count = bookings_qs.count()

        # Completed bookings
        completed_qs = _Booking.objects.filter(
            slot__teacher=self, status='completed',
        )
        if since:
            completed_qs = completed_qs.filter(ended_at__gte=since)
        completed_count = completed_qs.count()

        # Pending — текущий snapshot (для tile «нужно подтвердить»)
        pending_count = _Booking.objects.filter(
            slot__teacher=self, status='pending',
        ).count()

        stats = {
            'views': views_total,
            'viewers': viewers_unique,
            'conversations': conversations_count,
            'bookings': bookings_count,
            'completed': completed_count,
            'pending': pending_count,
            'period': period,
            'period_label': self._period_label(period),
        }
        cache.set(cache_key, stats, CACHE_TTL_SHORT)
        return stats

    def get_funnel_stats(self, period='7d'):
        """Воронка views → conversations → bookings → completed с % конверсии.

        Каждый шаг — {label, count, rate_from_prev_pct, icon}.
        Cache key — отдельный от get_activity_stats для гранулярной инвалидации.
        """
        s = self.get_activity_stats(period)

        def _rate(num, den):
            if not den:
                return None
            return round(100 * num / den, 1)

        steps = [
            {'key': 'views',         'label': 'Просмотры',      'icon_class': 'fa-solid fa-eye',            'count': s['views'],         'rate': None},
            {'key': 'conversations', 'label': 'Беседы',         'icon_class': 'fa-solid fa-comment-dots',   'count': s['conversations'], 'rate': _rate(s['conversations'], s['views'])},
            {'key': 'bookings',      'label': 'Бронирования',   'icon_class': 'fa-solid fa-calendar-check', 'count': s['bookings'],      'rate': _rate(s['bookings'], s['conversations'] or s['views'])},
            {'key': 'completed',     'label': 'Проведено',      'icon_class': 'fa-solid fa-circle-check',   'count': s['completed'],     'rate': _rate(s['completed'], s['bookings'])},
        ]
        return {
            'steps': steps,
            'period': period,
            'period_label': self._period_label(period),
        }

    def get_earnings_stats(self, period='30d'):
        """Заработок за период: completed bookings × hourly_rate + trial_price_paid.

        Считаем только completed (не pending/confirmed) — это то, что точно заработано.
        Использует hourly_rate из TeacherSubject (на текущий момент) — упрощение,
        для точных финансов нужен снапшот цены в Booking. Это достаточно для дашборда.
        """
        cache_key = f'teacher_earnings_{self.id}_{period}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        since = self._period_to_since(period)
        from .models import Booking as _Booking
        qs = _Booking.objects.filter(
            slot__teacher=self, status='completed',
        ).select_related('slot', 'subject')
        if since:
            qs = qs.filter(ended_at__gte=since)

        # Карта subject_id → hourly_rate учителя
        rates = dict(self.teachersubject_set.values_list('subject_id', 'hourly_rate'))
        # Fallback для booking без subject — берём минимальную цену учителя
        fallback_rate = min(rates.values()) if rates else None

        total = 0
        lessons = 0
        trial_revenue = 0
        for b in qs:
            lessons += 1
            if b.trial_price_paid:
                trial_revenue += float(b.trial_price_paid)
                continue
            if b.is_trial:
                continue  # бесплатный пробный — 0
            rate = rates.get(b.subject_id) if b.subject_id else fallback_rate
            if rate is not None:
                total += float(rate)
        gross = total + trial_revenue

        result = {
            'gross': int(gross),
            'lessons': lessons,
            'avg_per_lesson': int(gross / lessons) if lessons else 0,
            'period': period,
            'period_label': self._period_label(period),
        }
        cache.set(cache_key, result, CACHE_TTL_SHORT)
        return result

    def get_first_booking_checklist(self):
        """Чек-лист для учителя без completed bookings — «5 шагов до первой брони».

        Возвращает list of {key, label, done, hint, action_anchor}.
        """
        from .models import Booking as _Booking
        has_completed = _Booking.objects.filter(
            slot__teacher=self, status='completed',
        ).exists()
        if has_completed:
            return None  # уже не нужен

        return [
            {
                'key': 'avatar',
                'label': 'Загрузить фото профиля',
                'done': bool(self.user.avatar),
                'hint': 'Профили с фото получают в 3 раза больше просмотров',
                'action_anchor': '#section-personal',
            },
            {
                'key': 'bio',
                'label': 'Написать «О себе»',
                'done': bool(self.bio and len(self.bio) >= 50),
                'hint': 'Минимум 50 символов — расскажите о подходе',
                'action_anchor': '#section-professional',
            },
            {
                'key': 'video',
                'label': 'Записать видео-визитку (1-2 мин)',
                'done': bool(self.video_url),
                'hint': 'Учителя с видео получают в 5 раз больше бронирований',
                'action_anchor': '#section-video',
            },
            {
                'key': 'subjects',
                'label': 'Указать предметы и цены',
                'done': self.teachersubject_set.exists(),
                'hint': 'Без предметов вас невозможно забронировать',
                'action_anchor': '#section-subjects',
            },
            {
                'key': 'schedule',
                'label': 'Задать расписание',
                'done': self.has_schedule(),
                'hint': 'Слоты бронируются только из расписания',
                'action_anchor': '',  # ведёт на teacher_calendar
            },
        ]

    def get_trial_subject(self):
        """Возвращает TeacherSubject с trial-уроком (приоритет: free → paid), либо None.

        Используется в teacher_detail.html для подсветки trial-CTA в booking-sidebar.
        Кэшируется на 5 минут.
        """
        cache_key = f'teacher_trial_subject_{self.id}'
        cached = cache.get(cache_key)
        if cached is not None:
            return cached if cached != 'NONE' else None

        qs = self.teachersubject_set.select_related('subject')
        ts = qs.filter(is_free_trial=True).order_by('hourly_rate').first()
        if ts is None:
            ts = qs.filter(trial_price__isnull=False).order_by('trial_price').first()

        cache.set(cache_key, ts if ts is not None else 'NONE', CACHE_TTL)
        return ts

    def get_available_weekdays_display(self):
        days = self.available_weekdays.split(',')
        return ', '.join([WEEKDAYS_MAP.get(day.strip(), day) for day in days])

    # Порядок дней недели для отображения и нормализации расписания
    WEEKDAYS_ORDERED = [
        ('monday', _('Понедельник')), ('tuesday', _('Вторник')), ('wednesday', _('Среда')),
        ('thursday', _('Четверг')), ('friday', _('Пятница')), ('saturday', _('Суббота')),
        ('sunday', _('Воскресенье')),
    ]

    def get_schedule_intervals(self):
        """Нормализует weekly_schedule в {day_key: [(from, to), ...]}.

        Поддерживает оба формата хранения:
          новый: {"monday": [{"from": "09:00", "to": "12:00"}, ...]}
          старый: {"monday": {"from": "09:00", "to": "18:00"}}
        """
        raw = self.weekly_schedule or {}
        out = {}
        for key, _label in self.WEEKDAYS_ORDERED:
            day = raw.get(key)
            intervals = []
            if isinstance(day, dict):
                if day.get('from') and day.get('to'):
                    intervals.append((day['from'], day['to']))
            elif isinstance(day, list):
                for itv in day:
                    if isinstance(itv, dict) and itv.get('from') and itv.get('to'):
                        intervals.append((itv['from'], itv['to']))
            out[key] = intervals
        return out

    WEEKDAYS_SHORT = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

    def get_schedule_display(self):
        """[(day_label, [(from, to), ...]), ...] по всем 7 дням — для шаблонов.
        Пустой список интервалов = выходной день."""
        intervals = self.get_schedule_intervals()
        return [(label, intervals[key]) for key, label in self.WEEKDAYS_ORDERED]

    def get_schedule_display_short(self):
        """То же, что get_schedule_display, но с короткими именами дней (Пн, Вт, ...)."""
        intervals = self.get_schedule_intervals()
        return [
            (self.WEEKDAYS_SHORT[i], intervals[key])
            for i, (key, _label) in enumerate(self.WEEKDAYS_ORDERED)
        ]

    def has_schedule(self):
        """True, если задан хотя бы один рабочий интервал."""
        return any(self.get_schedule_intervals().values())

    def generate_slots_from_template(self, weeks: int = 4, slot_minutes: int = 60,
                                     start_date=None) -> dict:
        """Нарезает TimeSlot из шаблона weekly_schedule на N недель вперёд.

        Используется и после регистрации (авто-генерация), и через UI календаря.
        Пересекающиеся / прошедшие слоты пропускаются.

        Возвращает: {'created': N, 'skipped': M, 'total': K}.
        """
        from datetime import datetime, time as dt_time, timedelta
        from django.utils import timezone

        if weeks < 1:
            weeks = 1
        if weeks > 12:
            weeks = 12
        if slot_minutes not in (30, 45, 60, 90, 120):
            slot_minutes = 60

        schedule = self.get_schedule_intervals()
        if not any(schedule.values()):
            return {'created': 0, 'skipped': 0, 'total': 0}

        tz = timezone.get_current_timezone()
        now = timezone.now()
        if start_date is None:
            start_date = (now + timedelta(days=1)).date()
        end_date = start_date + timedelta(weeks=weeks)

        existing = list(TimeSlot.objects.filter(
            teacher=self,
            start_at__gte=timezone.make_aware(datetime.combine(start_date, dt_time(0, 0)), tz),
            start_at__lt=timezone.make_aware(datetime.combine(end_date, dt_time(0, 0)), tz),
        ).values_list('start_at', 'end_at'))

        def overlaps(s, e):
            for es, ee in existing:
                if s < ee and es < e:
                    return True
            return False

        weekday_map = {
            'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
            'friday': 4, 'saturday': 5, 'sunday': 6,
        }
        created = skipped = total = 0
        current = start_date
        while current < end_date:
            day_key = current.strftime('%A').lower()
            for from_str, to_str in schedule.get(day_key, []):
                try:
                    from_t = dt_time.fromisoformat(from_str)
                    to_t = dt_time.fromisoformat(to_str)
                except (ValueError, TypeError):
                    continue
                day_start = timezone.make_aware(datetime.combine(current, from_t), tz)
                day_end = timezone.make_aware(datetime.combine(current, to_t), tz)
                cursor = day_start
                while cursor + timedelta(minutes=slot_minutes) <= day_end:
                    slot_end = cursor + timedelta(minutes=slot_minutes)
                    total += 1
                    if cursor < now or overlaps(cursor, slot_end):
                        skipped += 1
                    else:
                        TimeSlot.objects.create(
                            teacher=self, start_at=cursor, end_at=slot_end, status='free',
                        )
                        existing.append((cursor, slot_end))
                        created += 1
                    cursor = slot_end
            current += timedelta(days=1)
        return {'created': created, 'skipped': skipped, 'total': total}

    def get_absolute_url(self):
        return reverse('teacher_detail', kwargs={'pk': self.pk})
    
    def _rebuild_search_text(self):
        """Пересобирает search_text из связанных полей."""
        user = getattr(self, 'user', None)
        first_name = getattr(user, 'first_name', '') if user else ''
        last_name = getattr(user, 'last_name', '') if user else ''
        self.search_text = normalize_search_text(
            first_name, last_name, self.bio, self.university, self.specialization,
        )

    def save(self, *args, **kwargs):
        """Переопределяем save для автоматического создания уведомлений и инвалидации кэша"""
        # Пересобираем search_text перед каждым save — поля могли измениться
        self._rebuild_search_text()

        # Проверяем, меняется ли статус модерации
        if self.pk:
            try:
                old_instance = TeacherProfile.objects.get(pk=self.pk)
                old_status = old_instance.moderation_status
                new_status = self.moderation_status

                if old_status != new_status:
                    super().save(*args, **kwargs)
                    self.clear_cache()

                    # Создаём уведомление ПОСЛЕ сохранения
                    moderator = self.moderated_by or User.objects.filter(is_staff=True).first()
                    if moderator:
                        if new_status == 'approved' and old_status != 'approved':
                            self._create_approval_notification(moderator, self.moderation_comment or '')
                        elif new_status == 'rejected' and old_status != 'rejected':
                            self._create_rejection_notification(moderator, self.moderation_comment or '')
                    else:
                        logger.warning(f"No moderator found for notification (teacher: {self.user.username})")

                    return

            except TeacherProfile.DoesNotExist:
                pass

        super().save(*args, **kwargs)
        self.clear_cache()
    
    def clear_cache(self):
        """Очистить весь кэш связанный с этим профилем учителя"""
        for period in ['all', 'day', 'week', 'month']:
            cache.delete(f'teacher_views_{self.id}_{period}')
            cache.delete(f'teacher_unique_views_{self.id}_{period}')
        cache.delete(f'teacher_min_price_{self.id}')
    

class TeacherSubject(models.Model):
    """Промежуточная модель для связи учитель-предмет с ценой"""

    TRIAL_DURATION_CHOICES = [
        (30, _('30 минут')),
        (60, _('60 минут')),
    ]

    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    hourly_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text=_("Цена за час в сумах")
    )
    is_free_trial = models.BooleanField(default=True, help_text=_("Бесплатное пробное занятие"))
    trial_duration_minutes = models.PositiveSmallIntegerField(
        choices=TRIAL_DURATION_CHOICES,
        default=60,
        help_text=_("Длительность пробного урока (30 или 60 минут)"),
    )
    trial_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Цена платного пробного урока в сумах (не указывается, если пробный бесплатный)"),
    )
    description = models.TextField(blank=True, help_text=_("Дополнительная информация по предмету"))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['teacher', 'subject']
        verbose_name = _('Предмет учителя')
        verbose_name_plural = _('Предметы учителей')
        # ⚡ ОПТИМИЗАЦИЯ: Индексы для фильтрации по цене
        indexes = [
            models.Index(fields=['teacher', 'hourly_rate']),  # Для фильтра по цене
            models.Index(fields=['hourly_rate']),  # Диапазон цены по всем учителям (marketplace-фильтр)
            models.Index(fields=['subject']),  # Для фильтра по предмету
        ]

    def __str__(self):
        return f"{self.teacher.user.get_full_name()} - {self.subject.name} ({self.hourly_rate} сум/час)"
    
    def save(self, *args, **kwargs):
        """Переопределяем save для инвалидации кэша"""
        super().save(*args, **kwargs)
        # Инвалидируем кэш минимальной цены учителя
        cache.delete(f'teacher_min_price_{self.teacher.id}')
        # Инвалидируем кэш количества учителей для предмета
        cache.delete(f'subject_teachers_count_{self.subject.id}')
    
    def delete(self, *args, **kwargs):
        """Переопределяем delete для инвалидации кэша"""
        teacher_id = self.teacher.id
        subject_id = self.subject.id
        super().delete(*args, **kwargs)
        # Инвалидируем кэш после удаления
        cache.delete(f'teacher_min_price_{teacher_id}')
        cache.delete(f'subject_teachers_count_{subject_id}')

class StudentProfile(models.Model):
    """Профиль ученика"""
    EDUCATION_LEVELS = [
        ('elementary', _('Начальная школа (1-4 класс)')),
        ('middle', _('Средняя школа (5-9 класс)')),
        ('high', _('Старшая школа (10-11 класс)')),
        ('university', _('Университет')),
        ('adult', _('Взрослый')),
    ]

    LEARNING_FORMATS = [
        ('online', _('Онлайн')),
        ('offline', _('Офлайн')),
        ('both', _('Онлайн и офлайн')),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='student_profile')
    education_level = models.CharField(max_length=20, choices=EDUCATION_LEVELS, blank=True)
    school_university = models.CharField(max_length=200, blank=True)
    city = models.ForeignKey(City, on_delete=models.SET_NULL, null=True, blank=True)
    
    interests = models.ManyToManyField(
        Subject, 
        blank=True, 
        related_name='interested_students',
        help_text="Интересующие предметы"
    )
    
    desired_subjects = models.ManyToManyField(
        Subject,
        blank=True,
        related_name='learning_students',
        help_text="Предметы для изучения"
    )
    
    bio = models.TextField(max_length=500, blank=True, verbose_name=_("Краткое описание"))

    description = models.TextField(
        max_length=1000,
        blank=True,
        verbose_name=_("Описание целей и пожеланий"),
        help_text=_("Расскажите о своих целях обучения, уровне подготовки и ожиданиях")
    )
    
    budget_min = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name=_("Минимальный бюджет (сум/час)"),
        help_text=_("Минимальная цена, которую готов платить")
    )
    
    budget_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name=_("Максимальный бюджет (сум/час)"),
        help_text=_("Максимальная цена, которую готов платить")
    )
    
    learning_format = models.CharField(
        max_length=10,
        choices=LEARNING_FORMATS,
        default='both',
        verbose_name=_("Предпочитаемый формат обучения")
    )

    # ✅ НОВЫЕ ПОЛЯ: Контакты для связи
    telegram = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Telegram",
        help_text=_("Ваш Telegram username (@username) или номер телефона")
    )

    whatsapp = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="WhatsApp",
        help_text=_("Номер WhatsApp для связи (+998 90 123 45 67)")
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name=_("Активный профиль"),
        help_text=_("Ищет ли ученик учителя в данный момент")
    )

    available_weekdays = models.CharField(
        max_length=20,
        default='1,2,3,4,5,6,7',
        blank=True,
        verbose_name=_("Доступные дни недели"),
        help_text=_("Дни недели через запятую (1-7)")
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Профиль ученика')
        verbose_name_plural = _('Профили учеников')
        ordering = ['-created_at']
        # ⚡ ОПТИМИЗАЦИЯ: Индексы для ускорения поиска и фильтрации
        indexes = [
            models.Index(fields=['is_active', '-created_at']),  # Для списка учеников
            models.Index(fields=['city', 'is_active']),  # Для фильтра по городу
            models.Index(fields=['learning_format']),  # Для фильтра формата
            models.Index(fields=['education_level']),  # Для фильтра уровня образования
            models.Index(fields=['budget_min', 'budget_max']),  # Для фильтра бюджета
        ]

    def get_desired_subjects_display(self):
        """Возвращает строку с названиями желаемых предметов"""
        # ⚡ ОПТИМИЗАЦИЯ: Используем only для загрузки только нужных полей
        subjects = self.desired_subjects.only('name')[:3]
        if subjects:
            return ", ".join([s.name for s in subjects])
        return "Не указано"
    
    def get_budget_display(self):
        """Возвращает строку с бюджетом"""
        from django.utils.translation import gettext as _
        if self.budget_min and self.budget_max:
            return f"{self.budget_min:,.0f} - {self.budget_max:,.0f} {_('сум/час')}"
        elif self.budget_max:
            return f"{_('До')} {self.budget_max:,.0f} {_('сум/час')}"
        elif self.budget_min:
            return f"{_('От')} {self.budget_min:,.0f} {_('сум/час')}"
        return _("Договорная")
    
    def get_available_weekdays_display(self):
        """Возвращает строку с днями недели"""
        if self.available_weekdays:
            days = self.available_weekdays.split(',')
            return ', '.join([WEEKDAYS_MAP.get(day.strip(), day) for day in days])
        return "Не указано"

    def get_views_count(self, period='all'):
        """Количество просмотров (сумма views_count после дедупа), с кэшем."""
        cache_key = f'student_views_{self.id}_{period}'
        count = cache.get(cache_key)
        if count is not None:
            return count
        qs = _filter_views_by_period(self.profile_views.all(), period)
        count = qs.aggregate(total=models.Sum('views_count'))['total'] or 0
        cache.set(cache_key, count, CACHE_TTL_SHORT)
        return count

    def get_unique_viewers_count(self, period='all'):
        """Уникальные зрители (по viewer_user/IP)."""
        cache_key = f'student_unique_views_{self.id}_{period}'
        count = cache.get(cache_key)
        if count is not None:
            return count
        qs = _filter_views_by_period(self.profile_views.all(), period)
        count = qs.values('viewer_user_id', 'viewer_ip').distinct().count()
        cache.set(cache_key, count, CACHE_TTL_SHORT)
        return count

    def __str__(self):
        return f"{self.user.get_full_name()} - Ученик"
    
    def save(self, *args, **kwargs):
        """Переопределяем save для инвалидации кэша"""
        super().save(*args, **kwargs)
        self.clear_cache()

    def clear_cache(self):
        """Очистить весь кэш связанный с этим профилем ученика"""
        for period in ['all', 'day', 'week', 'month']:
            cache.delete(f'student_views_{self.id}_{period}')
            cache.delete(f'student_unique_views_{self.id}_{period}')

class Conversation(models.Model):
    """Модель переписки между учителем и учеником"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name='conversations')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversations')
    subject = models.ForeignKey(Subject, on_delete=models.SET_NULL, null=True, blank=True)
    
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['teacher', 'student']
        ordering = ['-updated_at']
        verbose_name = _('Переписка')
        verbose_name_plural = _('Переписки')

    def __str__(self):
        return f"Переписка: {self.student.get_full_name()} - {self.teacher.user.get_full_name()}"

    def get_last_message(self):
        """Получить последнее сообщение (возвращает None если сообщений нет)"""
        return self.messages.order_by('-created_at').first()
    
    def get_unread_count(self, user):
        """Получить количество непрочитанных сообщений для пользователя"""
        return self.messages.filter(is_read=False).exclude(sender=user).count()

class Message(models.Model):
    """Модель сообщения"""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    content = models.TextField(max_length=2000)

    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = _('Сообщение')
        verbose_name_plural = _('Сообщения')
        indexes = [
            models.Index(fields=['conversation', '-created_at']),
            models.Index(fields=['sender', '-created_at']),
            models.Index(fields=['is_read']),
        ]

    def __str__(self):
        return f"{self.sender.get_full_name()}: {self.content[:50]}..."

    def mark_as_read(self):
        """Пометить сообщение как прочитанное"""
        if not self.is_read:
            self.is_read = True
            self.read_at = timezone.now()
            self.save(update_fields=['is_read', 'read_at'])

class Review(models.Model):
    """Отзывы о учителях"""
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name='reviews')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='given_reviews')
    subject = models.ForeignKey(Subject, on_delete=models.SET_NULL, null=True)

    # Бронь, по итогам которой оставлен отзыв (если отзыв «проверенный»).
    # OneToOne: одна завершённая бронь → максимум один отзыв.
    booking = models.OneToOneField(
        'Booking', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='review',
        help_text=_("Урок, после которого оставлен отзыв"),
    )

    rating = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text=_("Оценка от 1 до 5")
    )
    comment = models.TextField(max_length=1000, blank=True)

    # Детальные оценки
    knowledge_rating = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    communication_rating = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    punctuality_rating = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])

    is_verified = models.BooleanField(default=False, help_text=_("Проверенный отзыв (был реальный урок)"))
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        # Уникальность через OneToOne(booking) — один Review на один Booking.
        # Старый unique_together(teacher,student,subject) убран: для подписки
        # с 8 уроками ученик может оставить 8 отдельных отзывов (по одному на урок).
        ordering = ['-created_at']
        verbose_name = _('Отзыв')
        verbose_name_plural = _('Отзывы')
        indexes = [
            models.Index(fields=['teacher', '-created_at']),
            models.Index(fields=['student', '-created_at']),
        ]

    def __str__(self):
        return f"Отзыв от {self.student.get_full_name()} для {self.teacher.user.get_full_name()}"

class Favorite(models.Model):
    """Избранные учителя"""
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites')
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name='favorited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['student', 'teacher']
        verbose_name = _('Избранный учитель')
        verbose_name_plural = _('Избранные учителя')

    def __str__(self):
        return f"{self.student.get_full_name()} -> {self.teacher.user.get_full_name()}"


class FavoriteStudent(models.Model):
    """Избранные ученики у учителя"""
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name='favorite_students')
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='favorited_by_teachers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['teacher', 'student']
        verbose_name = _('Избранный ученик')
        verbose_name_plural = _('Избранные ученики')

    def __str__(self):
        return f"{self.teacher.user.get_full_name()} -> {self.student.user.get_full_name()}"


class LeadOptOut(models.Model):
    """Ученик сказал учителю «не интересно».

    Контроль на стороне спроса: даже если ученик остаётся в избранном или
    бронировал пробный, наличие записи здесь лишает учителя права писать
    ПЕРВЫМ и убирает ученика из раздела «Потенциальные ученики» этого учителя.
    На уже открытую переписку не влияет (для жёсткой блокировки — отдельный механизм).
    """
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name='lead_opt_outs',
        verbose_name=_('Ученик'),
    )
    teacher = models.ForeignKey(
        TeacherProfile, on_delete=models.CASCADE, related_name='lead_opt_outs',
        verbose_name=_('Учитель'),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['student', 'teacher']
        verbose_name = _('Отказ ученика от лида')
        verbose_name_plural = _('Отказы учеников от лидов')
        indexes = [
            models.Index(fields=['teacher', 'student']),
        ]

    def __str__(self):
        return f"{self.student.get_full_name()} ✕ {self.teacher.user.get_full_name()}"

class TelegramUser(models.Model):
    """Модель для хранения Telegram-пользователей"""
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='telegram_user',
        null=True,
        blank=True,
        verbose_name=_('Связанный пользователь')
    )
    telegram_id = models.BigIntegerField(
        unique=True,
        verbose_name=_('Telegram ID'),
        help_text=_('Уникальный ID пользователя в Telegram')
    )
    telegram_username = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name=_('Username в Telegram')
    )
    first_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('Имя в Telegram')
    )
    last_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name=_('Фамилия в Telegram')
    )
    language_code = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        verbose_name=_('Язык интерфейса')
    )

    # Настройки уведомлений
    notifications_enabled = models.BooleanField(
        default=True,
        verbose_name=_('Уведомления включены')
    )

    # Статистика
    started_bot = models.BooleanField(
        default=False,
        verbose_name=_('Нажал Start в боте')
    )
    last_interaction = models.DateTimeField(
        auto_now=True,
        verbose_name=_('Последнее взаимодействие')
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Дата регистрации в боте')
    )

    class Meta:
        verbose_name = _('Telegram пользователь')
        verbose_name_plural = _('Telegram пользователи')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['telegram_id']),
            models.Index(fields=['telegram_username']),
        ]
    
    def __str__(self):
        if self.user:
            return f"@{self.telegram_username or self.telegram_id} ({self.user.get_full_name()})"
        return f"@{self.telegram_username or self.telegram_id} (Не привязан)"


class ProfileView(models.Model):
    """
    Модель для отслеживания просмотров профилей
    Записывает каждый просмотр профиля учителя или ученика
    """
    PROFILE_TYPES = [
        ('teacher', _('Профиль учителя')),
        ('student', _('Профиль ученика')),
    ]

    # Общие поля
    profile_type = models.CharField(max_length=10, choices=PROFILE_TYPES, verbose_name=_('Тип профиля'))
    viewer_ip = models.GenericIPAddressField(verbose_name=_('IP адрес просмотревшего'), null=True, blank=True)
    viewer_user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='profile_views_made',
        verbose_name=_('Пользователь (если авторизован)')
    )
    viewed_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Дата и время просмотра'))

    # Связи с профилями
    teacher_profile = models.ForeignKey(
        TeacherProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='profile_views',
        verbose_name=_('Профиль учителя')
    )
    student_profile = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='profile_views',
        verbose_name=_('Профиль ученика')
    )

    # Дата просмотра (без времени) — для дедупликации.
    # На один (профиль, viewer, день) создаётся ровно одна запись,
    # повторные просмотры инкрементируют views_count.
    viewed_date = models.DateField(
        default=timezone.now,
        db_index=True,
        verbose_name=_('Дата просмотра')
    )
    views_count = models.PositiveIntegerField(
        default=1,
        verbose_name=_('Количество просмотров в этот день')
    )
    last_viewed_at = models.DateTimeField(
        default=timezone.now,
        verbose_name=_('Время последнего просмотра в этот день')
    )

    # Дополнительная информация
    user_agent = models.TextField(blank=True, verbose_name=_('User Agent браузера'))

    class Meta:
        verbose_name = _('Просмотр профиля')
        verbose_name_plural = _('Просмотры профилей')
        ordering = ['-viewed_at']
        indexes = [
            models.Index(fields=['-viewed_at']),
            models.Index(fields=['teacher_profile', '-viewed_at']),
            models.Index(fields=['student_profile', '-viewed_at']),
            models.Index(fields=['viewer_ip', '-viewed_at']),
            models.Index(fields=['teacher_profile', '-viewed_date']),
            models.Index(fields=['student_profile', '-viewed_date']),
        ]
    
    def __str__(self):
        if self.profile_type == 'teacher' and self.teacher_profile:
            profile_name = self.teacher_profile.user.get_full_name()
        elif self.profile_type == 'student' and self.student_profile:
            profile_name = self.student_profile.user.get_full_name()
        else:
            profile_name = "Неизвестный профиль"
        
        viewer_name = self.viewer_user.get_full_name() if self.viewer_user else f"Гость ({self.viewer_ip})"
        return f"{viewer_name} просмотрел профиль {profile_name} ({self.viewed_at.strftime('%d.%m.%Y %H:%M')})"
    
    def save(self, *args, **kwargs):
        # Автоматически устанавливаем тип профиля
        if self.teacher_profile:
            self.profile_type = 'teacher'
        elif self.student_profile:
            self.profile_type = 'student'
        super().save(*args, **kwargs)
        
        # Инвалидируем кэш статистики просмотров
        if self.teacher_profile:
            self.teacher_profile.clear_cache()
        elif self.student_profile:
            self.student_profile.clear_cache()


class NotificationQueue(models.Model):
    """
    Очередь уведомлений для Telegram
    Обеспечивает надёжную доставку с повторными попытками
    """
    STATUS_CHOICES = [
        ('pending', _('Ожидает отправки')),
        ('processing', _('В обработке')),
        ('sent', _('Отправлено')),
        ('failed', _('Ошибка')),
        ('cancelled', _('Отменено')),
    ]

    NOTIFICATION_TYPES = [
        ('new_message', _('Новое сообщение')),
        ('new_review', _('Новый отзыв')),
        ('profile_view', _('Просмотр профиля')),
        ('system', _('Системное уведомление')),
        ('broadcast', _('Массовая рассылка')),
    ]
    
    # Основные поля
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='telegram_notifications',
        verbose_name=_('Получатель')
    )
    notification_type = models.CharField(
        max_length=20,
        choices=NOTIFICATION_TYPES,
        default='new_message',
        verbose_name=_('Тип уведомления')
    )

    # Содержимое
    title = models.CharField(max_length=200, verbose_name=_('Заголовок'))
    message = models.TextField(verbose_name=_('Текст сообщения'))
    data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('Дополнительные данные'),
        help_text=_('JSON с доп. информацией (sender_id, conversation_id, url и т.д.)')
    )

    # Статус обработки
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True,
        verbose_name=_('Статус')
    )
    retry_count = models.PositiveIntegerField(
        default=0,
        verbose_name=_('Количество попыток')
    )
    max_retries = models.PositiveIntegerField(
        default=5,
        verbose_name=_('Максимум попыток')
    )
    last_error = models.TextField(
        blank=True,
        verbose_name=_('Последняя ошибка')
    )

    # Временные метки
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name=_('Создано')
    )
    scheduled_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        verbose_name=_('Запланировано на'),
        help_text=_('Время когда уведомление должно быть отправлено')
    )
    processing_started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Начало обработки')
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_('Отправлено')
    )

    # Идемпотентность
    idempotency_key = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        verbose_name=_('Ключ идемпотентности'),
        help_text=_('Уникальный ключ для предотвращения дублирования')
    )

    class Meta:
        verbose_name = _('Уведомление в очереди')
        verbose_name_plural = _('Очередь уведомлений')
        ordering = ['scheduled_at', 'created_at']
        indexes = [
            models.Index(fields=['status', 'scheduled_at']),
            models.Index(fields=['recipient', 'status']),
            models.Index(fields=['notification_type', 'status']),
            models.Index(fields=['idempotency_key']),
        ]
    
    def __str__(self):
        return f"{self.get_notification_type_display()} для {self.recipient.get_full_name()} ({self.get_status_display()})"
    
    def can_retry(self):
        """Проверить можно ли повторить попытку отправки"""
        return self.retry_count < self.max_retries and self.status in ['pending', 'failed']
    
    def mark_as_processing(self):
        """Отметить как обрабатываемое"""
        self.status = 'processing'
        self.processing_started_at = timezone.now()
        self.save(update_fields=['status', 'processing_started_at'])
    
    def mark_as_sent(self):
        """Отметить как успешно отправленное"""
        self.status = 'sent'
        self.sent_at = timezone.now()
        self.save(update_fields=['status', 'sent_at'])
    
    def mark_as_failed(self, error_message: str):
        """Отметить как неуспешное"""
        self.status = 'failed'
        self.last_error = error_message
        self.retry_count += 1
        self.save(update_fields=['status', 'last_error', 'retry_count'])
    
    def calculate_next_retry_delay(self):
        """
        Рассчитать задержку до следующей попытки (экспоненциальная задержка)
        Returns: timedelta
        """
        # Экспоненциальная задержка: 2^retry_count минут
        delay_minutes = 2 ** self.retry_count
        return timedelta(minutes=min(delay_minutes, 60))  # Макс 1 час


class NotificationLog(models.Model):
    """
    Лог попыток отправки уведомлений
    Для аудита и отладки
    """
    STATUS_CHOICES = [
        ('success', _('Успешно')),
        ('error', _('Ошибка')),
        ('skipped', _('Пропущено')),
    ]

    notification = models.ForeignKey(
        NotificationQueue,
        on_delete=models.CASCADE,
        related_name='logs',
        verbose_name=_('Уведомление')
    )
    attempt_number = models.PositiveIntegerField(verbose_name=_('Номер попытки'))
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        verbose_name=_('Статус')
    )
    error_message = models.TextField(
        blank=True,
        verbose_name=_('Сообщение об ошибке')
    )
    telegram_message_id = models.BigIntegerField(
        null=True,
        blank=True,
        verbose_name=_('ID сообщения в Telegram')
    )
    response_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name=_('Данные ответа')
    )
    processing_time_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_('Время обработки (мс)')
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name=_('Время попытки')
    )

    class Meta:
        verbose_name = _('Лог уведомления')
        verbose_name_plural = _('Логи уведомлений')
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['notification', '-timestamp']),
            models.Index(fields=['status', '-timestamp']),
        ]
    
    def __str__(self):
        return f"Попытка #{self.attempt_number} для {self.notification.id} - {self.get_status_display()}"

class SubjectSearchLog(models.Model):
    """Логирование поисков предметов для аналитики"""
    query = models.CharField(
        max_length=200,
        verbose_name=_('Поисковый запрос'),
        db_index=True,
        blank=True,
        null=True
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subject_searches',
        verbose_name=_('Пользователь')
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name=_('IP адрес'))
    found_results_count = models.PositiveIntegerField(default=0, verbose_name=_('Найдено результатов'))
    selected_subject = models.ForeignKey(
        Subject,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name=_('Выбранный предмет')
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name=_('Дата поиска'))

    class Meta:
        verbose_name = _('Лог поиска предметов')
        verbose_name_plural = _('Логи поиска предметов')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['query', '-created_at']),
            models.Index(fields=['-created_at']),
        ]
    
    def __str__(self):
        return f'"{self.query}" - {self.created_at.strftime("%d.%m.%Y %H:%M")}'


class ViewCounter(models.Model):
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField()
    page = models.CharField(max_length=100)
    viewed_at = models.DateTimeField(auto_now_add=True)
    month = models.DateField()

    class Meta:
        unique_together = ('ip_address', 'user_agent', 'page', 'month')

    @classmethod
    def add_view(cls, request, page):
        current_month = timezone.now().date().replace(day=1)
        try:
            cls.objects.get_or_create(
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],  # Ограничиваем длину
                page=page[:100],  # Ограничиваем длину страницы
                month=current_month
            )
        except Exception as e:
            logger.warning(f"Failed to record view for page {page}: {e}")

    @classmethod
    def get_monthly_stats(cls):
        current_month = timezone.now().date().replace(day=1)
        return cls.objects.filter(month=current_month).count()



# ============================================
# СИСТЕМА УВЕДОМЛЕНИЙ
# ============================================

class Notification(models.Model):
    """
    Модель уведомлений для пользователей
    Управляется через Django Admin
    """
    TARGET_CHOICES = [
        ('all', _('Все пользователи')),
        ('students', _('Только ученики')),
        ('teachers', _('Только учителя')),
        ('admins', _('Только администраторы')),
        ('specific_user', _('Конкретный пользователь')),
    ]

    title = models.CharField(
        max_length=200,
        verbose_name=_('Заголовок'),
        help_text=_('Краткий заголовок уведомления')
    )

    short_text = models.CharField(
        max_length=300,
        verbose_name=_('Краткий текст'),
        help_text=_('Текст для отображения в списке уведомлений')
    )

    full_text = models.TextField(
        verbose_name=_('Полный текст'),
        help_text=_('Подробное описание уведомления')
    )

    image = models.ImageField(
        upload_to='notifications/',
        blank=True,
        null=True,
        verbose_name=_('Изображение'),
        help_text=_('Опциональное изображение к уведомлению')
    )

    action_url = models.URLField(
        blank=True,
        null=True,
        verbose_name=_('Ссылка действия'),
        help_text=_('URL для перехода при клике (опционально)')
    )

    target = models.CharField(
        max_length=20,
        choices=TARGET_CHOICES,
        default='all',
        verbose_name=_('Целевая аудитория')
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name=_('Активно'),
        help_text=_('Только активные уведомления видны пользователям')
    )

    priority = models.IntegerField(
        default=0,
        verbose_name=_('Приоритет'),
        help_text=_('Чем выше число, тем выше в списке. 0 = обычный приоритет')
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Создано')
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('Обновлено')
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_notifications',
        verbose_name=_('Создал')
    )

    # Поле для персонализированных уведомлений
    target_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='personal_notifications',
        verbose_name=_('Конкретный пользователь'),
        help_text=_('Если указан, уведомление будет видно только этому пользователю')
    )

    # Связанное бронирование — позволяет действовать (подтвердить/отклонить)
    # прямо со страницы уведомления.
    booking = models.ForeignKey(
        'Booking',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name=_('Связанное бронирование'),
        help_text=_('Если уведомление о брони — позволяет подтвердить/отклонить прямо из уведомления')
    )

    class Category(models.TextChoices):
        GENERAL = 'general', _('Общее')
        BOOKING = 'booking', _('Бронирование')
        LESSON = 'lesson', _('Урок')
        PAYMENT_IN = 'payment_in', _('Пополнение')
        PAYMENT_OUT = 'payment_out', _('Списание')
        REVIEW = 'review', _('Отзыв')
        MODERATION = 'moderation', _('Модерация')
        REMINDER = 'reminder', _('Напоминание')
        SUBSCRIPTION = 'subscription', _('Подписка')
        SUCCESS = 'success', _('Успех')
        WARNING = 'warning', _('Предупреждение')

    # Категория определяет UI-иконку в списке уведомлений (вместо эмодзи в тексте).
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.GENERAL,
        verbose_name=_('Категория'),
        help_text=_('Определяет иконку уведомления в интерфейсе'),
    )

    # Категория → Font Awesome иконка + тон оформления (info/success/danger/warning).
    _ICON_MAP = {
        'general': ('fa-bell', 'info'),
        'booking': ('fa-calendar-check', 'info'),
        'lesson': ('fa-chalkboard-user', 'info'),
        'payment_in': ('fa-wallet', 'success'),
        'payment_out': ('fa-money-bill-transfer', 'warning'),
        'review': ('fa-star', 'warning'),
        'moderation': ('fa-user-clock', 'info'),
        'reminder': ('fa-clock', 'info'),
        'subscription': ('fa-box', 'info'),
        'success': ('fa-circle-check', 'success'),
        'warning': ('fa-triangle-exclamation', 'warning'),
    }

    @property
    def icon(self):
        """Font Awesome класс иконки для категории уведомления."""
        return self._ICON_MAP.get(self.category, self._ICON_MAP['general'])[0]

    @property
    def icon_tone(self):
        """Цветовой тон иконки: info / success / warning / danger."""
        return self._ICON_MAP.get(self.category, self._ICON_MAP['general'])[1]

    class Meta:
        verbose_name = _('Уведомление')
        verbose_name_plural = _('Уведомления')
        ordering = ['-priority', '-created_at']
        indexes = [
            models.Index(fields=['is_active', 'target']),
            models.Index(fields=['-priority', '-created_at']),
            models.Index(fields=['target_user', 'is_active']),
        ]

    def __str__(self):
        if self.target_user:
            return f"{self.title} (для {self.target_user.get_full_name()})"
        return f"{self.title} ({self.get_target_display()})"
    
    def is_visible_for_user(self, user):
        """
        Проверяет, должен ли пользователь видеть это уведомление
        """
        if not self.is_active:
            return False
        
        # Если указан конкретный пользователь - показываем только ему
        if self.target_user:
            return self.target_user.id == user.id
        
        if self.target == 'all':
            return True
        
        if self.target == 'students':
            return user.user_type == 'student'
        
        if self.target == 'teachers':
            return user.user_type == 'teacher'
        
        if self.target == 'admins':
            return user.is_staff or user.is_superuser
        
        return False
    
    def get_unread_count_for_user(self, user):
        """
        Возвращает количество непрочитанных уведомлений для пользователя
        """
        if not self.is_visible_for_user(user):
            return 0
        
        is_read = NotificationRead.objects.filter(
            notification=self,
            user=user
        ).exists()
        
        return 0 if is_read else 1
    
    @classmethod
    def get_unread_count(cls, user):
        """
        Возвращает общее количество непрочитанных уведомлений для пользователя.
        Использует один SQL-запрос с Exists-подзапросом.
        """
        from django.db.models import OuterRef, Exists

        if not user.is_authenticated:
            return 0

        read_subquery = NotificationRead.objects.filter(
            user=user,
            notification_id=OuterRef('id')
        )

        return cls.objects.filter(
            is_active=True
        ).filter(
            cls._build_visibility_filter(user)
        ).exclude(
            Exists(read_subquery)
        ).count()
    
    @classmethod
    def _build_visibility_filter(cls, user):
        """Строит Q-фильтр видимости уведомлений для пользователя."""
        visibility_filters = [
            Q(target_user=user),
            Q(target='all', target_user__isnull=True),
        ]
        if getattr(user, 'user_type', None) == 'student':
            visibility_filters.append(Q(target='students', target_user__isnull=True))
        if getattr(user, 'user_type', None) == 'teacher':
            visibility_filters.append(Q(target='teachers', target_user__isnull=True))
        if user.is_staff or user.is_superuser:
            visibility_filters.append(Q(target='admins', target_user__isnull=True))
        return Q(*visibility_filters, _connector=Q.OR)

    @classmethod
    def get_user_notifications(cls, user, include_read=False):
        """
        Возвращает queryset уведомлений для пользователя.
        Фильтрация на уровне БД вместо Python-итерации.
        """
        if not user.is_authenticated:
            return cls.objects.none()

        notifications = cls.objects.filter(
            is_active=True
        ).filter(
            cls._build_visibility_filter(user)
        )

        if not include_read:
            read_ids = NotificationRead.objects.filter(
                user=user
            ).values_list('notification_id', flat=True)
            notifications = notifications.exclude(id__in=read_ids)

        return notifications
    
    def mark_as_read(self, user):
        """
        Помечает уведомление как прочитанное для пользователя
        """
        NotificationRead.objects.get_or_create(
            notification=self,
            user=user
        )

        # Инвалидируем кэш непрочитанных уведомлений
        from .context_processors import invalidate_notification_cache
        invalidate_notification_cache(user.pk)
    
    def is_read_by(self, user):
        """
        Проверяет, прочитано ли уведомление пользователем
        """
        return NotificationRead.objects.filter(
            notification=self,
            user=user
        ).exists()


class DailyReminderTemplate(models.Model):
    """
    Шаблон текста для ежедневной автоматической рассылки (утренней/вечерней).
    Администратор может добавлять, редактировать и отключать варианты из
    admin-dashboard. При отправке выбирается случайный активный шаблон
    соответствующего периода и языка.
    """
    PERIOD_CHOICES = [
        ('morning', _('Утро')),
        ('evening', _('Вечер')),
    ]
    LANGUAGE_CHOICES = [
        ('ru', _('Русский')),
        ('uz', "O‘zbek"),
        ('en', 'English'),
    ]

    period = models.CharField(
        max_length=10,
        choices=PERIOD_CHOICES,
        verbose_name=_('Период'),
    )
    language = models.CharField(
        max_length=2,
        choices=LANGUAGE_CHOICES,
        verbose_name=_('Язык'),
    )
    text = models.TextField(
        verbose_name=_('Текст сообщения'),
        help_text=_(
            'Поддерживается Markdown: *жирный*, _курсив_. '
            'Можно использовать эмодзи и переводы строк.'
        ),
    )
    is_active = models.BooleanField(
        default=True,
        verbose_name=_('Активен'),
        help_text=_('Если выключено — шаблон не попадает в рассылку.'),
    )
    note = models.CharField(
        max_length=200,
        blank=True,
        default='',
        verbose_name=_('Заметка для админа'),
        help_text=_('Необязательное описание — только для вас.'),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_('Создан'))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_('Обновлён'))

    class Meta:
        verbose_name = _('Шаблон ежедневного напоминания')
        verbose_name_plural = _('Шаблоны ежедневных напоминаний')
        ordering = ['period', 'language', '-is_active', '-updated_at']
        indexes = [
            models.Index(fields=['period', 'language', 'is_active']),
        ]

    def __str__(self):
        return f"{self.get_period_display()} · {self.get_language_display()}: {self.text[:40]}…"


class NotificationRead(models.Model):
    """
    Модель для отслеживания прочитанных уведомлений
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='read_notifications',
        verbose_name=_('Пользователь')
    )

    notification = models.ForeignKey(
        Notification,
        on_delete=models.CASCADE,
        related_name='read_by_users',
        verbose_name=_('Уведомление')
    )

    read_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Прочитано')
    )

    class Meta:
        verbose_name = _('Прочитанное уведомление')
        verbose_name_plural = _('Прочитанные уведомления')
        unique_together = ['user', 'notification']
        indexes = [
            models.Index(fields=['user', 'notification']),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.notification.title}"


class WizardDraft(models.Model):
    """
    Черновик многошагового мастера регистрации учителя.

    Хранит сериализованное состояние SessionWizardView, привязанное к session_key.
    Если пользователь закроет браузер или выйдет — он сможет продолжить
    регистрацию с того же шага, на котором остановился.

    Действителен 14 дней (см. management-команду cleanup_wizard_drafts).
    """
    session_key = models.CharField(
        max_length=64,
        primary_key=True,
        verbose_name=_('Ключ сессии Django')
    )
    wizard_name = models.CharField(
        max_length=50,
        default='teacher_registration',
        help_text=_('Идентификатор wizard (на случай если их будет несколько)')
    )
    current_step = models.CharField(
        max_length=50,
        blank=True,
        help_text=_('На каком шаге был пользователь')
    )
    data = models.JSONField(
        default=dict,
        blank=True,
        help_text=_('Сериализованные данные wizard (storage.data)')
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Черновик регистрации')
        verbose_name_plural = _('Черновики регистрации')
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['-updated_at']),
        ]

    def __str__(self):
        return f"Draft {self.session_key[:8]}… step={self.current_step}"


# =============================================================================
# BOOKING / LESSON SYSTEM (Phase 1)
# =============================================================================
# Сценарий:
#   1. Учитель создаёт TimeSlot (либо вручную, либо генерируется из расписания).
#   2. Ученик нажимает Book на свободном слоте → create_hold():
#      - status слота меняется на 'held'
#      - создаётся Booking со status='pending' и expires_at=now+15min
#      - Celery beat-задача release_expired_holds каждую минуту чистит протухшие
#   3. Учитель подтверждает или отклоняет → Booking.confirm() / reject().
#   4. После урока → Booking.complete().
#
# Race-safety: Booking.slot = OneToOneField → UNIQUE constraint на уровне БД.
# Два одновременных запроса не смогут забронировать один и тот же слот —
# второй INSERT упадёт IntegrityError, плюс мы используем select_for_update.


class SlotUnavailable(Exception):
    """Raised when trying to book a slot that's no longer free."""
    pass


class TimeSlot(models.Model):
    """
    Конкретный временной слот учителя (без рекурренции).
    Не пересекается с другими слотами того же учителя.
    """
    STATUS_CHOICES = [
        ('free', _('Свободен')),
        ('held', _('Зарезервирован (ожидает подтверждения)')),
        ('booked', _('Забронирован')),
        ('blocked', _('Заблокирован учителем')),
    ]

    teacher = models.ForeignKey(
        TeacherProfile,
        on_delete=models.CASCADE,
        related_name='time_slots',
        verbose_name=_('Учитель'),
    )
    start_at = models.DateTimeField(verbose_name=_('Начало'))
    end_at = models.DateTimeField(verbose_name=_('Конец'))
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='free',
        db_index=True,
        verbose_name=_('Статус'),
    )
    # Дедлайн удержания слота (до 1ч до урока). Заполнено когда status='held'.
    hold_expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _('Временной слот')
        verbose_name_plural = _('Временные слоты')
        ordering = ['start_at']
        indexes = [
            models.Index(fields=['teacher', 'start_at']),
            models.Index(fields=['status', 'hold_expires_at']),
            models.Index(fields=['teacher', 'status', 'start_at']),
        ]
        constraints = [
            # Слот не может начинаться позже чем заканчивается
            models.CheckConstraint(
                check=models.Q(end_at__gt=models.F('start_at')),
                name='timeslot_end_after_start',
            ),
            # Hold-поле имеет смысл только когда status='held'
            models.CheckConstraint(
                check=(
                    models.Q(status='held', hold_expires_at__isnull=False) |
                    ~models.Q(status='held')
                ),
                name='timeslot_hold_consistency',
            ),
        ]

    def __str__(self):
        return f"{self.teacher.user.get_full_name()} • {self.start_at:%Y-%m-%d %H:%M}-{self.end_at:%H:%M} • {self.get_status_display()}"

    @property
    def duration_minutes(self):
        return int((self.end_at - self.start_at).total_seconds() // 60)

    @property
    def is_in_past(self):
        return self.end_at < timezone.now()

    def overlaps_with(self, other_start, other_end):
        """True если слот пересекается с диапазоном [other_start, other_end)."""
        return self.start_at < other_end and other_start < self.end_at


class Booking(models.Model):
    """
    Заявка ученика на конкретный TimeSlot.
    OneToOneField на slot — гарантирует что один слот = одна заявка.
    """
    STATUS_CHOICES = [
        ('pending', _('Ожидает подтверждения учителя')),
        ('confirmed', _('Подтверждено')),
        ('completed', _('Завершён')),
        ('cancelled_by_student', _('Отменено учеником')),
        ('cancelled_by_teacher', _('Отменено учителем')),
        ('rescheduled', _('Перенесено')),
        ('expired', _('Заявка истекла (не подтверждена до начала урока)')),
        ('no_show_student', _('Ученик не пришёл')),
        ('no_show_teacher', _('Учитель не пришёл')),
        ('not_held', _('Не состоялся (никто не пришёл)')),
    ]

    # Активные статусы — slot может иметь только один Booking в этих статусах.
    # Cancelled / expired / completed / no_show — могут быть в истории сколько угодно.
    ACTIVE_STATUSES = ('pending', 'confirmed')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    slot = models.ForeignKey(
        TimeSlot,
        on_delete=models.CASCADE,
        related_name='bookings',
        verbose_name=_('Слот'),
    )
    student = models.ForeignKey(
        'User',
        on_delete=models.CASCADE,
        related_name='bookings',
        verbose_name=_('Ученик'),
    )
    subject = models.ForeignKey(
        Subject,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='bookings',
        verbose_name=_('Предмет'),
    )

    status = models.CharField(
        max_length=25,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True,
    )

    # Дедлайн подтверждения (до 1ч до урока). Заполнено только пока status='pending'.
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)

    student_message = models.TextField(
        blank=True, max_length=1000,
        help_text=_('Сообщение от ученика при бронировании'),
    )
    teacher_reply = models.TextField(
        blank=True, max_length=1000,
        help_text=_('Комментарий учителя при подтверждении/отказе'),
    )

    is_trial = models.BooleanField(
        default=False,
        verbose_name=_('Пробный урок'),
    )

    # Phase 9.5: цена платного пробного, снэпшот на момент бронирования.
    # NULL = бесплатный пробный или обычный урок подписки.
    trial_price_paid = models.DecimalField(
        max_digits=12, decimal_places=2,
        null=True, blank=True,
        verbose_name=_('Оплачено за пробный'),
        help_text=_('Снэпшот цены платного пробного на момент бронирования. NULL = бесплатный или не пробный.'),
    )

    # Phase 3: связь с подпиской. Null = разовая бронь (trial или старая логика).
    subscription = models.ForeignKey(
        'billing.Subscription',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='bookings',
        verbose_name=_('Подписка'),
    )

    # Phase 5: ссылка на видеоконференцию (Google Meet/Zoom)
    meeting_url = models.URLField(blank=True, max_length=500)

    # Фактические времена урока (заполняются Celery при start/complete)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    # Присутствие: когда каждая сторона РЕАЛЬНО подключилась к видео-конференции
    # (событие Jitsi videoConferenceJoined, а не просто открытие страницы).
    # Используется, чтобы НЕ платить учителю за урок, на который он не пришёл
    # (см. settle_after_end / mark_completed_lessons).
    teacher_joined_at = models.DateTimeField(null=True, blank=True)
    student_joined_at = models.DateTimeField(null=True, blank=True)
    # Фактически проведённые в звонке секунды (накапливаются по событию выхода).
    # Для прозрачности и разбора споров; жёстко выплату не блокируют.
    teacher_present_seconds = models.PositiveIntegerField(default=0)
    student_present_seconds = models.PositiveIntegerField(default=0)

    # Неявка ученика «прощена» (ТЗ §6): одна из первых N неявок за окно.
    # True → урок возвращается ученику: НЕ списывается из пакета, учителю НЕ
    # платится, не учитывается в квоте брони (ученик выбирает новую дату).
    # False у обычной no_show_student (4-я+) → урок засчитан, оплата учителю.
    no_show_forgiven = models.BooleanField(default=False, db_index=True)

    # Аудит переносов: сколько раз эту бронь переносил ученик и когда последний раз.
    reschedule_count = models.PositiveSmallIntegerField(default=0)
    rescheduled_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Бронирование / Урок'
        verbose_name_plural = 'Бронирования / Уроки'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['student', '-created_at']),
            models.Index(fields=['status', 'expires_at']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['slot', 'status']),
            # Горячий путь: уроки подписки (дашборды прогресса, выплаты).
            models.Index(fields=['subscription', 'status']),
        ]
        constraints = [
            # На slot может быть только один Booking в активных статусах
            # (pending или confirmed). История cancelled/expired/completed
            # не ограничена. Гарантия на уровне БД.
            models.UniqueConstraint(
                fields=['slot'],
                condition=models.Q(status__in=('pending', 'confirmed')),
                name='one_active_booking_per_slot',
            ),
        ]

    def __str__(self):
        return f"{self.student.get_full_name()} → {self.slot} • {self.get_status_display()}"

    # ---------- Lifecycle методы ----------

    # За сколько минут до начала урока истекает окно подтверждения.
    # Бизнес-правило: учитель может подтвердить заявку (в т.ч. пробную)
    # вплоть до CONFIRM_LEAD_MINUTES минут до старта — без искусственных
    # ограничений «за час».
    CONFIRM_LEAD_MINUTES = 5

    @classmethod
    def compute_hold_expiry(cls, slot, now=None):
        """Дедлайн подтверждения брони: за CONFIRM_LEAD_MINUTES до начала урока.

        Если до урока осталось меньше этого окна — даём учителю подтвердить
        вплоть до самого начала (иначе заявка истекла бы сразу).
        """
        now = now or timezone.now()
        deadline = slot.start_at - timedelta(minutes=cls.CONFIRM_LEAD_MINUTES)
        if deadline <= now:
            deadline = slot.start_at
        return deadline

    @classmethod
    def create_hold(cls, slot_id, student, subject=None, message='',
                    is_trial=False, hold_minutes=None):
        """
        Атомарно: помечаем slot 'held' и создаём Booking 'pending'.
        Если slot уже занят — SlotUnavailable.
        Race-safe: select_for_update + UNIQUE на slot.

        Окно подтверждения: по умолчанию до 1 часа до начала урока
        (см. compute_hold_expiry). Параметр hold_minutes оставлен для
        явного фиксированного hold (используется в тестах).
        """
        from django.db import transaction
        with transaction.atomic():
            slot = TimeSlot.objects.select_for_update().get(pk=slot_id)
            if slot.status != 'free':
                raise SlotUnavailable(f'Slot {slot_id} is {slot.status}, not free')
            # Нельзя бронировать слот, который уже начался (или прошёл): урок
            # «в процессе/в прошлом» не подлежит бронированию. Используем start_at,
            # а не end_at (is_in_past), иначе слот «идёт прямо сейчас» считался бы
            # доступным и hold рождался бы уже протухшим.
            if slot.start_at <= timezone.now():
                raise SlotUnavailable(f'Slot {slot_id} has already started')
            # Нельзя бронировать слот учителя, снятого с публикации/модерации
            # (прямой POST в API мимо публичного списка слотов).
            teacher = slot.teacher
            if not teacher.is_active or teacher.moderation_status != 'approved':
                raise SlotUnavailable('Teacher is not available for booking')
            if hold_minutes is not None:
                expires = timezone.now() + timedelta(minutes=hold_minutes)
            else:
                expires = cls.compute_hold_expiry(slot)
            slot.status = 'held'
            slot.hold_expires_at = expires
            slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
            booking = cls.objects.create(
                slot=slot,
                student=student,
                subject=subject,
                student_message=message[:1000],
                is_trial=is_trial,
                status='pending',
                expires_at=expires,
            )
            return booking

    def jitsi_room_name(self):
        """Имя видео-комнаты Jitsi, уникальное для этой брони (UUID → приватно)."""
        from django.conf import settings
        prefix = getattr(settings, 'JITSI_ROOM_PREFIX', 'UstozHub')
        return f'{prefix}-{self.id}'

    def build_meeting_url(self):
        """Авто-ссылка на видео-комнату Jitsi, уникальная для этой брони.

        Учитель может заменить на свою ссылку (Zoom/Meet) — тогда не трогаем.
        """
        from django.conf import settings
        base = getattr(settings, 'JITSI_BASE_URL', 'https://meet.jit.si').rstrip('/')
        return f'{base}/{self.jitsi_room_name()}'

    def is_jitsi_meeting(self):
        """True, если meeting_url ведёт на наш Jitsi (встраиваем в комнату).
        Кастомные внешние ссылки (Zoom и т.п.) открываем напрямую."""
        from django.conf import settings
        base = (getattr(settings, 'JITSI_BASE_URL', '') or '').rstrip('/')
        return bool(self.meeting_url) and bool(base) and self.meeting_url.startswith(base)

    def confirm(self, teacher_reply=''):
        """Учитель подтверждает: status→confirmed, slot→booked, hold снимается.

        Если ссылка на встречу не задана — автоматически создаём
        видео-комнату Jitsi, чтобы кнопка «Войти в урок» всегда работала.

        Race-safe: блокируем строку брони через select_for_update —
        две параллельные вкладки/клика учителя не пройдут оба чек статуса.
        """
        from django.db import transaction
        with transaction.atomic():
            # Перечитываем с блокировкой, чтобы статус не «убежал» между read и write
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.status != 'pending':
                raise ValueError(f'Cannot confirm booking in status {locked.status}')
            locked.status = 'confirmed'
            locked.expires_at = None
            locked.teacher_reply = (teacher_reply or '')[:1000]
            if not (locked.meeting_url or '').strip():
                locked.meeting_url = locked.build_meeting_url()
            locked.save(update_fields=['status', 'expires_at', 'teacher_reply', 'meeting_url', 'updated_at'])
            locked.slot.status = 'booked'
            locked.slot.hold_expires_at = None
            locked.slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
            # Подтягиваем изменения в self (объект, переданный в вызывающий код)
            self.refresh_from_db()

    def reject(self, teacher_reply=''):
        """Учитель отказывает: status→cancelled_by_teacher, slot→free.

        Race-safe: select_for_update — параллельные клики дадут ValueError
        вместо двойной обработки.
        """
        from django.db import transaction
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.status != 'pending':
                raise ValueError(f'Cannot reject booking in status {locked.status}')
            locked.status = 'cancelled_by_teacher'
            locked.expires_at = None
            locked.teacher_reply = (teacher_reply or '')[:1000]
            locked.save(update_fields=['status', 'expires_at', 'teacher_reply', 'updated_at'])
            locked.slot.status = 'free'
            locked.slot.hold_expires_at = None
            locked.slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
            self.refresh_from_db()

    def cancel_by_student(self):
        """Ученик отменяет до начала: slot снова free, бронирование cancelled.

        Race-safe: select_for_update.
        """
        from django.db import transaction
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.status not in ('pending', 'confirmed'):
                raise ValueError(f'Cannot cancel booking in status {locked.status}')
            # Нельзя отменить уже начавшийся/прошедший урок (иначе возврат за
            # проведённый урок). Спор по такому уроку — через dispute-флоу.
            if locked.slot.start_at <= timezone.now():
                raise ValueError('Нельзя отменить начавшийся урок')
            locked.status = 'cancelled_by_student'
            locked.expires_at = None
            locked.save(update_fields=['status', 'expires_at', 'updated_at'])
            locked.slot.status = 'free'
            locked.slot.hold_expires_at = None
            locked.slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
            self.refresh_from_db()

    def cancel_by_teacher(self):
        """Учитель отменяет подтверждённую бронь: slot снова free.

        Race-safe: select_for_update — иначе гонка с Celery mark_completed
        могла оставить урок completed (и оплаченным), хотя учитель его отменил.
        Для pending используйте reject().
        """
        from django.db import transaction
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.status not in ('pending', 'confirmed'):
                raise ValueError(f'Cannot cancel booking in status {locked.status}')
            if locked.slot.start_at <= timezone.now():
                raise ValueError('Нельзя отменить начавшийся урок')
            locked.status = 'cancelled_by_teacher'
            locked.expires_at = None
            locked.save(update_fields=['status', 'expires_at', 'updated_at'])
            slot = TimeSlot.objects.select_for_update().get(pk=locked.slot_id)
            slot.status = 'free'
            slot.hold_expires_at = None
            slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
            self.refresh_from_db()

    def reschedule_by_student(self, new_slot_id):
        """Ученик переносит активную бронь на другой свободный слот того же учителя.

        Правила (v2 Шаг 3):
          * нельзя переносить позже чем за RESCHEDULE_MIN_LEAD_HOURS до начала урока;
          * для урока в рамках оплаченной подписки действует месячный лимит переносов
            (SUBSCRIPTION_FREE_RESCHEDULES_PER_MONTH) и НОВЫЙ слот сразу становится
            подтверждённым (confirmed) — слоты учителя уже его доступность, повторное
            подтверждение не нужно, оплаченный урок не «теряется»;
          * разовый/пробный урок (без подписки) переносится в pending+hold — учитель
            подтверждает новое время, как раньше.
        Аудит: reschedule_count++ и rescheduled_at.
        Race-safe: select_for_update на брони и обоих слотах.
        """
        from django.conf import settings
        from django.db import transaction
        with transaction.atomic():
            # Перечитываем бронь под локом — иначе параллельный confirm() мог
            # уже перевести её в confirmed/booked между read во вьюхе и сюда,
            # и мы бы откатили подтверждённый урок на стейле.
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.status not in ('pending', 'confirmed'):
                raise ValueError(f'Нельзя перенести бронь в статусе {locked.get_status_display()}')
            if str(new_slot_id) == str(locked.slot_id):
                raise ValueError('Это тот же самый слот')

            now = timezone.now()
            # Дедлайн: запрещаем перенос слишком близко к началу текущего урока.
            lead_hours = settings.RESCHEDULE_MIN_LEAD_HOURS
            if locked.slot.start_at - now < timedelta(hours=lead_hours):
                raise ValueError(
                    f'Перенести урок можно не позднее чем за {lead_hours} ч до начала.'
                )

            is_subscription = locked.subscription_id is not None

            # Месячный лимит переносов — только для подписочных уроков.
            sub = None
            if is_subscription:
                from billing.models import Subscription
                sub = Subscription.objects.select_for_update().get(pk=locked.subscription_id)
                period = now.strftime('%Y-%m')
                limit = settings.SUBSCRIPTION_FREE_RESCHEDULES_PER_MONTH
                used = sub.reschedules_used if sub.reschedules_period == period else 0
                if used >= limit:
                    raise ValueError(
                        f'Лимит переносов в этом месяце исчерпан ({limit}).'
                    )

            new_slot = TimeSlot.objects.select_for_update().get(pk=new_slot_id)
            if new_slot.teacher_id != locked.slot.teacher_id:
                raise ValueError('Новый слот принадлежит другому учителю')
            if new_slot.status != 'free':
                raise SlotUnavailable(f'Слот {new_slot_id} уже занят')
            if new_slot.start_at <= now:
                raise SlotUnavailable(f'Слот {new_slot_id} уже начался или в прошлом')

            # Недельная квота (v2 Шаг 4): нельзя переносом превысить число уроков
            # в неделю по тарифу — иначе ученик стащил бы 4 урока в одну неделю.
            if is_subscription and sub is not None:
                monday = (new_slot.start_at
                          - timedelta(days=new_slot.start_at.weekday())
                          ).replace(hour=0, minute=0, second=0, microsecond=0)
                next_monday = monday + timedelta(days=7)
                week_active = (
                    type(self).objects
                    .filter(subscription_id=sub.pk,
                            status__in=('pending', 'confirmed'),
                            slot__start_at__gte=monday,
                            slot__start_at__lt=next_monday)
                    .exclude(pk=locked.pk)
                    .count()
                )
                if week_active >= sub.lessons_per_week:
                    raise ValueError(
                        f'В выбранной неделе уже максимум уроков по вашему тарифу '
                        f'({sub.lessons_per_week}). Выберите другую неделю.'
                    )

            old_slot = TimeSlot.objects.select_for_update().get(pk=locked.slot_id)
            old_slot.status = 'free'
            old_slot.hold_expires_at = None
            old_slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])

            if is_subscription:
                # Оплаченный урок не теряем: новый слот сразу booked + confirmed.
                new_slot.status = 'booked'
                new_slot.hold_expires_at = None
                new_slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
                locked.slot = new_slot
                locked.status = 'confirmed'
                locked.expires_at = None
            else:
                # Разовый/пробный урок — снова в hold, ждёт подтверждения учителя.
                expires = self.compute_hold_expiry(new_slot)
                new_slot.status = 'held'
                new_slot.hold_expires_at = expires
                new_slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
                locked.slot = new_slot
                locked.status = 'pending'
                locked.expires_at = expires

            locked.reschedule_count = (locked.reschedule_count or 0) + 1
            locked.rescheduled_at = now
            locked.save(update_fields=[
                'slot', 'status', 'expires_at',
                'reschedule_count', 'rescheduled_at', 'updated_at',
            ])

            # Фиксируем месячный счётчик переносов подписки.
            if sub is not None:
                if sub.reschedules_period == now.strftime('%Y-%m'):
                    sub.reschedules_used = sub.reschedules_used + 1
                else:
                    sub.reschedules_period = now.strftime('%Y-%m')
                    sub.reschedules_used = 1
                sub.save(update_fields=['reschedules_used', 'reschedules_period', 'updated_at'])

            self.refresh_from_db()
            return locked.status

    def expire(self):
        """Hold протух (вызывается Celery задачей). slot снова free.

        Race-safe: перечитываем бронь и слот под select_for_update. Без лока
        задача могла перетереть только что подтверждённую учителем бронь
        (last-write-wins) и освободить уже забронированный слот.
        """
        from django.db import transaction
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.status != 'pending':
                return  # уже не pending (подтверждена/отменена) — игнорируем
            locked.status = 'expired'
            locked.expires_at = None
            locked.save(update_fields=['status', 'expires_at', 'updated_at'])
            slot = TimeSlot.objects.select_for_update().get(pk=locked.slot_id)
            if slot.status == 'held':
                slot.status = 'free'
                slot.hold_expires_at = None
                slot.save(update_fields=['status', 'hold_expires_at', 'updated_at'])
            self.refresh_from_db()

    def mark_completed(self):
        """Урок прошёл (вызывается Celery после end_at).

        После завершения просим ученика оставить отзыв (если он ещё не оставлен
        по этой броне) — ключевой шаг для маркетплейса.

        Race-safe: select_for_update — иначе задача могла перетереть отмену
        учителя/ученика, и отменённый урок всё равно был бы оплачен.
        """
        from django.db import transaction
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.status != 'confirmed':
                return
            locked.status = 'completed'
            locked.ended_at = timezone.now()
            locked.save(update_fields=['status', 'ended_at', 'updated_at'])
            self.refresh_from_db()
        LessonEvent.log(self, 'settle_completed')
        try:
            self.request_review()
        except Exception:
            logger.warning('request_review failed for booking %s', self.pk, exc_info=True)

    def record_join(self, *, is_teacher: bool):
        """Фиксирует факт входа стороны в комнату урока (для учёта присутствия).

        Идемпотентно: первое значение не перезаписывается. Пишем через
        .update(), чтобы не словить гонку двух одновременных открытий комнаты.
        """
        now = timezone.now()
        updates = {}
        if is_teacher and self.teacher_joined_at is None:
            updates['teacher_joined_at'] = now
        if (not is_teacher) and self.student_joined_at is None:
            updates['student_joined_at'] = now
        if not updates:
            return
        if self.started_at is None:
            updates['started_at'] = now
        updates['updated_at'] = now
        type(self).objects.filter(pk=self.pk).update(**updates)
        for field, value in updates.items():
            setattr(self, field, value)
        LessonEvent.log(
            self, 'join_teacher' if is_teacher else 'join_student',
            actor='teacher' if is_teacher else 'student',
        )

    def add_presence_seconds(self, *, is_teacher: bool, seconds: int):
        """Накопить фактические секунды присутствия (по событию выхода из звонка).

        Клампим до разумного потолка (длительность урока + 30 мин), чтобы
        подделанное значение не раздуло метрику. Race-safe через F().
        """
        from django.db.models import F
        try:
            seconds = int(seconds)
        except (TypeError, ValueError):
            return
        if seconds <= 0:
            return
        cap = self.slot.duration_minutes * 60 + 1800
        seconds = min(seconds, cap)
        field = 'teacher_present_seconds' if is_teacher else 'student_present_seconds'
        type(self).objects.filter(pk=self.pk).update(**{field: F(field) + seconds})

    def settle_after_end(self) -> str:
        """Решает судьбу confirmed-урока после end_at на основе присутствия.

        Для урока в нашем Jitsi (присутствие отслеживается):
          * никто не подключился               → not_held (ТЗ §8: возврат ученику,
            никому не начисляется);
          * учитель не подключился             → no_show_teacher (ТЗ §7: возврат);
          * учитель был, ученик не подключился → no_show_student (ТЗ §6). Первые
            STUDENT_NO_SHOW_FORGIVE_LIMIT неявок за окно «прощаются»
            (no_show_forgiven=True: урок возвращается ученику, оплаты нет);
            начиная с (N+1)-й — урок засчитан учителю, ученик теряет урок;
          * оба подключились                   → completed.

        Для внешних ссылок (Zoom и т.п.) сигнала о присутствии нет → completed
        по времени (прежнее поведение, чтобы не штрафовать учителя ложно).

        Возвращает итоговый статус-строку.
        """
        from django.db import transaction
        with transaction.atomic():
            locked = type(self).objects.select_for_update().get(pk=self.pk)
            if locked.status != 'confirmed':
                return locked.status
            if locked.is_jitsi_meeting():
                teacher_absent = locked.teacher_joined_at is None
                student_absent = locked.student_joined_at is None
                now = timezone.now()
                if teacher_absent and student_absent:
                    # ТЗ §8 — не состоялся: деньги остаются в эскроу (вернутся
                    # ученику), урок не списывается и не учитывается в квоте.
                    locked.status = 'not_held'
                    locked.ended_at = now
                    locked.save(update_fields=['status', 'ended_at', 'updated_at'])
                    self.refresh_from_db()
                    LessonEvent.log(self, 'settle_not_held')
                    return 'not_held'
                if teacher_absent:
                    locked.status = 'no_show_teacher'
                    locked.ended_at = now
                    locked.save(update_fields=['status', 'ended_at', 'updated_at'])
                    self.refresh_from_db()
                    LessonEvent.log(self, 'settle_no_show_teacher')
                    return 'no_show_teacher'
                if student_absent:
                    # ТЗ §6: «прощаем» неявку, только если это урок пакета —
                    # у пакета есть что «вернуть». Пробные/разовые не прощаем.
                    forgiven = bool(locked.subscription_id) and \
                        type(self).should_forgive_student_no_show(locked.student_id, now)
                    locked.status = 'no_show_student'
                    locked.no_show_forgiven = forgiven
                    locked.ended_at = now
                    locked.save(update_fields=[
                        'status', 'no_show_forgiven', 'ended_at', 'updated_at',
                    ])
                    self.refresh_from_db()
                    LessonEvent.log(
                        self,
                        'no_show_forgiven' if forgiven else 'no_show_consumed',
                    )
                    return 'no_show_student'
        # Учитель присутствовал (или присутствие не отслеживается) — завершаем штатно.
        self.mark_completed()
        return self.status

    @staticmethod
    def count_student_no_shows(student_id, *, now=None, days=None) -> int:
        """Сколько неявок (no_show_student) у ученика за окно последних `days` дней.

        Считаем по времени НАЧАЛА урока (slot.start_at). Учитываются обе
        разновидности неявки — и прощённые, и засчитанные (ТЗ: «подсчёт неявок
        ведётся за последние 90 дней»).
        """
        from django.conf import settings as dj_settings
        now = now or timezone.now()
        if days is None:
            days = getattr(dj_settings, 'STUDENT_NO_SHOW_WINDOW_DAYS', 90)
        since = now - timedelta(days=days)
        return Booking.objects.filter(
            student_id=student_id,
            status='no_show_student',
            slot__start_at__gte=since,
        ).count()

    @classmethod
    def should_forgive_student_no_show(cls, student_id, now=None) -> bool:
        """True, если ТЕКУЩУЮ (ещё не сохранённую) неявку нужно простить.

        Прощаем, пока число прежних неявок за окно меньше лимита, т.е. эта по
        счёту ≤ STUDENT_NO_SHOW_FORGIVE_LIMIT.
        """
        from django.conf import settings as dj_settings
        limit = getattr(dj_settings, 'STUDENT_NO_SHOW_FORGIVE_LIMIT', 3)
        prior = cls.count_student_no_shows(student_id, now=now)
        return (prior + 1) <= limit

    def request_review(self):
        """Создаёт уведомление ученику с просьбой оценить прошедший урок.

        Идемпотентно: не дублирует, если по этой броне уже есть отзыв
        или уже отправляли запрос-уведомление.
        """
        from django.urls import reverse
        # Уже оставлен отзыв по этой броне — не просим
        if Review.objects.filter(booking=self).exists():
            return
        # Не дублируем запрос на один и тот же урок
        if Notification.objects.filter(booking=self, title='Оцените урок').exists():
            return
        teacher_name = self.slot.teacher.user.get_full_name() or self.slot.teacher.user.username
        try:
            action_url = reverse('leave_review', args=[self.id])
        except Exception:
            action_url = ''
        Notification.objects.create(
            title='Оцените урок',
            short_text=f'Как прошёл урок с {teacher_name}?',
            full_text=(
                f'Урок с {teacher_name} завершён. '
                f'Пожалуйста, оцените его — это поможет другим ученикам выбрать преподавателя.'
            ),
            target='specific_user',
            target_user=self.student,
            action_url=action_url,
            priority=5,
            is_active=True,
            category=Notification.Category.REVIEW,
            booking=self,
        )


class LessonReminderSent(models.Model):
    """
    Журнал отправленных напоминаний (для идемпотентности).
    Celery beat-задача send_lesson_reminders проверяет наличие записи
    перед отправкой — гарантирует что одно напоминание уходит только один раз.

    KIND_CHOICES соответствуют T-Xh точкам:
        '24h' — за сутки до урока
        '3h'  — за 3 часа
        '10min' — за 10 минут
    """
    KIND_CHOICES = [
        ('24h', _('За 24 часа')),
        ('3h', _('За 3 часа')),
        ('10min', _('За 10 минут')),
    ]

    booking = models.ForeignKey(
        Booking,
        on_delete=models.CASCADE,
        related_name='reminders_sent',
    )
    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    sent_at = models.DateTimeField(auto_now_add=True)
    # Что было отправлено (для аудита)
    channels = models.CharField(
        max_length=100, blank=True, default='',
        help_text=_('Каналы через запятую: email,in_app,telegram'),
    )

    class Meta:
        verbose_name = 'Отправленное напоминание'
        verbose_name_plural = 'Отправленные напоминания'
        ordering = ['-sent_at']
        constraints = [
            models.UniqueConstraint(
                fields=['booking', 'kind'],
                name='unique_reminder_per_booking_kind',
            ),
        ]
        indexes = [
            models.Index(fields=['booking', 'kind']),
        ]

    def __str__(self):
        return f'{self.booking_id} • {self.kind} • {self.sent_at:%Y-%m-%d %H:%M}'


class LessonEvent(models.Model):
    """Журнал событий урока (ТЗ §11: «все действия фиксируются в журнале»).

    Append-only лента по каждой брони: вход сторон, исход урока (settle),
    прощённая/засчитанная неявка, выплата/возврат, отправленное предупреждение.
    Нужна для разбора споров и прозрачности перед обеими сторонами.
    """
    KIND_CHOICES = [
        ('join_teacher', _('Учитель подключился')),
        ('join_student', _('Ученик подключился')),
        ('settle_completed', _('Урок проведён')),
        ('settle_no_show_teacher', _('Неявка учителя')),
        ('no_show_forgiven', _('Неявка ученика прощена')),
        ('no_show_consumed', _('Неявка ученика — урок списан')),
        ('settle_not_held', _('Урок не состоялся')),
        ('warning_sent', _('Отправлено предупреждение')),
        ('payout', _('Выплата учителю')),
        ('refund', _('Возврат ученику')),
    ]

    booking = models.ForeignKey(
        Booking, on_delete=models.CASCADE, related_name='events',
    )
    kind = models.CharField(max_length=32, choices=KIND_CHOICES, db_index=True)
    # Кто инициатор: 'teacher' / 'student' / 'system'.
    actor = models.CharField(max_length=16, blank=True, default='system')
    # Произвольные детали (порядковый номер неявки, суммы и т.п.).
    meta = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = _('Событие урока')
        verbose_name_plural = _('События уроков')
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['booking', 'created_at']),
        ]

    def __str__(self):
        return f'{self.booking_id} • {self.kind} • {self.created_at:%Y-%m-%d %H:%M}'

    @classmethod
    def log(cls, booking, kind, *, actor='system', **meta):
        """Безопасно записать событие. Никогда не роняет основной поток."""
        try:
            cls.objects.create(
                booking=booking, kind=kind, actor=actor, meta=meta or {},
            )
        except Exception:
            logger.warning('LessonEvent.log failed: %s %s', kind, getattr(booking, 'pk', None),
                           exc_info=True)


