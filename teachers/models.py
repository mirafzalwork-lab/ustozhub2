# models.py
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.urls import reverse
from django.utils import timezone
from PIL import Image
import uuid
import datetime

class User(AbstractUser):
    """Расширенная модель пользователя"""
    USER_TYPES = [
        ('student', 'Ученик'),
        ('teacher', 'Учитель'),
    ]
    
    user_type = models.CharField(max_length=10, choices=USER_TYPES, default='student')
    phone = models.CharField(max_length=20, blank=True, null=True)
    age = models.PositiveIntegerField(validators=[MinValueValidator(10), MaxValueValidator(100)], null=True, blank=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.avatar:
            img = Image.open(self.avatar.path)
            if img.height > 300 or img.width > 300:
                output_size = (300, 300)
                img.thumbnail(output_size)
                img.save(self.avatar.path)

class SubjectCategory(models.Model):
    """Категории предметов для удобной группировки"""
    name = models.CharField(max_length=100, unique=True, verbose_name='Название категории')
    description = models.TextField(blank=True, verbose_name='Описание')
    icon = models.CharField(max_length=50, blank=True, help_text="CSS класс иконки (например, fas fa-calculator)")
    color = models.CharField(max_length=7, default='#3B82F6', help_text="Цвет в формате HEX (#3B82F6)")
    order = models.PositiveIntegerField(default=0, help_text="Порядок сортировки")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Категория предметов'
        verbose_name_plural = 'Категории предметов'

    def __str__(self):
        return self.name
    
    def get_subjects_count(self):
        """Количество активных предметов в категории"""
        return self.subjects.filter(is_active=True).count()


class Subject(models.Model):
    """Модель предметов"""
    category = models.ForeignKey(
        SubjectCategory, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='subjects',
        verbose_name='Категория'
    )
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text="CSS класс иконки")
    is_active = models.BooleanField(default=True)
    is_popular = models.BooleanField(default=False, help_text="Популярный предмет (показывать в топе)")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Предмет'
        verbose_name_plural = 'Предметы'
        indexes = [
            models.Index(fields=['category', 'is_active']),
            models.Index(fields=['is_popular', 'is_active']),
        ]

    def __str__(self):
        return self.name
    
    def get_teachers_count(self):
        """Количество учителей, преподающих этот предмет"""
        return self.teachersubject_set.filter(teacher__is_active=True).count()

class City(models.Model):
    """Модель городов"""
    name = models.CharField(max_length=100, unique=True)
    country = models.CharField(max_length=100, default='Узбекистан')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Город'
        verbose_name_plural = 'Города'

    def __str__(self):
        return f"{self.name}, {self.country}"

