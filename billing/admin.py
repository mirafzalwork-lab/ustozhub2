import uuid
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import path, reverse

from .models import (
    Homework, HomeworkAttachment, HomeworkSubmission, HomeworkSubmissionFile,
    Subscription, Tariff, Transaction, Wallet, WithdrawalRequest,
)
from .services import WalletService, WithdrawalError, WithdrawalService


class WalletTopUpForm(forms.Form):
    """Форма ручного пополнения кошелька — для тестов до интеграции платежки."""
    amount = forms.DecimalField(
        min_value=Decimal('0.01'), max_digits=14, decimal_places=2,
        label='Сумма пополнения (сум)',
        widget=forms.NumberInput(attrs={'step': '1000', 'autofocus': True}),
    )
    reason = forms.CharField(
        max_length=200, required=False,
        label='Комментарий (например: «manual top-up для тестов»)',
    )


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'balance', 'currency', 'last_transaction_at', 'updated_at')
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('balance', 'last_transaction_at', 'created_at', 'updated_at')
    list_select_related = ('user',)
    change_form_template = 'admin/billing/wallet_change_form.html'

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                '<int:wallet_id>/topup/',
                self.admin_site.admin_view(self.topup_view),
                name='billing_wallet_topup',
            ),
        ]
        return custom + urls

    def topup_view(self, request, wallet_id):
        wallet = get_object_or_404(Wallet, pk=wallet_id)
        if request.method == 'POST':
            form = WalletTopUpForm(request.POST)
            if form.is_valid():
                amount = form.cleaned_data['amount']
                reason = form.cleaned_data['reason'] or 'admin manual top-up'
                WalletService.credit(
                    user=wallet.user,
                    amount=amount,
                    tx_type=Transaction.Type.DEPOSIT,
                    idempotency_key=f'admin-topup:{uuid.uuid4()}',
                    description=f'[admin {request.user.username}] {reason}',
                )
                self.message_user(
                    request,
                    f'Кошелёк {wallet.user.username} пополнен на {amount} {wallet.currency}.',
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(
                    reverse('admin:billing_wallet_change', args=[wallet.pk])
                )
        else:
            form = WalletTopUpForm()

        return render(request, 'admin/billing/wallet_topup.html', {
            'form': form,
            'wallet': wallet,
            'opts': self.model._meta,
            'title': f'Пополнить кошелёк {wallet.user.username}',
        })


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('id', 'wallet', 'amount', 'balance_after', 'type', 'status', 'created_at')
    list_filter = ('type', 'status')
    search_fields = ('idempotency_key', 'reference', 'wallet__user__username')
    readonly_fields = tuple(f.name for f in Transaction._meta.fields)
    list_select_related = ('wallet', 'wallet__user')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'teacher', 'subject', 'name', 'lessons_per_week',
        'lesson_duration_minutes', 'duration_months', 'price_per_month',
        'total_lessons_display', 'is_active', 'is_recommended',
    )
    list_filter = ('is_active', 'is_recommended', 'lessons_per_week', 'duration_months')
    search_fields = ('name', 'teacher__user__username', 'subject__name')
    list_select_related = ('teacher__user', 'subject')
    readonly_fields = ('created_at', 'updated_at')

    @admin.display(description='Уроков всего')
    def total_lessons_display(self, obj):
        return obj.total_lessons


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'student', 'teacher', 'subject', 'status',
        'completed_lessons', 'total_lessons', 'escrow_balance',
        'started_at', 'expires_at',
    )
    list_filter = ('status',)
    search_fields = (
        'student__username', 'student__email',
        'teacher__user__username', 'subject__name',
    )
    list_select_related = ('student', 'teacher__user', 'subject', 'tariff')
    readonly_fields = (
        'id', 'lessons_per_week', 'lesson_duration_minutes', 'duration_months',
        'total_lessons', 'price_total', 'price_per_lesson', 'commission_rate',
        'escrow_balance', 'completed_lessons', 'lessons_paid_out',
        'started_at', 'expires_at', 'cancelled_at',
        'purchase_idempotency_key', 'created_at', 'updated_at',
    )
    date_hierarchy = 'created_at'

    def has_add_permission(self, request) -> bool:
        # Создаём подписки только через SubscriptionService.purchase()
        return False


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = (
        'id', 'user', 'amount', 'status', 'payout_method',
        'payout_details_short', 'created_at', 'reviewed_at',
    )
    list_filter = ('status', 'payout_method')
    search_fields = ('user__username', 'user__email', 'payout_details', 'comment')
    list_select_related = ('user', 'reviewed_by')
    readonly_fields = (
        'id', 'idempotency_key', 'created_at', 'updated_at',
        'reviewed_at', 'reviewed_by', 'completed_at', 'cancelled_at',
    )
    actions = ['action_approve', 'action_complete', 'action_reject']
    date_hierarchy = 'created_at'

    @admin.display(description='Реквизиты')
    def payout_details_short(self, obj):
        if not obj.payout_details:
            return '—'
        s = obj.payout_details
        return (s[:6] + '…' + s[-4:]) if len(s) > 14 else s

    def has_add_permission(self, request) -> bool:
        return False  # Только через UI учителя.

    def _process(self, request, queryset, action_fn, success_msg, *, require_note=False):
        note = (request.POST.get('admin_note') or '').strip()
        if require_note and not note:
            self.message_user(
                request,
                'Для отклонения нужно указать причину в поле «admin_note» (через "Изменить" на странице заявки).',
                level=messages.ERROR,
            )
            return
        ok, fail = 0, 0
        for wr in queryset:
            try:
                action_fn(wr, admin_user=request.user, note=note)
                ok += 1
            except WithdrawalError as e:
                fail += 1
                self.message_user(request, f'#{wr.id}: {e}', level=messages.WARNING)
        if ok:
            self.message_user(request, f'{success_msg}: {ok}.', level=messages.SUCCESS)

    @admin.action(description='✓ Одобрить (status → approved)')
    def action_approve(self, request, queryset):
        self._process(request, queryset, WithdrawalService.approve, 'Одобрено')

    @admin.action(description='✓✓ Завершить — перевод сделан (status → completed)')
    def action_complete(self, request, queryset):
        self._process(request, queryset, WithdrawalService.complete, 'Завершено')

    @admin.action(description='✗ Отклонить с возвратом средств')
    def action_reject(self, request, queryset):
        # Reject требует note — берём из admin_note в самой записи (для bulk без поля).
        # Простой UX: админ сначала открывает заявку, проставляет admin_note, потом action.
        ok, fail = 0, 0
        for wr in queryset:
            try:
                WithdrawalService.reject(
                    wr, admin_user=request.user,
                    note=wr.admin_note or 'отклонено администратором',
                )
                ok += 1
            except WithdrawalError as e:
                fail += 1
                self.message_user(request, f'#{wr.id}: {e}', level=messages.WARNING)
        if ok:
            self.message_user(request, f'Отклонено: {ok} (средства возвращены).',
                              level=messages.SUCCESS)


