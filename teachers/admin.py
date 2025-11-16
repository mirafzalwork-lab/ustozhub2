from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from .models import (
    User, Subject, SubjectCategory, City, Certificate,
    TeacherProfile, TeacherSubject, StudentProfile,
    Conversation, Message, Review, Favorite, SubjectSearchLog
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


# --- Категории предметов ---
@admin.register(SubjectCategory)
class SubjectCategoryAdmin(admin.ModelAdmin):
    """Администрирование категорий предметов"""
    list_display = ('name', 'get_colored_icon', 'order', 'get_subjects_count_display', 'is_active', 'created_at')
    list_display_links = ('name',)
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'description')
    ordering = ('order', 'name')
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'description', 'is_active')
        }),
        ('Оформление', {
            'fields': ('icon', 'color', 'order'),
            'description': 'Настройки внешнего вида категории'
        }),
    )
    
    def get_colored_icon(self, obj):
        """Отображение иконки с цветом"""
        icon_class = obj.icon or 'fas fa-folder'
        return format_html(
            '<span style="color: {}; font-size: 18px;"><i class="{}"></i></span>',
            obj.color,
            icon_class
        )
    get_colored_icon.short_description = 'Иконка'
    
    def get_subjects_count_display(self, obj):
        """Количество предметов в категории"""
        count = obj.get_subjects_count()
        if count > 0:
            return format_html(
                '<span style="background: #10B981; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px;">{}</span>',
                count
            )
        return format_html('<span style="color: #999;">0</span>')
    get_subjects_count_display.short_description = 'Предметов'
    
    class Media:
        css = {
            'all': ('https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css',)
        }


# --- Предметы ---
@admin.register(Subject)
class SubjectAdminEnhanced(admin.ModelAdmin):
    """Улучшенное администрирование предметов с категориями"""
    list_display = (
        'name', 
        'get_category_badge', 
        'get_teachers_count_display',
        'is_popular', 
        'is_active', 
        'created_at'
    )
    list_display_links = ('name',)
    list_filter = ('is_active', 'is_popular', 'category', 'created_at')
    search_fields = ('name', 'description', 'category__name')
    ordering = ('category__order', 'name')
    list_per_page = 50
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('name', 'category', 'description')
        }),
        ('Настройки', {
            'fields': ('icon', 'is_popular', 'is_active'),
            'description': 'Популярные предметы отображаются в топе при поиске'
        }),
    )
    
    def get_category_badge(self, obj):
        """Отображение категории с цветом"""
        if obj.category:
            return format_html(
                '<span style="background: {}; color: white; padding: 3px 10px; border-radius: 12px; font-size: 11px;">{}</span>',
                obj.category.color,
                obj.category.name
            )
        return format_html('<span style="color: #999; font-style: italic;">Без категории</span>')
    get_category_badge.short_description = 'Категория'
    
    def get_teachers_count_display(self, obj):
        """Количество учителей, преподающих этот предмет"""
        count = obj.get_teachers_count()
        if count > 0:
            return format_html(
                '<span style="background: #3B82F6; color: white; padding: 2px 8px; border-radius: 12px; font-size: 11px;">{} 👨‍🏫</span>',
                count
            )
        return format_html('<span style="color: #999;">0</span>')
    get_teachers_count_display.short_description = 'Учителей'
    
    actions = ['mark_as_popular', 'mark_as_not_popular', 'activate_subjects', 'deactivate_subjects']
    
    def mark_as_popular(self, request, queryset):
        """Отметить как популярные"""
        count = queryset.update(is_popular=True)
        self.message_user(request, f'✅ Отмечено как популярные: {count} предметов')
    mark_as_popular.short_description = '⭐ Отметить как популярные'
    
    def mark_as_not_popular(self, request, queryset):
        """Убрать из популярных"""
        count = queryset.update(is_popular=False)
        self.message_user(request, f'Убрано из популярных: {count} предметов')
    mark_as_not_popular.short_description = '⚪ Убрать из популярных'
    
    def activate_subjects(self, request, queryset):
        """Активировать предметы"""
        count = queryset.update(is_active=True)
        self.message_user(request, f'✅ Активировано: {count} предметов')
    activate_subjects.short_description = '✅ Активировать'
    
    def deactivate_subjects(self, request, queryset):
        """Деактивировать предметы"""
        count = queryset.update(is_active=False)
        self.message_user(request, f'❌ Деактивировано: {count} предметов')
    deactivate_subjects.short_description = '❌ Деактивировать'