class Certificate(models.Model):
    """Модель сертификатов учителей"""
    name = models.CharField(max_length=200)
    issuer = models.CharField(max_length=200, help_text="Кто выдал сертификат")
    file = models.FileField(upload_to='certificates/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Сертификат'
        verbose_name_plural = 'Сертификаты'

    def __str__(self):
        return f"{self.name} - {self.issuer}"

class TeacherProfile(models.Model):
    """Профиль учителя"""
    EDUCATION_LEVELS = [
        ('bachelor', 'Бакалавр'),
        ('master', 'Магистр'),
        ('phd', 'PhD'),
        ('other', 'Другое'),
    ]
    
    TEACHING_FORMATS = [
        ('online', 'Онлайн'),
        ('offline', 'Офлайн'),
        ('both', 'Онлайн и офлайн'),
    ]
    TEACHING_LANGUAGES = [
        ('uz', 'Узбекский'),
        ('ru', 'Русский'),
        ('en', 'Английский'),
        ('tr', 'Турецкий'),
        ('de', 'Немецкий'),
        ('fr', 'Французский'),
        ('other', 'Другой'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='teacher_profile')
    
    # Основная информация
    bio = models.TextField(max_length=1000, blank=True, null=True, help_text="Краткое описание о себе")
    education_level = models.CharField(max_length=20, blank=True, null=True, choices=EDUCATION_LEVELS)
    university = models.CharField(max_length=200, blank=True, null=True)
    specialization = models.CharField(max_length=200, blank=True, null=True)
    
    # Опыт работы
    experience_years = models.PositiveIntegerField(
        validators=[MinValueValidator(0), MaxValueValidator(50)],
        help_text="Лет опыта преподавания"
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
        help_text="Коды языков через запятую (uz,ru,en)"
    )
    # Время работы
    available_from = models.TimeField(default=datetime.time(9, 0))
    available_to = models.TimeField(default=datetime.time(21, 0))
    available_weekdays = models.CharField(max_length=20, default='1,2,3,4,5,6,7', 
                                        help_text="Дни недели через запятую (1-7)")
    
    # Рейтинг и статус
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0.00)
    total_reviews = models.PositiveIntegerField(default=0)
    total_students = models.PositiveIntegerField(default=0)
    is_featured = models.BooleanField(default=False, help_text="Рекомендуемый учитель")
    is_active = models.BooleanField(default=True)
    
    # Сертификаты
    certificates = models.ManyToManyField(Certificate, blank=True)
    
    # Даты
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    MODERATION_STATUS = [
        ('pending', 'На модерации'),
        ('approved', 'Одобрено'),
        ('rejected', 'Отклонено'),
    ]
    
    moderation_status = models.CharField(
        max_length=20,
        choices=MODERATION_STATUS,
        default='pending',
        verbose_name='Статус модерации'
    )
    
    moderation_comment = models.TextField(
        blank=True,
        verbose_name='Комментарий модератора',
        help_text='Причина отклонения или рекомендации'
    )
    
    moderation_date = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Дата модерации'
    )
    
    moderated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='moderated_teachers',
        verbose_name='Проверил'
    )
    
    class Meta:
        verbose_name = 'Профиль учителя'
        verbose_name_plural = 'Профили учителей'
        ordering = ['-created_at']
        # ⚡ ОПТИМИЗАЦИЯ: Индексы для ускорения поиска и фильтрации
        indexes = [
            models.Index(fields=['-rating', '-created_at']),  # Для сортировки на главной
            models.Index(fields=['is_active', 'moderation_status']),  # Для фильтров
            models.Index(fields=['city', 'is_active']),  # Для фильтра по городу
            models.Index(fields=['teaching_format']),  # Для фильтра формата
            models.Index(fields=['experience_years']),  # Для фильтра опыта
        ]
        
    def approve(self, moderator, comment=''):
        """Одобрить профиль учителя"""
        self.moderation_status = 'approved'
        self.moderation_comment = comment
        self.moderation_date = timezone.now()
        self.moderated_by = moderator
        self.save()

    def reject(self, moderator, comment=''):
        """Отклонить профиль учителя"""
        self.moderation_status = 'rejected'
        self.moderation_comment = comment
        self.moderation_date = timezone.now()
        self.moderated_by = moderator
        self.save()
    
    def get_teaching_languages_display(self):
        """Получить названия языков преподавания"""
        languages_dict = dict(self.TEACHING_LANGUAGES)
        codes = self.teaching_languages.split(',')
        return ', '.join([languages_dict.get(code.strip(), code) for code in codes if code.strip()])

    def get_teaching_languages_list(self):
        """Получить список языков для отображения"""
        languages_dict = dict(self.TEACHING_LANGUAGES)
        codes = self.teaching_languages.split(',')
        return [languages_dict.get(code.strip(), code) for code in codes if code.strip()]

    def get_views_count(self, period='all'):
        """
        Получить количество просмотров профиля
        period: 'day', 'week', 'month', 'all'
        """
        from datetime import timedelta
        views = self.profile_views.all()
        
        if period == 'day':
            start_date = timezone.now() - timedelta(days=1)
            views = views.filter(viewed_at__gte=start_date)
        elif period == 'week':
            start_date = timezone.now() - timedelta(weeks=1)
            views = views.filter(viewed_at__gte=start_date)
        elif period == 'month':
            start_date = timezone.now() - timedelta(days=30)
            views = views.filter(viewed_at__gte=start_date)
        
        return views.count()

    def get_unique_viewers_count(self, period='all'):
        """Получить количество уникальных просмотров (по IP)"""
        from datetime import timedelta
        views = self.profile_views.all()
        
        if period == 'day':
            start_date = timezone.now() - timedelta(days=1)
            views = views.filter(viewed_at__gte=start_date)
        elif period == 'week':
            start_date = timezone.now() - timedelta(weeks=1)
            views = views.filter(viewed_at__gte=start_date)
        elif period == 'month':
            start_date = timezone.now() - timedelta(days=30)
            views = views.filter(viewed_at__gte=start_date)
        
        return views.values('viewer_ip').distinct().count()

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.get_subjects_display()}"

    def get_subjects_display(self):
        # ⚡ ОПТИМИЗАЦИЯ: Используем select_related для избежания N+1
        subjects = self.teachersubject_set.select_related('subject').all()[:3]
        return ", ".join([ts.subject.name for ts in subjects])

    def get_min_price(self):
        # ⚡ ОПТИМИЗАЦИЯ: Используем values для более быстрого запроса
        min_price = self.teachersubject_set.aggregate(
            min_price=models.Min('hourly_rate')
        )['min_price']
        return min_price or 0

    def get_available_weekdays_display(self):
        days_map = {
            '1': 'Пн', '2': 'Вт', '3': 'Ср', '4': 'Чт', 
            '5': 'Пт', '6': 'Сб', '7': 'Вс'
        }
        days = self.available_weekdays.split(',')
        return ', '.join([days_map.get(day, day) for day in days])

    def get_absolute_url(self):
        return reverse('teacher_detail', kwargs={'pk': self.pk})
    