class HomeworkAttachmentInline(admin.TabularInline):
    model = HomeworkAttachment
    extra = 0
    readonly_fields = ('filename', 'file_size', 'mime_type', 'uploaded_at')


class HomeworkSubmissionFileInline(admin.TabularInline):
    model = HomeworkSubmissionFile
    extra = 0
    readonly_fields = ('filename', 'file_size', 'mime_type', 'uploaded_at')


@admin.register(Homework)
class HomeworkAdmin(admin.ModelAdmin):
    list_display = ('id', 'title', 'teacher', 'student', 'status', 'due_at', 'created_at')
    list_filter = ('status',)
    search_fields = ('title', 'description', 'teacher__user__username', 'student__username')
    list_select_related = ('teacher__user', 'student', 'subscription__subject')
    readonly_fields = ('id', 'created_at', 'updated_at')
    inlines = [HomeworkAttachmentInline]
    date_hierarchy = 'created_at'


@admin.register(HomeworkSubmission)
class HomeworkSubmissionAdmin(admin.ModelAdmin):
    list_display = ('id', 'homework', 'student', 'grade', 'submitted_at', 'graded_at')
    search_fields = ('homework__title', 'student__username')
    list_select_related = ('homework', 'student')
    readonly_fields = ('id', 'submitted_at', 'updated_at')
    inlines = [HomeworkSubmissionFileInline]
