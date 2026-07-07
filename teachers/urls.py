from django.urls import path
from allauth.socialaccount.providers.google.views import oauth2_login as google_login_view
from .views import (
    home, detail, student_detail,
    profile_view, profile_edit,
    login_view, students_list, logout_view, register_choose, register_student,
    teacher_profile_edit, student_profile_edit, toggle_profile_status,
    toggle_favorite_teacher, toggle_favorite_student,
    lead_opt_out, potential_students,
    my_favorite_teachers, my_favorite_students,
    student_suggestions,
    conversations_list, conversation_detail, start_conversation,
    send_message_ajax, mark_messages_read, delete_conversation,
    delete_teacher_subject, delete_teacher, admin_teachers, admin_dashboard,
    # API для поиска предметов
    subjects_autocomplete, subjects_popular, subjects_categories, subjects_by_category,
    # Telegram & admin messages
    telegram_management, send_broadcast_message, send_individual_message, export_telegram_users, messages_management,
    admin_toggle_telegram_notifications, admin_conversation_detail,
    # Daily reminders (admin-dashboard)
    daily_reminders_list, daily_reminder_edit, daily_reminder_delete,
    daily_reminder_toggle, daily_reminder_test,
    # Notifications / Уведомления
    notifications_list, notification_detail, mark_notification_read,
    mark_all_notifications_read, notifications_dropdown,
    # Badge counts API
    badge_counts,
    # Google OAuth2 role completion
    google_complete_student, google_complete_teacher, google_student_onboarding,
    # Legal pages
    privacy_view, terms_view,
)
from .telegram_views import (
    telegram_auth, link_telegram_account, telegram_status, toggle_notifications
)
from .registration_wizard import TeacherRegistrationWizard, teacher_register_complete
from .video_views import video_presigned_url, video_presigned_url_register, video_save, video_delete
from .booking_views import (
    teacher_calendar, slots_list_api, slots_create_api, slots_detail_api,
    slots_bulk_generate_api, slots_bulk_delete_api,
    public_teacher_slots,
    booking_create_api, booking_cancel_api,
    booking_confirm_api, booking_reject_api,
    booking_set_meeting_url_api, booking_reschedule_api,
    booking_report_teacher_noshow_api,
    my_bookings_api, my_bookings_page, book_teacher_page,
    lesson_room, lesson_archive, lesson_attendance_api, lesson_diag_api, leave_review, booking_ical,
)
from .lesson_files_views import (
    lesson_file_list, lesson_file_presigned_url, lesson_file_save, lesson_file_delete,
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
    path('admin-dashboard/teachers/', admin_teachers, name='admin_teachers'),
    path('admin-dashboard/teacher/<int:teacher_id>/delete/', delete_teacher, name='admin_delete_teacher'),

    # Telegram Management
    path('admin-dashboard/telegram/', telegram_management, name='telegram_management'),
    path('admin-dashboard/telegram/broadcast/', send_broadcast_message, name='send_broadcast_message'),
    path('admin-dashboard/telegram/individual/', send_individual_message, name='send_individual_message'),
    path('admin-dashboard/telegram/export/', export_telegram_users, name='export_telegram_users'),
    path('api/admin/telegram/toggle-notifications/<int:user_id>/', admin_toggle_telegram_notifications, name='admin_toggle_telegram_notifications'),

    # Admin Messages management
    path('admin-dashboard/messages/', messages_management, name='admin_messages'),
    path('admin-dashboard/conversation/<uuid:conversation_id>/', admin_conversation_detail, name='admin_conversation_detail'),

    # Daily reminder templates (ежедневная авто-рассылка)
    path('admin-dashboard/reminders/', daily_reminders_list, name='daily_reminders_list'),
    path('admin-dashboard/reminders/new/', daily_reminder_edit, name='daily_reminder_create'),
    path('admin-dashboard/reminders/<int:template_id>/edit/', daily_reminder_edit, name='daily_reminder_edit'),
    path('admin-dashboard/reminders/<int:template_id>/delete/', daily_reminder_delete, name='daily_reminder_delete'),
    path('admin-dashboard/reminders/<int:template_id>/toggle/', daily_reminder_toggle, name='daily_reminder_toggle'),
    path('admin-dashboard/reminders/<int:template_id>/test/', daily_reminder_test, name='daily_reminder_test'),

    # New Multi-Step Teacher Registration Wizard.
    # Для Google-регистрации шаг account_security полностью скрыт (email/пароль уже
    # есть от Google) — иначе пользователь видел бы пустой шаг и просто жал «Далее».
    path('register/', TeacherRegistrationWizard.as_view(
        condition_dict={
            'account_security': lambda w: not w.request.session.get('is_google_teacher', False),
        },
    ), name='teacher_register'),
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
    path('leads/', potential_students, name='potential_students'),
    path('api/favorites/toggle/<int:teacher_id>/', toggle_favorite_teacher, name='toggle_favorite_teacher'),
    path('api/favorites/student/toggle/<int:student_id>/', toggle_favorite_student, name='toggle_favorite_student'),
    path('api/leads/opt-out/<int:teacher_id>/', lead_opt_out, name='lead_opt_out'),
    
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

    # Video API / Видео-визитка учителя
    path('api/video/presigned-url/', video_presigned_url, name='video_presigned_url'),
    path('api/video/presigned-url/register/', video_presigned_url_register, name='video_presigned_url_register'),
    path('api/video/save/', video_save, name='video_save'),
    path('api/video/delete/', video_delete, name='video_delete'),

    # Booking / Calendar (Phase 2)
    path('teacher/calendar/', teacher_calendar, name='teacher_calendar'),
    path('api/calendar/slots/', slots_list_api, name='slots_list_api'),
    path('api/calendar/slots/create/', slots_create_api, name='slots_create_api'),
    path('api/calendar/slots/<int:slot_id>/', slots_detail_api, name='slots_detail_api'),
    path('api/calendar/slots/bulk-generate/', slots_bulk_generate_api, name='slots_bulk_generate_api'),
    path('api/calendar/slots/bulk-delete/', slots_bulk_delete_api, name='slots_bulk_delete_api'),

    # Booking flow ученика (Phase 3)
    path('teacher/<int:teacher_id>/book/', book_teacher_page, name='book_teacher_page'),
    path('api/teachers/<int:teacher_id>/slots/', public_teacher_slots, name='public_teacher_slots'),
    path('api/bookings/create/', booking_create_api, name='booking_create_api'),
    path('api/bookings/<uuid:booking_id>/confirm/', booking_confirm_api, name='booking_confirm_api'),
    path('api/bookings/<uuid:booking_id>/reject/', booking_reject_api, name='booking_reject_api'),
    path('api/bookings/<uuid:booking_id>/cancel/', booking_cancel_api, name='booking_cancel_api'),
    path('api/bookings/<uuid:booking_id>/meeting-url/', booking_set_meeting_url_api, name='booking_set_meeting_url_api'),
    path('api/bookings/<uuid:booking_id>/reschedule/', booking_reschedule_api, name='booking_reschedule_api'),
    path('api/bookings/<uuid:booking_id>/report-teacher-noshow/', booking_report_teacher_noshow_api, name='booking_report_teacher_noshow_api'),
    path('api/bookings/my/', my_bookings_api, name='my_bookings_api'),
    path('my/bookings/', my_bookings_page, name='my_bookings_page'),
    path('bookings/<uuid:booking_id>/calendar.ics', booking_ical, name='booking_ical'),
    path('lesson/<uuid:booking_id>/', lesson_room, name='lesson_room'),
    path('lesson/<uuid:booking_id>/archive/', lesson_archive, name='lesson_archive'),
    path('lesson/<uuid:booking_id>/attendance/', lesson_attendance_api, name='lesson_attendance_api'),
    path('lesson/<uuid:booking_id>/diag/', lesson_diag_api, name='lesson_diag_api'),
    # Материалы урока (LessonFile)
    path('lesson/<uuid:booking_id>/files/', lesson_file_list, name='lesson_file_list'),
    path('lesson/<uuid:booking_id>/files/presign/', lesson_file_presigned_url, name='lesson_file_presign'),
    path('lesson/<uuid:booking_id>/files/save/', lesson_file_save, name='lesson_file_save'),
    path('lesson/<uuid:booking_id>/files/<int:file_id>/delete/', lesson_file_delete, name='lesson_file_delete'),
    path('review/<uuid:booking_id>/', leave_review, name='leave_review'),

    # Legal pages (footer links)
    path('privacy/', privacy_view, name='privacy'),
    path('terms/', terms_view, name='terms'),

    # Google OAuth2
    path('auth/google/', google_login_view, name='google_login'),
    path('google/complete/student/', google_complete_student, name='google_complete_student'),
    path('google/complete/teacher/', google_complete_teacher, name='google_complete_teacher'),
    path('google/onboarding/student/', google_student_onboarding, name='google_student_onboarding'),
]
