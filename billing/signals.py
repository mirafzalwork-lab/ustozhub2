from django.conf import settings
from django.db.models import F
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_wallet_for_user(sender, instance, created, **kwargs):
    """При создании User автоматически создаём ему кошелёк."""
    if not created:
        return
    from .models import Wallet
    Wallet.objects.get_or_create(user=instance)


# ---- Booking → Subscription counter sync ---------------------------------
#
# Когда Booking переходит в «доставленный» статус (completed или no_show_student
# — ученик не пришёл, но урок засчитан) и привязан к подписке, инкрементируем
# Subscription.completed_lessons (для UI прогресса / остатка пакета).
# Финансовая выплата (lessons_paid_out) делается отдельно — в Celery task
# release_pending_payouts по истечении PAYOUT_GRACE_HOURS.

DELIVERED_STATUSES = ('completed', 'no_show_student')

def _connect_booking_signals():
    """Регистрируем сигналы Booking лениво, чтобы избежать circular import."""
    from teachers.models import Booking

    @receiver(pre_save, sender=Booking, dispatch_uid='billing.track_booking_status')
    def _track_status(sender, instance, **kwargs):
        if instance.pk:
            try:
                prev = Booking.objects.only('status').get(pk=instance.pk)
                instance._prev_status = prev.status
            except Booking.DoesNotExist:
                instance._prev_status = None
        else:
            instance._prev_status = None

    @receiver(post_save, sender=Booking, dispatch_uid='billing.on_booking_completed')
    def _on_completed(sender, instance, created, **kwargs):
        if created:
            return
        prev = getattr(instance, '_prev_status', None)
        # Считаем урок потреблённым при переходе в любой доставленный статус
        # (completed / no_show_student), но один раз — не пере-инкрементим при
        # переходе completed → no_show_student и наоборот.
        if prev in DELIVERED_STATUSES or instance.status not in DELIVERED_STATUSES:
            return
        if not instance.subscription_id:
            return
        # Атомарно инкрементим счётчик подписки.
        from .models import Subscription
        Subscription.objects.filter(pk=instance.subscription_id).update(
            completed_lessons=F('completed_lessons') + 1,
        )


_connect_booking_signals()
