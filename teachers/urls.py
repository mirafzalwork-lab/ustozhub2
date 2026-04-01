from django.urls import path
from allauth.socialaccount.providers.google.views import oauth2_login as google_login_view
from .views import (
    home, detail, student_detail,
    profile_view, profile_edit,
    login_view, students_list, logout_view, register_choose, register_student,
    teacher_profile_edit, student_profile_edit, toggle_profile_status,
    toggle_favorite_teacher, toggle_favorite_student,
    my_favorite_teachers, my_favorite_students,
    student_suggestions,
    conversations_list, conversation_detail, start_conversation,
    send_message_ajax, mark_messages_read, delete_conversation,
    delete_teacher_subject, admin_dashboard,
    # API для поиска предметов
    subjects_autocomplete, subjects_popular, subjects_categories, subjects_by_category,
    # Telegram & admin messages
    telegram_management, send_broadcast_message, send_individual_message, export_telegram_users, messages_management,
    # Notifications / Уведомления
    notifications_list, notification_detail, mark_notification_read,
    mark_all_notifications_read, notifications_dropdown,
    # Badge counts API
    badge_counts,
    # Google OAuth2 role completion
    google_complete_student, google_complete_teacher, google_student_onboarding,
)
from .telegram_views import (
    telegram_auth, link_telegram_account, telegram_status, toggle_notifications
)
from .registration_wizard import TeacherRegistrationWizard, teacher_register_complete

urlpatterns = [
    path('', home, name='home'),

    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('register/choose/', register_choose, name='register_choose'),
    path('register/student/', register_student, name='register_student'),
    path('students/', students_list, name='students_list'),
    path('student/<int:id>/', student_detail, name='student_detail'),
    path('admin-dashboard/', admin_dashboard, name='admin_dashboard'),
    
    # Telegram Management
    path('admin-dashboard/telegram/', telegram_management, name='telegram_management'),
    path('admin-dashboard/telegram/broadcast/', send_broadcast_message, name='send_broadcast_message'),
    path('admin-dashboard/telegram/individual/', send_individual_message, name='send_individual_message'),
    path('admin-dashboard/telegram/export/', export_telegram_users, name='export_telegram_users'),

    # Admin Messages management
    path('admin-dashboard/messages/', messages_management, name='admin_messages'),

    # New Multi-Step Teacher Registration Wizard
    path('register/', TeacherRegistrationWizard.as_view(), name='teacher_register'),
    path('register/complete/', teacher_register_complete, name='teacher_register_complete'),
    
    path('student/suggestions/', student_suggestions, name='student_suggestions'),
    path('teacher/<int:id>/', detail, name='teacher_detail'),
    path('profile/', profile_view, name='profile'),
    path('profile/edit/', profile_edit, name='profile_edit'),
    path('profile/edit/teacher/', teacher_profile_edit, name='teacher_profile_edit'),
    path('profile/edit/student/', student_profile_edit, name='student_profile_edit'),
    path('profile/toggle-status/', toggle_profile_status, name='toggle_profile_status'),
    path('api/teacher/subject/<int:subject_id>/delete/', delete_teacher_subject, name='delete_teacher_subject'),
    
    # Favorites
    path('favorites/teachers/', my_favorite_teachers, name='my_favorite_teachers'),
    path('favorites/students/', my_favorite_students, name='my_favorite_students'),
    path('api/favorites/toggle/<int:teacher_id>/', toggle_favorite_teacher, name='toggle_favorite_teacher'),
    path('api/favorites/student/toggle/<int:student_id>/', toggle_favorite_student, name='toggle_favorite_student'),
    
    # Telegram API
    path('api/telegram/auth/', telegram_auth, name='telegram_auth'),
    path('api/telegram/link/', link_telegram_account, name='link_telegram_account'),
    path('api/telegram/status/', telegram_status, name='telegram_status'),
    path('api/telegram/notifications/toggle/', toggle_notifications, name='toggle_telegram_notifications'),
    
    # Messages / Сообщения (с интегрированным real-time чатом)
    path('messages/', conversations_list, name='conversations_list'),
    path('messages/<uuid:conversation_id>/', conversation_detail, name='conversation_detail'),
    path('messages/start/<int:user_id>/', start_conversation, name='start_conversation'),
    path('api/messages/<uuid:conversation_id>/send/', send_message_ajax, name='send_message_ajax'),
    path('api/messages/<uuid:conversation_id>/read/', mark_messages_read, name='mark_messages_read'),
    path('messages/<uuid:conversation_id>/delete/', delete_conversation, name='delete_conversation'),
    
    # Subjects API / API для поиска предметов
    path('api/subjects/autocomplete/', subjects_autocomplete, name='subjects_autocomplete'),
    path('api/subjects/popular/', subjects_popular, name='subjects_popular'),
    path('api/subjects/categories/', subjects_categories, name='subjects_categories'),
    path('api/subjects/category/<int:category_id>/', subjects_by_category, name='subjects_by_category'),
    
    # Notifications / Уведомления
    path('notifications/', notifications_list, name='notifications_list'),
    path('notifications/<int:notification_id>/', notification_detail, name='notification_detail'),
    path('notifications/<int:notification_id>/mark-read/', mark_notification_read, name='mark_notification_read'),
    path('notifications/mark-all-read/', mark_all_notifications_read, name='mark_all_notifications_read'),
    path('api/notifications/dropdown/', notifications_dropdown, name='notifications_dropdown'),

    # Badge counts API (для real-time обновления badge)
    path('api/badge-counts/', badge_counts, name='badge_counts'),

    # Google OAuth2
    path('auth/google/', google_login_view, name='google_login'),
    path('google/complete/student/', google_complete_student, name='google_complete_student'),
    path('google/complete/teacher/', google_complete_teacher, name='google_complete_teacher'),
    path('google/onboarding/student/', google_student_onboarding, name='google_student_onboarding'),
]
