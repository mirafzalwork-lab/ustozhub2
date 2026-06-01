from django.urls import path

from . import admin_views, views

urlpatterns = [
    # Admin billing dashboard
    path('admin-dashboard/billing/', admin_views.billing_hub, name='admin_billing_hub'),
    path('admin-dashboard/billing/wallets/', admin_views.wallet_search, name='admin_billing_wallets'),
    path('admin-dashboard/billing/wallets/<int:user_id>/topup/', admin_views.wallet_topup_action, name='admin_wallet_topup_action'),
    path('admin-dashboard/billing/withdrawals/', admin_views.withdrawals_manage, name='admin_billing_withdrawals'),
    path('admin-dashboard/billing/withdrawals/<uuid:wr_id>/action/', admin_views.withdrawal_action, name='admin_withdrawal_action'),
    path('admin-dashboard/billing/subscriptions/', admin_views.subscriptions_manage, name='admin_billing_subscriptions'),
    path('admin-dashboard/billing/subscriptions/<uuid:sub_id>/cancel/', admin_views.subscription_admin_cancel, name='admin_subscription_cancel'),

    # Тарифы — управление учителем
    path('profile/tariffs/', views.tariffs_list, name='tariffs_list'),
    path('profile/tariffs/new/', views.tariff_create, name='tariff_create'),
    path('profile/tariffs/<int:pk>/edit/', views.tariff_edit, name='tariff_edit'),
    path('profile/tariffs/<int:pk>/delete/', views.tariff_delete, name='tariff_delete'),
    path('profile/tariffs/<int:pk>/toggle/', views.tariff_toggle_active, name='tariff_toggle_active'),

    # Wallet — пополнение (manual flow до Payme/Click)
    path('my/wallet/topup/', views.wallet_topup_request, name='wallet_topup_request'),

    # Покупка подписки + история
    path('subscriptions/buy/<int:tariff_id>/', views.subscription_buy, name='subscription_buy'),
    path('subscriptions/<uuid:sub_id>/cancel/', views.subscription_cancel, name='subscription_cancel'),
    path('my/subscriptions/', views.my_subscriptions, name='my_subscriptions'),
    path('profile/subscribers/', views.teacher_subscribers, name='teacher_subscribers'),

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
