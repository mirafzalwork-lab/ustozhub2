from django.urls import path
from .views import (
    home, detail, student_detail, teacher_register_step1, teacher_register_step2, 
    teacher_register_step3, teacher_register_complete, profile_view, profile_edit, 
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
    # Platform messages
    platform_messages_management, create_platform_message, toggle_platform_message,
    platform_messages_list, platform_message_detail, mark_platform_message_read
)
from .telegram_views import (
    telegram_auth, link_telegram_account, telegram_status, toggle_notifications
)

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
    
    # Platform Messages management
    path('admin-dashboard/platform-messages/', platform_messages_management, name='platform_messages_management'),
    path('admin-dashboard/platform-messages/create/', create_platform_message, name='create_platform_message'),
    path('admin-dashboard/platform-messages/<int:message_id>/toggle/', toggle_platform_message, name='toggle_platform_message'),
    
    # Platform Messages for users
    path('notifications/', platform_messages_list, name='platform_messages_list'),
    path('notifications/<int:message_id>/', platform_message_detail, name='platform_message_detail'),
    path('api/platform-messages/<int:message_id>/read/', mark_platform_message_read, name='mark_platform_message_read'),

    path('register/', teacher_register_step1, name='teacher_register_step1'),
    path('register/step2/', teacher_register_step2, name='teacher_register_step2'),
    path('register/step3/', teacher_register_step3, name='teacher_register_step3'),
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
    
    # Messages / Сообщения
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
]