class TeacherSubject(models.Model):
    """Промежуточная модель для связи учитель-предмет с ценой"""
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.CASCADE)
    hourly_rate = models.DecimalField(
        max_digits=10, 
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Цена за час в сумах"
    )
    is_free_trial = models.BooleanField(default=False, help_text="Бесплатное пробное занятие")
    description = models.TextField(blank=True, help_text="Дополнительная информация по предмету")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['teacher', 'subject']
        verbose_name = 'Предмет учителя'
        verbose_name_plural = 'Предметы учителей'
        # ⚡ ОПТИМИЗАЦИЯ: Индексы для фильтрации по цене
        indexes = [
            models.Index(fields=['teacher', 'hourly_rate']),  # Для фильтра по цене
            models.Index(fields=['subject']),  # Для фильтра по предмету
        ]

    def __str__(self):
        return f"{self.teacher.user.get_full_name()} - {self.subject.name} ({self.hourly_rate} сум/час)"

class StudentProfile(models.Model):
    """Профиль ученика"""
    EDUCATION_LEVELS = [
        ('elementary', 'Начальная школа (1-4 класс)'),
        ('middle', 'Средняя школа (5-9 класс)'),
        ('high', 'Старшая школа (10-11 класс)'),
        ('university', 'Университет'),
        ('adult', 'Взрослый'),
    ]
    
    LEARNING_FORMATS = [
        ('online', 'Онлайн'),
        ('offline', 'Офлайн'),
        ('both', 'Онлайн и офлайн'),
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
    
    bio = models.TextField(max_length=500, blank=True, verbose_name="Краткое описание")
    
    description = models.TextField(
        max_length=1000, 
        blank=True,
        verbose_name="Описание целей и пожеланий",
        help_text="Расскажите о своих целях обучения, уровне подготовки и ожиданиях"
    )
    
    budget_min = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="Минимальный бюджет (сум/час)",
        help_text="Минимальная цена, которую готов платить"
    )
    
    budget_max = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="Максимальный бюджет (сум/час)",
        help_text="Максимальная цена, которую готов платить"
    )
    
    learning_format = models.CharField(
        max_length=10,
        choices=LEARNING_FORMATS,
        default='both',
        verbose_name="Предпочитаемый формат обучения"
    )
    
    # ✅ НОВЫЕ ПОЛЯ: Контакты для связи
    telegram = models.CharField(
        max_length=100,
        blank=True,
        verbose_name="Telegram",
        help_text="Ваш Telegram username (@username) или номер телефона"
    )
    
    whatsapp = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="WhatsApp",
        help_text="Номер WhatsApp для связи (+998 90 123 45 67)"
    )
    
    is_active = models.BooleanField(
        default=True,
        verbose_name="Активный профиль",
        help_text="Ищет ли ученик учителя в данный момент"
    )
    
    available_weekdays = models.CharField(
        max_length=20,
        default='1,2,3,4,5,6,7',
        blank=True,
        verbose_name="Доступные дни недели",
        help_text="Дни недели через запятую (1-7)"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Профиль ученика'
        verbose_name_plural = 'Профили учеников'
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
        days_map = {
            '1': 'Пн', '2': 'Вт', '3': 'Ср', '4': 'Чт',
            '5': 'Пт', '6': 'Сб', '7': 'Вс'
        }
        if self.available_weekdays:
            days = self.available_weekdays.split(',')
            return ', '.join([days_map.get(day.strip(), day) for day in days])
        return "Не указано"

    def get_views_count(self, period='all'):
        """
        Получить количество просмотров профиля
        period: 'day', 'week', 'month', 'all'
        """
        from datetime import timedelta
        views = self.profile_views.all()
        
        if period == 'day':
            start_date = timezone.now() - timedelta(days=1)
            views = views.filter(viewed_at__gte=start_date)
        elif period == 'week':
            start_date = timezone.now() - timedelta(weeks=1)
            views = views.filter(viewed_at__gte=start_date)
        elif period == 'month':
            start_date = timezone.now() - timedelta(days=30)
            views = views.filter(viewed_at__gte=start_date)
        
        return views.count()

    def get_unique_viewers_count(self, period='all'):
        """Получить количество уникальных просмотров (по IP)"""
        from datetime import timedelta
        views = self.profile_views.all()
        
        if period == 'day':
            start_date = timezone.now() - timedelta(days=1)
            views = views.filter(viewed_at__gte=start_date)
        elif period == 'week':
            start_date = timezone.now() - timedelta(weeks=1)
            views = views.filter(viewed_at__gte=start_date)
        elif period == 'month':
            start_date = timezone.now() - timedelta(days=30)
            views = views.filter(viewed_at__gte=start_date)
        
        return views.values('viewer_ip').distinct().count()

    def __str__(self):
        return f"{self.user.get_full_name()} - Ученик"

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
        verbose_name = 'Переписка'
        verbose_name_plural = 'Переписки'

    def __str__(self):
        return f"Переписка: {self.student.get_full_name()} - {self.teacher.user.get_full_name()}"

    def get_last_message(self):
        return self.messages.first()

class Message(models.Model):
    """Модель сообщения"""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    content = models.TextField(max_length=2000)
    
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Сообщение'
        verbose_name_plural = 'Сообщения'

    def __str__(self):
        return f"{self.sender.get_full_name()}: {self.content[:50]}..."

class Review(models.Model):
    """Отзывы о учителях"""
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name='reviews')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='given_reviews')
    subject = models.ForeignKey(Subject, on_delete=models.SET_NULL, null=True)
    
    rating = models.PositiveIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="Оценка от 1 до 5"
    )
    comment = models.TextField(max_length=1000, blank=True)
    
    # Детальные оценки
    knowledge_rating = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    communication_rating = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    punctuality_rating = models.PositiveIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    
    is_verified = models.BooleanField(default=False, help_text="Проверенный отзыв")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['teacher', 'student', 'subject']
        ordering = ['-created_at']
        verbose_name = 'Отзыв'
        verbose_name_plural = 'Отзывы'

    def __str__(self):
        return f"Отзыв от {self.student.get_full_name()} для {self.teacher.user.get_full_name()}"

