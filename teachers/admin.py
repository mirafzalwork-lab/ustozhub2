from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (
    User, Subject, City, Certificate,
    TeacherProfile, TeacherSubject, StudentProfile,
    Conversation, Message, Review, Favorite
)


# --- Пользователь ---
@admin.register(User)
class UserAdmin(BaseUserAdmin):
    fieldsets = BaseUserAdmin.fieldsets + (
        ("Дополнительно", {
            "fields": ("user_type", "phone", "age", "avatar", "is_verified")
        }),
    )
    list_display = ("username", "email", "user_type", "phone", "age", "is_verified", "is_active", "date_joined")
    list_filter = ("user_type", "is_verified", "is_active", "is_staff")
    search_fields = ("username", "email", "phone")
    ordering = ("-date_joined",)


# --- Предметы ---
@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


# --- Города ---
@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "is_active")
    list_filter = ("country", "is_active")
    search_fields = ("name", "country")


# --- Сертификаты ---
@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ("name", "issuer")
    search_fields = ("name", "issuer")


# --- TeacherSubject inline ---
class TeacherSubjectInline(admin.TabularInline):
    model = TeacherSubject
    extra = 1


# --- TeacherProfile ---
@admin.register(TeacherProfile)
class TeacherProfileAdmin(admin.ModelAdmin):
    inlines = [TeacherSubjectInline]
    list_display = ("user", "education_level", "teaching_languages", "experience_years", "city", "teaching_format",
                    "rating", "total_reviews", "total_students", "is_featured", "is_active")
    list_filter = ("education_level", "teaching_format", "is_featured", "is_active", "city")
    search_fields = ("user__username", "user__first_name", "user__last_name", "specialization", "university")


# admin.py
from django.contrib import admin
from .models import ProfileView, TeacherProfile, StudentProfile

# Регистрация модели просмотров профилей
@admin.register(ProfileView)
class ProfileViewAdmin(admin.ModelAdmin):
    """Админка для просмотров профилей"""
    list_display = [
        'id', 
        'profile_type', 
        'get_profile_name',
        'get_viewer_name', 
        'viewer_ip', 
        'viewed_at'
    ]
    list_filter = [
        'profile_type', 
        'viewed_at',
        ('teacher_profile', admin.RelatedOnlyFieldListFilter),
        ('student_profile', admin.RelatedOnlyFieldListFilter),
    ]
    search_fields = [
        'viewer_ip',
        'viewer_user__username',
        'viewer_user__email',
        'teacher_profile__user__first_name',
        'teacher_profile__user__last_name',
        'student_profile__user__first_name',
        'student_profile__user__last_name',
    ]
    readonly_fields = [
        'profile_type', 
        'viewer_ip', 
        'viewer_user', 
        'viewed_at',
        'teacher_profile',
        'student_profile',
        'user_agent'
    ]
    date_hierarchy = 'viewed_at'
    ordering = ['-viewed_at']
    list_per_page = 50
    
    def get_profile_name(self, obj):
        """Получить имя просмотренного профиля"""
        if obj.profile_type == 'teacher' and obj.teacher_profile:
            return obj.teacher_profile.user.get_full_name()
        elif obj.profile_type == 'student' and obj.student_profile:
            return obj.student_profile.user.get_full_name()
        return "—"
    get_profile_name.short_description = 'Профиль'
    
    def get_viewer_name(self, obj):
        """Получить имя просмотревшего"""
        if obj.viewer_user:
            return f"{obj.viewer_user.get_full_name()} ({obj.viewer_user.user_type})"
        return f"Гость"
    get_viewer_name.short_description = 'Кто просмотрел'
    
    def has_add_permission(self, request):
        """Запрещаем создание записей вручную"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """Запрещаем изменение записей"""
        return False


# Добавление статистики просмотров в админку профилей учителей
class TeacherProfileAdminInline(admin.StackedInline):
    """Инлайн для отображения статистики просмотров в профиле учителя"""
    model = ProfileView
    extra = 0
    can_delete = False
    verbose_name = 'Просмотр профиля'
    verbose_name_plural = 'История просмотров профиля'
    readonly_fields = ['viewer_user', 'viewer_ip', 'viewed_at', 'user_agent']
    
    def has_add_permission(self, request, obj=None):
        return False


# Дополнительные методы для админки TeacherProfile (добавить к существующей)
class TeacherProfileAdminMixin:
    """Миксин для добавления статистики просмотров в админку"""
    
    def get_total_views(self, obj):
        """Получить общее количество просмотров"""
        return obj.profile_views.count()
    get_total_views.short_description = 'Всего просмотров'
    
    def get_week_views(self, obj):
        """Получить просмотры за неделю"""
        return obj.get_views_count('week')
    get_week_views.short_description = 'Просмотров за неделю'
    
    def get_unique_viewers(self, obj):
        """Получить количество уникальных просмотров"""
        return obj.get_unique_viewers_count('all')
    get_unique_viewers.short_description = 'Уникальных посетителей'


# Аналогично для StudentProfile
class StudentProfileAdminMixin:
    """Миксин для добавления статистики просмотров в админку"""
    
    def get_total_views(self, obj):
        """Получить общее количество просмотров"""
        return obj.profile_views.count()
    get_total_views.short_description = 'Всего просмотров'
    
    def get_week_views(self, obj):
        """Получить просмотры за неделю"""
        return obj.get_views_count('week')
    get_week_views.short_description = 'Просмотров за неделю'
    
    def get_unique_viewers(self, obj):
        """Получить количество уникальных просмотров"""
        return obj.get_unique_viewers_count('all')
    get_unique_viewers.short_description = 'Уникальных посетителей'


# Пример использования в существующей админке:
"""
@admin.register(TeacherProfile)
class TeacherProfileAdmin(TeacherProfileAdminMixin, admin.ModelAdmin):
    list_display = [
        'user', 
        'city', 
        'rating', 
        'get_total_views',  # НОВОЕ
        'get_week_views',   # НОВОЕ
        'is_active'
    ]
    # ... остальные настройки ...
"""
# --- TeacherSubject ---
@admin.register(TeacherSubject)
class TeacherSubjectAdmin(admin.ModelAdmin):
    list_display = ("teacher", "subject", "hourly_rate", "is_free_trial", "created_at")
    list_filter = ("is_free_trial", "subject")
    search_fields = ("teacher__user__username", "subject__name")


# --- StudentProfile ---
@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "education_level", "school_university", "city", "created_at")
    list_filter = ("education_level", "city")
    search_fields = ("user__username", "user__first_name", "user__last_name", "school_university")


# --- Conversation ---
class MessageInline(admin.TabularInline):
    model = Message
    extra = 1
    readonly_fields = ("created_at",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    inlines = [MessageInline]
    list_display = ("id", "teacher", "student", "subject", "is_active", "created_at", "updated_at")
    list_filter = ("is_active", "subject")
    search_fields = ("teacher__user__username", "student__username")


# --- Message ---
@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("conversation", "sender", "content", "is_read", "created_at")
    list_filter = ("is_read",)
    search_fields = ("content", "sender__username", "conversation__id")


# --- Review ---
@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("teacher", "student", "subject", "rating", "is_verified", "created_at")
    list_filter = ("rating", "is_verified", "subject")
    search_fields = ("teacher__user__username", "student__username", "comment")


# --- Favorite ---
@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ("student", "teacher", "created_at")
    search_fields = ("student__username", "teacher__user__username")