# --- Логи поиска предметов ---
@admin.register(SubjectSearchLog)
class SubjectSearchLogAdmin(admin.ModelAdmin):
    """Администрирование логов поиска предметов"""
    list_display = ('query', 'get_user_display', 'found_results_count', 'selected_subject', 'created_at')
    list_filter = ('created_at', 'found_results_count')
    search_fields = ('query', 'user__username', 'ip_address')
    readonly_fields = ('query', 'user', 'ip_address', 'found_results_count', 'selected_subject', 'created_at')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    list_per_page = 100
    
    def get_user_display(self, obj):
        """Отображение пользователя или гостя"""
        if obj.user:
            return format_html(
                '<span style="color: #3B82F6;">👤 {}</span>',
                obj.user.get_full_name() or obj.user.username
            )
        return format_html('<span style="color: #999;">🌐 Гость ({})</span>', obj.ip_address)
    get_user_display.short_description = 'Пользователь'
    
    def has_add_permission(self, request):
        """Запрещаем ручное добавление"""
        return False
    
    def has_change_permission(self, request, obj=None):
        """Запрещаем изменение"""
        return False
    
    # Добавляем действие для анализа популярных запросов
    def changelist_view(self, request, extra_context=None):
        """Добавляем статистику популярных запросов"""
        from django.db.models import Count
        
        # Топ-20 популярных запросов
        popular_queries = SubjectSearchLog.objects.values('query').annotate(
            count=Count('id')
        ).order_by('-count')[:20]
        
        extra_context = extra_context or {}
        extra_context['popular_queries'] = popular_queries
        
        return super().changelist_view(request, extra_context=extra_context)


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


# Импорт для ProfileView
from .models import ProfileView

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


# --- TelegramUser (Telegram integration) ---
from .models import TelegramUser, NotificationQueue, NotificationLog
from django.shortcuts import render, redirect
from django.urls import path
from django.contrib import messages
from .admin_telegram_service import admin_telegram_service
from django.utils import timezone