class Favorite(models.Model):
    """Избранные учителя"""
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites')
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name='favorited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['student', 'teacher']
        verbose_name = 'Избранный учитель'
        verbose_name_plural = 'Избранные учителя'

    def __str__(self):
        return f"{self.student.get_full_name()} -> {self.teacher.user.get_full_name()}"


class FavoriteStudent(models.Model):
    """Избранные ученики у учителя"""
    teacher = models.ForeignKey(TeacherProfile, on_delete=models.CASCADE, related_name='favorite_students')
    student = models.ForeignKey(StudentProfile, on_delete=models.CASCADE, related_name='favorited_by_teachers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['teacher', 'student']
        verbose_name = 'Избранный ученик'
        verbose_name_plural = 'Избранные ученики'

    def __str__(self):
        return f"{self.teacher.user.get_full_name()} -> {self.student.user.get_full_name()}"

class TelegramUser(models.Model):
    """Модель для хранения Telegram-пользователей"""
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='telegram_user',
        null=True,
        blank=True,
        verbose_name='Связанный пользователь'
    )
    telegram_id = models.BigIntegerField(
        unique=True,
        verbose_name='Telegram ID',
        help_text='Уникальный ID пользователя в Telegram'
    )
    telegram_username = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name='Username в Telegram'
    )
    first_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Имя в Telegram'
    )
    last_name = models.CharField(
        max_length=200,
        blank=True,
        verbose_name='Фамилия в Telegram'
    )
    language_code = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        verbose_name='Язык интерфейса'
    )
    
    # Настройки уведомлений
    notifications_enabled = models.BooleanField(
        default=True,
        verbose_name='Уведомления включены'
    )
    
    # Статистика
    started_bot = models.BooleanField(
        default=False,
        verbose_name='Нажал Start в боте'
    )
    last_interaction = models.DateTimeField(
        auto_now=True,
        verbose_name='Последнее взаимодействие'
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Дата регистрации в боте'
    )
    
    class Meta:
        verbose_name = 'Telegram пользователь'
        verbose_name_plural = 'Telegram пользователи'
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
        ('teacher', 'Профиль учителя'),
        ('student', 'Профиль ученика'),
    ]
    
    # Общие поля
    profile_type = models.CharField(max_length=10, choices=PROFILE_TYPES, verbose_name='Тип профиля')
    viewer_ip = models.GenericIPAddressField(verbose_name='IP адрес просмотревшего', null=True, blank=True)
    viewer_user = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='profile_views_made',
        verbose_name='Пользователь (если авторизован)'
    )
    viewed_at = models.DateTimeField(auto_now_add=True, verbose_name='Дата и время просмотра')
    
    # Связи с профилями
    teacher_profile = models.ForeignKey(
        TeacherProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='profile_views',
        verbose_name='Профиль учителя'
    )
    student_profile = models.ForeignKey(
        StudentProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='profile_views',
        verbose_name='Профиль ученика'
    )
    
    # Дополнительная информация
    user_agent = models.TextField(blank=True, verbose_name='User Agent браузера')
    
    class Meta:
        verbose_name = 'Просмотр профиля'
        verbose_name_plural = 'Просмотры профилей'
        ordering = ['-viewed_at']
        indexes = [
            models.Index(fields=['-viewed_at']),
            models.Index(fields=['teacher_profile', '-viewed_at']),
            models.Index(fields=['student_profile', '-viewed_at']),
            models.Index(fields=['viewer_ip', '-viewed_at']),
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


class NotificationQueue(models.Model):
    """
    Очередь уведомлений для Telegram
    Обеспечивает надёжную доставку с повторными попытками
    """
    STATUS_CHOICES = [
        ('pending', 'Ожидает отправки'),
        ('processing', 'В обработке'),
        ('sent', 'Отправлено'),
        ('failed', 'Ошибка'),
        ('cancelled', 'Отменено'),
    ]
    
    NOTIFICATION_TYPES = [
        ('new_message', 'Новое сообщение'),
        ('new_review', 'Новый отзыв'),
        ('profile_view', 'Просмотр профиля'),
        ('system', 'Системное уведомление'),
        ('broadcast', 'Массовая рассылка'),
    ]
    
    # Основные поля
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='telegram_notifications',
        verbose_name='Получатель'
    )
    notification_type = models.CharField(
        max_length=20,
        choices=NOTIFICATION_TYPES,
        default='new_message',
        verbose_name='Тип уведомления'
    )
    
    # Содержимое
    title = models.CharField(max_length=200, verbose_name='Заголовок')
    message = models.TextField(verbose_name='Текст сообщения')
    data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Дополнительные данные',
        help_text='JSON с доп. информацией (sender_id, conversation_id, url и т.д.)'
    )
    
    # Статус обработки
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        db_index=True,
        verbose_name='Статус'
    )
    retry_count = models.PositiveIntegerField(
        default=0,
        verbose_name='Количество попыток'
    )
    max_retries = models.PositiveIntegerField(
        default=5,
        verbose_name='Максимум попыток'
    )
    last_error = models.TextField(
        blank=True,
        verbose_name='Последняя ошибка'
    )
    
    # Временные метки
    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='Создано'
    )
    scheduled_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        verbose_name='Запланировано на',
        help_text='Время когда уведомление должно быть отправлено'
    )
    processing_started_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Начало обработки'
    )
    sent_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Отправлено'
    )
    
    # Идемпотентность
    idempotency_key = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        verbose_name='Ключ идемпотентности',
        help_text='Уникальный ключ для предотвращения дублирования'
    )
    
    class Meta:
        verbose_name = 'Уведомление в очереди'
        verbose_name_plural = 'Очередь уведомлений'
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
        return datetime.timedelta(minutes=min(delay_minutes, 60))  # Макс 1 час


