# models.py
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.urls import reverse
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

class Subject(models.Model):
    """Модель предметов"""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text="CSS класс иконки")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Предмет'
        verbose_name_plural = 'Предметы'

    def __str__(self):
        return self.name

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

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='teacher_profile')
    
    # Основная информация
    bio = models.TextField(max_length=1000, help_text="Краткое описание о себе")
    education_level = models.CharField(max_length=20, choices=EDUCATION_LEVELS)
    university = models.CharField(max_length=200, blank=True)
    specialization = models.CharField(max_length=200, blank=True)
    
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

    class Meta:
        verbose_name = 'Профиль учителя'
        verbose_name_plural = 'Профили учителей'

    def __str__(self):
        return f"{self.user.get_full_name()} - {self.get_subjects_display()}"

    def get_subjects_display(self):
        return ", ".join([ts.subject.name for ts in self.teachersubject_set.all()[:3]])

    def get_min_price(self):
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

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='student_profile')
    education_level = models.CharField(max_length=20, choices=EDUCATION_LEVELS, blank=True)
    school_university = models.CharField(max_length=200, blank=True)
    city = models.ForeignKey(City, on_delete=models.SET_NULL, null=True, blank=True)
    interests = models.ManyToManyField(Subject, blank=True, help_text="Интересующие предметы")
    bio = models.TextField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Профиль ученика'
        verbose_name_plural = 'Профили учеников'

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