@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ("user", "telegram_id", "telegram_username", "first_name", 
                   "last_name", "notifications_enabled", "started_bot", "created_at")
    list_filter = ("notifications_enabled", "started_bot")
    search_fields = ("user__username", "telegram_id", "telegram_username", "first_name", "last_name")
    
    # Добавляем массовые действия
    actions = ['send_broadcast_message', 'send_to_django_users']
    
    def send_broadcast_message(self, request, queryset):
        """Отправить сообщение выбранным пользователям"""
        # Преобразуем queryset в список сразу, чтобы избежать проблем с ленивой загрузкой
        users_list = list(queryset) if queryset else []
        
        # Проверяем, что есть выбранные пользователи
        if not users_list:
            self.message_user(
                request,
                "❌ Не выбрано ни одного пользователя",
                messages.ERROR
            )
            return redirect('admin:teachers_telegramuser_changelist')
        
        if 'apply' in request.POST:
            message = request.POST.get('message')
            
            if not message:
                self.message_user(
                    request,
                    "❌ Пожалуйста, введите текст сообщения",
                    messages.ERROR
                )
                # Показываем форму снова с теми же пользователями
                return render(request, 'admin/send_broadcast.html', {
                    'users': users_list,
                    'title': 'Отправить сообщение пользователям',
                    'stats': admin_telegram_service.get_user_status_info()
                })
            
            # Используем новый сервис для отправки
            try:
                stats = admin_telegram_service.send_to_selected_users(
                    telegram_users=users_list,
                    message=message
                )
                
                # Формируем сообщение с детальной статистикой
                if stats['failed'] > 0:
                    message_text = (
                        f"📊 **Результаты отправки:**\n"
                        f"✅ Успешно: {stats['success']}\n"
                        f"❌ Ошибок: {stats['failed']}\n"
                        f"📊 Всего: {stats['total']}\n\n"
                        f"💡 **Причины ошибок:**\n"
                    )
                    
                    # Добавляем детали ошибок
                    failed_details = [detail for detail in stats['details'] if detail['status'] == 'failed']
                    for detail in failed_details[:5]:  # Показываем первые 5 ошибок
                        message_text += f"• {detail['user']}: {detail['reason']}\n"
                    
                    if len(failed_details) > 5:
                        message_text += f"... и еще {len(failed_details) - 5} ошибок\n"
                    
                    self.message_user(request, message_text, messages.WARNING)
                else:
                    self.message_user(
                        request,
                        f"✅ Сообщение успешно отправлено всем {stats['success']} пользователям!",
                        messages.SUCCESS
                    )
            except Exception as e:
                # Логируем ошибку
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Ошибка отправки сообщений: {e}")
                self.message_user(
                    request,
                    f"❌ Произошла ошибка при отправке сообщений: {str(e)}",
                    messages.ERROR
                )
            
            return redirect('admin:teachers_telegramuser_changelist')
        
        # Показываем форму выбора
        return render(request, 'admin/send_broadcast.html', {
            'users': users_list,
            'title': 'Отправить сообщение пользователям',
            'stats': admin_telegram_service.get_user_status_info()
        })
    
    send_broadcast_message.short_description = "📤 Отправить сообщение выбранным пользователям"
    
    def send_to_django_users(self, request, queryset):
        """Отправить сообщение Django пользователям через Telegram"""
        # Преобразуем queryset в список сразу
        users_list = list(queryset) if queryset else []
        
        # Проверяем, что есть выбранные пользователи
        if not users_list:
            self.message_user(
                request,
                "❌ Не выбрано ни одного пользователя",
                messages.ERROR
            )
            return redirect('admin:teachers_telegramuser_changelist')
        
        # Получаем Django пользователей из выбранных Telegram пользователей
        django_users = []
        telegram_users_with_django = []
        for tg_user in users_list:
            if tg_user.user:
                django_users.append(tg_user.user)
                telegram_users_with_django.append(tg_user)
        
        if 'apply' in request.POST:
            message = request.POST.get('message')
            
            if not message:
                self.message_user(
                    request,
                    "❌ Пожалуйста, введите текст сообщения",
                    messages.ERROR
                )
                # Показываем форму снова
                return render(request, 'admin/send_broadcast.html', {
                    'users': telegram_users_with_django,
                    'title': 'Отправить сообщение Django пользователям',
                    'stats': admin_telegram_service.get_user_status_info(),
                    'django_mode': True
                })
            
            if not django_users:
                self.message_user(
                    request,
                    "❌ Среди выбранных пользователей нет привязанных к Django аккаунтам",
                    messages.ERROR
                )
                return redirect('admin:teachers_telegramuser_changelist')
            
            # Отправляем сообщения Django пользователям
            stats = {'success': 0, 'failed': 0, 'total': len(django_users), 'details': []}
            
            for django_user in django_users:
                success = admin_telegram_service.send_to_django_user(
                    django_user=django_user,
                    message=message
                )
                
                if success:
                    stats['success'] += 1
                    stats['details'].append({
                        'user': f"{django_user.username} ({django_user.get_full_name() or 'нет имени'})",
                        'status': 'success',
                        'reason': 'Отправлено успешно'
                    })
                else:
                    stats['failed'] += 1
                    stats['details'].append({
                        'user': f"{django_user.username} ({django_user.get_full_name() or 'нет имени'})",
                        'status': 'failed',
                        'reason': 'Не найден Telegram пользователь или не готов к получению'
                    })
            
            # Формируем сообщение с результатами
            if stats['failed'] > 0:
                message_text = (
                    f"📊 **Результаты отправки Django пользователям:**\n"
                    f"✅ Успешно: {stats['success']}\n"
                    f"❌ Ошибок: {stats['failed']}\n"
                    f"📊 Всего: {stats['total']}\n\n"
                    f"💡 **Причины ошибок:**\n"
                )
                
                failed_details = [detail for detail in stats['details'] if detail['status'] == 'failed']
                for detail in failed_details[:5]:
                    message_text += f"• {detail['user']}: {detail['reason']}\n"
                
                if len(failed_details) > 5:
                    message_text += f"... и еще {len(failed_details) - 5} ошибок\n"
                
                self.message_user(request, message_text, messages.WARNING)
            else:
                self.message_user(
                    request,
                    f"✅ Сообщение успешно отправлено всем {stats['success']} Django пользователям!",
                    messages.SUCCESS
                )
            
            return redirect('admin:teachers_telegramuser_changelist')
        
        # Показываем форму только если есть пользователи с Django аккаунтами
        if not telegram_users_with_django:
            self.message_user(
                request,
                "❌ Среди выбранных пользователей нет привязанных к Django аккаунтам",
                messages.WARNING
            )
            return redirect('admin:teachers_telegramuser_changelist')
        
        return render(request, 'admin/send_broadcast.html', {
            'users': telegram_users_with_django,
            'title': 'Отправить сообщение Django пользователям',
            'stats': admin_telegram_service.get_user_status_info(),
            'django_mode': True
        })
    
    send_to_django_users.short_description = "👤 Отправить сообщение Django пользователям"
    
    # Добавляем кнопку "Отправить всем"
    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('send-all/', self.admin_site.admin_view(self.send_all_view), 
                 name='send_broadcast_all'),
        ]
        return custom_urls + urls
    
    def send_all_view(self, request):
        """Форма для отправки сообщения всем"""
        if request.method == 'POST':
            message = request.POST.get('message')
            user_type = request.POST.get('user_type', 'all')
            
            if not message:
                self.message_user(
                    request,
                    "❌ Пожалуйста, введите текст сообщения",
                    messages.ERROR
                )
                return redirect('..')
            
            # Используем новый сервис для массовой рассылки
            stats = admin_telegram_service.send_to_all_started_users(
                message=message,
                user_type=user_type if user_type != 'all' else None
            )
            
            # Формируем сообщение с результатами
            if stats['failed'] > 0:
                message_text = (
                    f"📊 **Результаты массовой рассылки:**\n"
                    f"✅ Успешно: {stats['success']}\n"
                    f"❌ Ошибок: {stats['failed']}\n"
                    f"📊 Всего: {stats['total']}\n\n"
                    f"💡 **Причины ошибок:**\n"
                )
                
                # Добавляем детали ошибок
                failed_details = [detail for detail in stats['details'] if detail['status'] == 'failed']
                for detail in failed_details[:5]:  # Показываем первые 5 ошибок
                    message_text += f"• {detail['user']}: {detail['reason']}\n"
                
                if len(failed_details) > 5:
                    message_text += f"... и еще {len(failed_details) - 5} ошибок\n"
                
                self.message_user(request, message_text, messages.WARNING)
            else:
                self.message_user(
                    request,
                    f"✅ Массовая рассылка завершена успешно!\n📊 Отправлено: {stats['success']} пользователям",
                    messages.SUCCESS
                )
            
            return redirect('..')
        
        # Получаем статистику для отображения
        stats = admin_telegram_service.get_user_status_info()
        
        context = {
            'title': 'Массовая рассылка',
            'opts': self.model._meta,
            'stats': stats
        }
        return render(request, 'admin/send_broadcast_all.html', context)
    
    # Используем кастомный шаблон для списка
    change_list_template = "admin/telegram_user_changelist.html"
    
    def changelist_view(self, request, extra_context=None):
        """
        Переопределяем для совместимости с Python 3.14
        """
        extra_context = extra_context or {}
        extra_context['show_broadcast_button'] = True
        return super().changelist_view(request, extra_context=extra_context)