class NotificationLog(models.Model):
    """
    Лог попыток отправки уведомлений
    Для аудита и отладки
    """
    STATUS_CHOICES = [
        ('success', 'Успешно'),
        ('error', 'Ошибка'),
        ('skipped', 'Пропущено'),
    ]
    
    notification = models.ForeignKey(
        NotificationQueue,
        on_delete=models.CASCADE,
        related_name='logs',
        verbose_name='Уведомление'
    )
    attempt_number = models.PositiveIntegerField(verbose_name='Номер попытки')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        verbose_name='Статус'
    )
    error_message = models.TextField(
        blank=True,
        verbose_name='Сообщение об ошибке'
    )
    telegram_message_id = models.BigIntegerField(
        null=True,
        blank=True,
        verbose_name='ID сообщения в Telegram'
    )
    response_data = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='Данные ответа'
    )
    processing_time_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name='Время обработки (мс)'
    )
    timestamp = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
        verbose_name='Время попытки'
    )
    
    class Meta:
        verbose_name = 'Лог уведомления'
        verbose_name_plural = 'Логи уведомлений'
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
        verbose_name='Поисковый запрос',
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
        verbose_name='Пользователь'
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name='IP адрес')
    found_results_count = models.PositiveIntegerField(default=0, verbose_name='Найдено результатов')
    selected_subject = models.ForeignKey(
        Subject,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Выбранный предмет'
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Дата поиска')
    
    class Meta:
        verbose_name = 'Лог поиска предметов'
        verbose_name_plural = 'Логи поиска предметов'
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
        cls.objects.get_or_create(
            ip_address=request.META.get('REMOTE_ADDR'),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
            page=page,
            month=current_month
        )

    @classmethod
    def get_monthly_stats(cls):
        current_month = timezone.now().date().replace(day=1)
        return cls.objects.filter(month=current_month).count()
