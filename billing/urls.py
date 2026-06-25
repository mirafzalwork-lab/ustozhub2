from django.urls import path

from . import admin_views, views

urlpatterns = [
    # Admin billing dashboard
    path('admin-dashboard/billing/', admin_views.billing_hub, name='admin_billing_hub'),
    path('admin-dashboard/billing/reports/', admin_views.billing_reports, name='admin_billing_reports'),
    path('admin-dashboard/billing/income/', admin_views.billing_income, name='admin_billing_income'),
    path('admin-dashboard/billing/wallets/', admin_views.wallet_search, name='admin_billing_wallets'),
    path('admin-dashboard/billing/wallets/<int:user_id>/topup/', admin_views.wallet_topup_action, name='admin_wallet_topup_action'),
    path('admin-dashboard/billing/withdrawals/', admin_views.withdrawals_manage, name='admin_billing_withdrawals'),
    path('admin-dashboard/billing/withdrawals/<uuid:wr_id>/action/', admin_views.withdrawal_action, name='admin_withdrawal_action'),
    path('admin-dashboard/billing/subscriptions/', admin_views.subscriptions_manage, name='admin_billing_subscriptions'),
    path('admin-dashboard/billing/subscriptions/<uuid:sub_id>/cancel/', admin_views.subscription_admin_cancel, name='admin_subscription_cancel'),
    path('admin-dashboard/billing/trials/', admin_views.trial_lessons_manage, name='admin_billing_trials'),
    path('admin-dashboard/billing/disputes/', admin_views.disputes_manage, name='admin_billing_disputes'),
    path('admin-dashboard/billing/disputes/<uuid:dispute_id>/action/', admin_views.dispute_action, name='admin_dispute_action'),

    # Тарифы — управление учителем
    path('profile/tariffs/', views.tariffs_list, name='tariffs_list'),
    path('profile/tariffs/new/', views.tariff_create, name='tariff_create'),
    path('profile/tariffs/<int:pk>/edit/', views.tariff_edit, name='tariff_edit'),
    path('profile/tariffs/<int:pk>/delete/', views.tariff_delete, name='tariff_delete'),
    path('profile/tariffs/<int:pk>/toggle/', views.tariff_toggle_active, name='tariff_toggle_active'),

    # Wallet — пополнение
    path('my/wallet/topup/', views.wallet_topup_request, name='wallet_topup_request'),
    # Онлайн-пополнение через Multicard (callback зарегистрирован в core/urls.py вне i18n)
    path('my/wallet/topup/multicard/', views.wallet_topup_multicard, name='wallet_topup_multicard'),
    path('my/wallet/topup/return/', views.wallet_topup_return, name='wallet_topup_return'),

    # Покупка подписки + история
    path('subscriptions/buy/<int:tariff_id>/', views.subscription_buy, name='subscription_buy'),
    path('subscriptions/<uuid:sub_id>/cancel/', views.subscription_cancel, name='subscription_cancel'),
    path('subscriptions/<uuid:sub_id>/pause/', views.subscription_pause, name='subscription_pause'),
    path('subscriptions/<uuid:sub_id>/resume/', views.subscription_resume, name='subscription_resume'),
    path('my/subscriptions/', views.my_subscriptions, name='my_subscriptions'),
    path('profile/subscribers/', views.teacher_subscribers, name='teacher_subscribers'),

    # ТЗ flow: заявка → одобрение → оплата → бронь
    path('learn/<int:teacher_id>/continue/', views.continue_learning, name='continue_learning'),
    path('learn/<int:teacher_id>/dismiss/', views.dismiss_trial_suggestion, name='dismiss_trial_suggestion'),
    path('profile/learning-requests/', views.teacher_learning_requests, name='teacher_learning_requests'),
    path('learning-requests/<uuid:sub_id>/action/', views.learning_request_action, name='learning_request_action'),
    path('subscriptions/<uuid:sub_id>/pay/', views.subscription_pay, name='subscription_pay'),
    path('subscriptions/<uuid:sub_id>/schedule/', views.subscription_schedule, name='subscription_schedule'),

    # Споры (ТЗ шаг 8)
    path('lessons/<uuid:booking_id>/dispute/', views.dispute_open, name='dispute_open'),
    path('disputes/<uuid:dispute_id>/cancel/', views.dispute_cancel, name='dispute_cancel'),

    # Вывод средств
    path('profile/withdrawals/', views.withdrawals_list, name='withdrawals_list'),
    path('profile/withdrawals/<uuid:wr_id>/cancel/', views.withdrawal_cancel, name='withdrawal_cancel'),

    # Homework (LMS, Phase 8)
    path('profile/homework/', views.teacher_homework_list, name='teacher_homework_list'),
    path('profile/homework/new/', views.teacher_homework_create, name='teacher_homework_create'),
    path('my/homework/', views.student_homework_list, name='student_homework_list'),
    path('homework/<uuid:hw_id>/', views.homework_detail, name='homework_detail'),

    # Progress (Phase 9)
    path('my/progress/', views.my_progress, name='my_progress'),
    path('profile/student-progress/<uuid:sub_id>/', views.teacher_student_progress, name='teacher_student_progress'),

    # Dashboards (Phase 10)
    path('dashboard/', views.dashboard, name='dashboard'),
]