# --- NotificationQueue (Очередь уведомлений) ---
class NotificationLogInline(admin.TabularInline):
    model = NotificationLog
    extra = 0
    readonly_fields = ('attempt_number', 'status', 'error_message', 'telegram_message_id', 'processing_time_ms', 'timestamp')
    can_delete = False
    
    def has_add_permission(self, request, obj=None):
        return False


@admin.register(NotificationQueue)
class NotificationQueueAdmin(admin.ModelAdmin):
    list_display = ('short_id', 'recipient', 'notification_type', 'status', 'retry_count', 'scheduled_at', 'created_at')
    list_filter = ('status', 'notification_type', 'created_at')
    search_fields = ('recipient__username', 'recipient__email', 'title', 'message')
    readonly_fields = ('id', 'idempotency_key', 'created_at', 'sent_at', 'processing_started_at')
    inlines = [NotificationLogInline]
    list_per_page = 50
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('id', 'recipient', 'notification_type', 'status', 'idempotency_key')
        }),
        ('Содержимое', {
            'fields': ('title', 'message', 'data')
        }),
        ('Настройки retry', {
            'fields': ('retry_count', 'max_retries', 'last_error')
        }),
        ('Временные метки', {
            'fields': ('scheduled_at', 'created_at', 'processing_started_at', 'sent_at')
        }),
    )
    
    actions = ['resend_failed', 'cancel_pending', 'force_process']
    
    def short_id(self, obj):
        """Показать короткую версию UUID"""
        return str(obj.id)[:8]
    short_id.short_description = 'ID'
    
    def resend_failed(self, request, queryset):
        """Повторить отправку failed уведомлений"""
        count = 0
        for notification in queryset.filter(status='failed'):
            if notification.can_retry():
                notification.status = 'pending'
                notification.scheduled_at = timezone.now()
                notification.save()
                count += 1
        self.message_user(request, f'✅ Запланировано повторно: {count} уведомлений', messages.SUCCESS)
    resend_failed.short_description = '🔄 Повторить отправку failed уведомлений'
    
    def cancel_pending(self, request, queryset):
        """Отменить pending уведомления"""
        count = queryset.filter(status='pending').update(
            status='cancelled',
            last_error='Отменено администратором'
        )
        self.message_user(request, f'❌ Отменено: {count} уведомлений', messages.WARNING)
    cancel_pending.short_description = '❌ Отменить pending уведомления'
    
    def force_process(self, request, queryset):
        """Принудительно обработать уведомления"""
        from telegram_bot.notification_service import notification_service
        import asyncio
        
        notifications = queryset.filter(status='pending')
        count = 0
        
        for notification in notifications:
            notification.status = 'pending'
            notification.scheduled_at = timezone.now()
            notification.save()
            count += 1
        
        # Обработать немедленно
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        sent = loop.run_until_complete(
            notification_service.process_queue_batch(batch_size=count)
        )
        
        self.message_user(
            request,
            f'🚀 Обработано: {sent}/{count} уведомлений',
            messages.SUCCESS if sent == count else messages.WARNING
        )
    force_process.short_description = '🚀 Обработать немедленно'


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ('short_notification_id', 'attempt_number', 'status', 'processing_time_ms', 'timestamp')
    list_filter = ('status', 'timestamp')
    search_fields = ('notification__id', 'error_message')
    readonly_fields = ('notification', 'attempt_number', 'status', 'error_message', 'telegram_message_id', 'response_data', 'processing_time_ms', 'timestamp')
    list_per_page = 100
    
    def short_notification_id(self, obj):
        """Показать короткую версию UUID"""
        return str(obj.notification.id)[:8]
    short_notification_id.short_description = 'Notification ID'
    
    def has_add_permission(self, request):
        return False
    
    def has_delete_permission(self, request, obj=None):
        # Разрешаем массовое удаление старых логов
        return True