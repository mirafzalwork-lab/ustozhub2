"""Тесты фиксов аудита 2026-06 (teachers-сторона).

Покрывают:
  * record_join — гонка/гейт по статусу (вход фиксируется только на confirmed);
  * анти-обход контактов — маскирование в сообщении брони, ответе учителя, отзыве;
  * mask_contacts — обходы ловятся, ложных срабатываний нет;
  * admin_conversation_detail — имперсонация только за участников беседы.
"""
from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.tests import (
    SIMPLE_STATIC_STORAGES,
    _make_student_with_balance,
    _make_teacher_with_subject,
)
from teachers.contact_filter import mask_contacts, mask_for_pair
from teachers.models import Booking, Conversation, Message, TimeSlot

User = get_user_model()


def _slot(teacher, *, in_hours=48, minutes=60):
    start = timezone.now() + timedelta(hours=in_hours)
    return TimeSlot.objects.create(
        teacher=teacher, start_at=start,
        end_at=start + timedelta(minutes=minutes), status='free',
    )


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class RecordJoinRaceTests(TestCase):
    """record_join фиксирует вход ТОЛЬКО на confirmed (гонка с settle/no-show)."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('rj_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('rj_s', balance=Decimal('0'))

    def _confirmed(self):
        slot = _slot(self.teacher)
        b = Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)
        b.confirm()
        b.refresh_from_db()
        return b

    def test_record_join_on_confirmed(self):
        b = self._confirmed()
        b.record_join(is_teacher=True)
        b.refresh_from_db()
        self.assertIsNotNone(b.teacher_joined_at)

    def test_record_join_idempotent(self):
        b = self._confirmed()
        b.record_join(is_teacher=True)
        first = b.teacher_joined_at
        b.record_join(is_teacher=True)
        b.refresh_from_db()
        self.assertEqual(b.teacher_joined_at, first)  # не перезаписан

    def test_record_join_noop_on_settled_booking(self):
        # Бронь уже отрасчётана как неявка учителя — вход фиксировать поздно.
        b = self._confirmed()
        Booking.objects.filter(pk=b.pk).update(status='no_show_teacher')
        b.refresh_from_db()
        b.record_join(is_teacher=True)
        b.refresh_from_db()
        self.assertIsNone(b.teacher_joined_at)  # не записан на settled


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES, CONTACT_MASK_MIN_PAID_LESSONS=5)
class ContactMaskingUnitTests(TestCase):
    """mask_contacts: обходы ловятся, легитимный текст не трогается."""

    def test_catches_bypasses(self):
        for txt in [
            'Звони +998 90 123 45 67',
            'мой телеграм ivan_teacher',
            'пиши в тг: @cool_user',
            'instagram super_teacher',
            'заходи на gmail.com',
            'discord.gg/xyz',
            '@username добавь',
        ]:
            _, did = mask_contacts(txt)
            self.assertTrue(did, f'не замаскировано: {txt}')

    def test_no_false_positives(self):
        for txt in [
            'Здравствуйте, хочу заниматься по вторникам',
            'Спасибо, всё понятно и т.д.',
            'Урок прошёл на 5.5 баллов',
            'Объясните тему 2.3 из учебника',
        ]:
            _, did = mask_contacts(txt)
            self.assertFalse(did, f'ложное срабатывание: {txt}')


class ContactMaskingBypassHardeningTests(TestCase):
    """Обходы через юникод-трюки и числительные прописью (#6)."""

    def test_unicode_bypasses_masked(self):
        for txt in [
            'Звони ９９８ ９０ １２３ ４５ ６７',          # полноширинные цифры
            'тел 9​9​8​9​0​1​2​3​4',  # zero-width
            '9️⃣9️⃣8️⃣9️⃣0️⃣1️⃣2️⃣3️⃣4️⃣',  # emoji-keycap цифры
            'пиши t․me/ivan_teacher',             # точка-лидер t․me
        ]:
            _, did = mask_contacts(txt)
            self.assertTrue(did, f'юникод-обход не пойман: {txt!r}')

    def test_extended_tlds_masked(self):
        for txt in ['смотри на signal.me/abc', 'мой канал project.dev',
                    'заходи на foo.xyz', 'ник telega.tg']:
            _, did = mask_contacts(txt)
            self.assertTrue(did, f'TLD-домен не пойман: {txt}')

    def test_spelled_phone_with_intent_masked(self):
        # ≥7 числительных + контактное намерение → телефон прописью.
        txt = 'позвони мне: девять девять восемь ноль один два три четыре пять'
        masked, did = mask_contacts(txt)
        self.assertTrue(did)
        self.assertEqual(masked, _MASK_PLACEHOLDER())

    def test_counting_lesson_not_masked(self):
        # Урок счёта без контактного слова — НЕ маскируем (платформа репетиторов).
        for txt in [
            'учим счёт: один два три четыре пять шесть семь восемь девять',
            'one two three four five six seven eight nine — повторяем',
            'на узбекском: bir ikki uch tort besh olti yetti sakkiz',
        ]:
            _, did = mask_contacts(txt)
            self.assertFalse(did, f'ложно замаскирован урок счёта: {txt}')


def _MASK_PLACEHOLDER():
    from teachers.contact_filter import _MASK
    return _MASK


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class ContactMaskingPairTests(TestCase):
    """mask_for_pair: порог доверия по числу оплаченных уроков."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('mp_t')
        self.student = _make_student_with_balance('mp_s', balance=Decimal('0'))

    def _completed_lesson(self):
        slot = TimeSlot.objects.create(
            teacher=self.teacher,
            start_at=timezone.now() - timedelta(hours=2),
            end_at=timezone.now() - timedelta(hours=1),
            status='booked',
        )
        Booking.objects.create(
            slot=slot, student=self.student, subject=self.subject, status='completed')

    @override_settings(CONTACT_MASK_MIN_PAID_LESSONS=5)
    def test_masks_below_threshold(self):
        text, did = mask_for_pair(self.student, self.teacher, 'звони +998901234567')
        self.assertTrue(did)
        self.assertNotIn('998901234567', text)

    @override_settings(CONTACT_MASK_MIN_PAID_LESSONS=1)
    def test_no_mask_above_threshold(self):
        self._completed_lesson()  # 1 оплаченный урок >= порог 1
        text, did = mask_for_pair(self.student, self.teacher, 'звони +998901234567')
        self.assertFalse(did)
        self.assertIn('998901234567', text)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES, CONTACT_MASK_MIN_PAID_LESSONS=5)
class ContactMaskingChannelsTests(TestCase):
    """Маскирование в реальных каналах: сообщение брони, ответ учителя, отзыв."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('ch_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('ch_s', balance=Decimal('0'))

    def test_booking_message_masked(self):
        slot = _slot(self.teacher)
        self.client.login(username='ch_s', password='x' * 12)
        r = self.client.post(
            reverse('booking_create_api'),
            data=json.dumps({
                'slot_id': slot.id, 'subject_id': self.subject.id,
                'message': 'мой телефон +998 90 123 45 67, пишите',
            }),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 201)
        b = Booking.objects.get(slot=slot)
        self.assertNotIn('123 45 67', b.student_message)
        self.assertNotIn('+998', b.student_message)

    def test_teacher_reply_masked(self):
        slot = _slot(self.teacher)
        b = Booking.create_hold(slot_id=slot.id, student=self.student, subject=self.subject)
        self.client.login(username='ch_t', password='x' * 12)
        r = self.client.post(
            reverse('booking_confirm_api', args=[b.id]),
            data=json.dumps({'reply': 'пишите в телеграм @teacher_handle'}),
            content_type='application/json',
        )
        self.assertEqual(r.status_code, 200)
        b.refresh_from_db()
        self.assertNotIn('@teacher_handle', b.teacher_reply)

    def test_review_comment_always_masked(self):
        # Завершённый урок → ученик оставляет отзыв с контактом.
        slot = TimeSlot.objects.create(
            teacher=self.teacher,
            start_at=timezone.now() - timedelta(hours=2),
            end_at=timezone.now() - timedelta(hours=1),
            status='booked',
        )
        b = Booking.objects.create(
            slot=slot, student=self.student, subject=self.subject, status='completed')
        self.client.login(username='ch_s', password='x' * 12)
        r = self.client.post(
            reverse('leave_review', args=[b.id]),
            data={'rating': 5, 'comment': 'супер! мой ватсап +998901234567'},
        )
        self.assertIn(r.status_code, (200, 302))
        from teachers.models import Review
        review = Review.objects.get(booking=b)
        self.assertNotIn('998901234567', review.comment)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class AdminImpersonationTests(TestCase):
    """admin_conversation_detail: writing as a user только за участников беседы."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('imp_t')
        self.student = _make_student_with_balance('imp_s', balance=Decimal('0'))
        self.outsider = _make_student_with_balance('imp_x', balance=Decimal('0'))
        self.conv = Conversation.objects.create(
            teacher=self.teacher, student=self.student, subject=self.subject)
        self.staff = User.objects.create_user(
            username='imp_admin', email='a@a.com', password='x' * 12,
            is_staff=True, is_superuser=True,
        )
        self.url = reverse('admin_conversation_detail', args=[self.conv.id])

    def test_send_as_participant_allowed(self):
        self.client.login(username='imp_admin', password='x' * 12)
        r = self.client.post(self.url, data={
            'content': 'привет от ученика', 'send_as': str(self.student.pk)})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Message.objects.filter(
            conversation=self.conv, sender=self.student).exists())

    def test_send_as_outsider_blocked(self):
        self.client.login(username='imp_admin', password='x' * 12)
        r = self.client.post(self.url, data={
            'content': 'подделка', 'send_as': str(self.outsider.pk)})
        self.assertEqual(r.status_code, 302)
        # Сообщение от постороннего НЕ создано.
        self.assertFalse(Message.objects.filter(
            conversation=self.conv, sender=self.outsider).exists())


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class SlotDeleteProtectsBookingHistoryTests(TestCase):
    """Аудит 2026-06-10 CRIT-2: удаление слотов не должно уносить брони.

    Booking.slot переведён на PROTECT: каскадное удаление слота уничтожало
    брони вместе с денежной историей (escrow/payout/refund, LessonEvent).
    Delete-вьюхи обязаны отвечать 409 на слот с историей, а bulk-delete —
    игнорировать клиентский only_free и не трогать слоты с бронями.
    """

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('sdel_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('sdel_s', balance=Decimal('0'))
        self.client.force_login(self.teacher.user)

    def _booked_slot(self):
        slot = _slot(self.teacher)
        b = Booking.create_hold(slot_id=slot.id, student=self.student,
                                subject=self.subject)
        b.confirm()
        slot.refresh_from_db()
        return slot, b

    def _cancelled_history_slot(self):
        """Free-слот, у которого есть отменённая (историческая) бронь."""
        slot, b = self._booked_slot()
        b.cancel_by_student()
        slot.refresh_from_db()
        self.assertEqual(slot.status, 'free')
        return slot, b

    def test_delete_slot_with_history_returns_409(self):
        slot, booking = self._cancelled_history_slot()
        r = self.client.delete(
            reverse('slots_detail_api', args=[slot.pk]))
        self.assertEqual(r.status_code, 409)
        self.assertTrue(TimeSlot.objects.filter(pk=slot.pk).exists())
        self.assertTrue(Booking.objects.filter(pk=booking.pk).exists())

    def test_delete_clean_free_slot_ok(self):
        slot = _slot(self.teacher)
        r = self.client.delete(
            reverse('slots_detail_api', args=[slot.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(TimeSlot.objects.filter(pk=slot.pk).exists())

    def _bulk_delete(self, **extra):
        payload = {
            'from': (timezone.now()).isoformat(),
            'to': (timezone.now() + timedelta(days=7)).isoformat(),
        }
        payload.update(extra)
        return self.client.post(
            reverse('slots_bulk_delete_api'),
            data=json.dumps(payload), content_type='application/json')

    def test_bulk_delete_ignores_client_only_free_false(self):
        slot, booking = self._booked_slot()
        clean = _slot(self.teacher, in_hours=24)
        r = self._bulk_delete(only_free=False)
        self.assertEqual(r.status_code, 200)
        # booked-слот и его бронь живы, чистый free-слот удалён.
        self.assertTrue(TimeSlot.objects.filter(pk=slot.pk).exists())
        self.assertTrue(Booking.objects.filter(pk=booking.pk).exists())
        self.assertFalse(TimeSlot.objects.filter(pk=clean.pk).exists())

    def test_bulk_delete_skips_free_slot_with_history(self):
        slot, booking = self._cancelled_history_slot()
        r = self._bulk_delete()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(TimeSlot.objects.filter(pk=slot.pk).exists())
        self.assertTrue(Booking.objects.filter(pk=booking.pk).exists())

    def test_db_level_protect_on_slot_delete(self):
        from django.db.models import ProtectedError
        slot, _ = self._cancelled_history_slot()
        with self.assertRaises(ProtectedError):
            slot.delete()


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class TeacherRejectClosesCommitmentsTests(TestCase):
    """Аудит 2026-06-10 H7: бан/отклонение учителя закрывает его обязательства.

    Раньше подписки забаненного учителя оставались ACTIVE: деньги учеников
    возвращались по одному уроку через no_show_teacher неделями. Теперь
    reject() отменяет подписки (refund эскроу) и одиночные брони (refund
    платных пробных).
    """

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('ban_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('ban_s', balance=Decimal('1000000'))
        self.moderator = User.objects.create_user(
            username='ban_mod', email='m@m.com', password='x' * 12,
            is_staff=True,
        )

    def test_reject_cancels_subscription_and_refunds_escrow(self):
        from billing.models import Subscription
        from billing.services import SubscriptionService
        from billing.tests import _make_tariff
        tariff = _make_tariff(self.teacher, self.subject,
                              lessons_per_week=2, duration_months=1,
                              price=Decimal('800000'))
        sub = SubscriptionService.purchase(
            student=self.student, tariff=tariff, idempotency_key='ban-purchase')
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('200000.00'))

        self.teacher.reject(self.moderator, comment='нарушение правил')

        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELLED_BY_ADMIN)
        self.assertEqual(sub.escrow_balance, Decimal('0.00'))
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('1000000.00'))
        # Будущие брони подписки отменены, слоты освобождены.
        self.assertFalse(Booking.objects.filter(
            subscription=sub, status__in=('pending', 'confirmed')).exists())

    def test_reject_cancels_paid_trial_and_refunds(self):
        from billing.services import TrialService
        from teachers.models import TeacherSubject
        ts = TeacherSubject.objects.get(teacher=self.teacher, subject=self.subject)
        ts.is_free_trial = False
        ts.trial_price = Decimal('50000')
        ts.save()
        slot = _slot(self.teacher)
        b = TrialService.book_paid_trial(
            student=self.student, slot_id=slot.id, teacher_subject=ts)
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('950000.00'))

        self.teacher.reject(self.moderator, comment='нарушение правил')

        b.refresh_from_db()
        self.assertEqual(b.status, 'cancelled_by_teacher')
        self.student.wallet.refresh_from_db()
        self.assertEqual(self.student.wallet.balance, Decimal('1000000.00'))


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class ContactBypassChannelsMaskedTests(TestCase):
    """Аудит 2026-06-10 H11: обходные каналы анти-контактного фильтра.

    Чат/бронь/отзывы уже маскировались, но контакты можно было передать через:
      * bio/университет/специализацию профиля (видны всем, без ре-модерации);
      * preferred_schedule заявки на обучение (свободный текст до 2000 симв.);
      * тексты домашних заданий (задание/ответ/комментарий оценки).
    """

    CONTACT = 'пишите мне в телеграм @ivan_repetitor срочно'

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('mask_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('mask_s', balance=Decimal('1000000'))

    def test_profile_edit_form_masks_public_text_fields(self):
        from teachers.forms import TeacherProfileEditForm
        form = TeacherProfileEditForm(
            data={
                'bio': self.CONTACT,
                'university': 'НУУз, тг: @ivan_repetitor',
                'specialization': 'математика, t.me/ivan_repetitor',
                'education_level': self.teacher.education_level,
                'experience_years': 3,
                'teaching_format': self.teacher.teaching_format,
                'teaching_languages': ['ru'],
                'available_weekdays': ['1'],
                'available_from': '09:00',
                'available_to': '18:00',
                'telegram': '@ivan_repetitor',  # легитимное поле — не трогаем
            },
            instance=self.teacher,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn('@ivan_repetitor', form.cleaned_data['bio'])
        self.assertNotIn('@ivan_repetitor', form.cleaned_data['university'])
        self.assertNotIn('t.me', form.cleaned_data['specialization'])
        # Гейтящееся поле контакта сохраняется как есть.
        self.assertEqual(form.cleaned_data['telegram'], '@ivan_repetitor')

    def test_create_request_masks_preferred_schedule(self):
        from billing.services import SubscriptionService
        sub = SubscriptionService.create_request(
            student=self.student, teacher=self.teacher, subject=self.subject,
            lessons_per_week=2, lesson_duration_minutes=60, duration_months=1,
            price_per_month=Decimal('800000'),
            preferred_schedule=self.CONTACT,
            idempotency_key='mask-req-1',
        )
        self.assertNotIn('@ivan_repetitor', sub.preferred_schedule)

    def test_homework_texts_masked(self):
        from billing.models import Homework, HomeworkSubmission, Subscription
        from billing.services import SubscriptionService
        from billing.tests import _make_tariff
        from django.urls import reverse as _rev

        tariff = _make_tariff(self.teacher, self.subject,
                              lessons_per_week=2, duration_months=1,
                              price=Decimal('800000'))
        sub = SubscriptionService.purchase(
            student=self.student, tariff=tariff, idempotency_key='mask-hw')

        # Учитель создаёт ДЗ с контактом в описании.
        self.client.force_login(self.teacher.user)
        r = self.client.post(_rev('teacher_homework_create'), data={
            'subscription': str(sub.pk),
            'title': 'Задание 1',
            'description': self.CONTACT,
        })
        self.assertEqual(r.status_code, 302)
        hw = Homework.objects.get(subscription=sub)
        self.assertNotIn('@ivan_repetitor', hw.description)

        # Ученик сдаёт работу с контактом в ответе.
        self.client.force_login(self.student)
        r = self.client.post(_rev('homework_detail', args=[hw.id]), data={
            'action': 'submit',
            'text_response': self.CONTACT,
        })
        self.assertEqual(r.status_code, 302)
        submission = HomeworkSubmission.objects.get(homework=hw)
        self.assertNotIn('@ivan_repetitor', submission.text_response)

        # Учитель оценивает с контактом в комментарии.
        self.client.force_login(self.teacher.user)
        r = self.client.post(_rev('homework_detail', args=[hw.id]), data={
            'action': 'grade', 'decision': 'grade', 'grade': 5,
            'feedback': self.CONTACT,
        })
        self.assertEqual(r.status_code, 302)
        submission.refresh_from_db()
        self.assertNotIn('@ivan_repetitor', submission.feedback)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES, TELEGRAM_BOT_TOKEN='test-bot-token')
class LinkTelegramProofOfOwnershipTests(TestCase):
    """Аудит 2026-06-10 H6: привязка Telegram требует подписанного payload.

    Раньше /api/telegram/link/ принимал голый telegram_id: атакующий мог
    привязать к себе чужой непривязанный TelegramUser (жертва нажала /start,
    но ещё не привязалась) — её чат получал бы уведомления атакующего.
    """

    def setUp(self):
        from teachers.models import TelegramUser
        self.attacker = _make_student_with_balance('tg_att', balance=Decimal('0'))
        # Жертва нажала /start в боте — создался непривязанный TelegramUser.
        self.victim_tg = TelegramUser.objects.create(telegram_id=777000111)
        self.client.force_login(self.attacker)
        from django.urls import reverse as _rev
        self.url = _rev('link_telegram_account')

    def _signed_payload(self, telegram_id, token='test-bot-token'):
        import hashlib
        import hmac as hmac_mod
        import time
        data = {'id': str(telegram_id), 'auth_date': str(int(time.time()))}
        check = '\n'.join(f'{k}={v}' for k, v in sorted(data.items()))
        secret = hashlib.sha256(token.encode()).digest()
        data['hash'] = hmac_mod.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return data

    def test_bare_telegram_id_rejected(self):
        r = self.client.post(
            self.url, data=json.dumps({'telegram_id': 777000111}),
            content_type='application/json')
        self.assertEqual(r.status_code, 403)
        self.victim_tg.refresh_from_db()
        self.assertIsNone(self.victim_tg.user)  # привязка НЕ произошла

    def test_forged_signature_rejected(self):
        payload = self._signed_payload(777000111, token='wrong-token')
        r = self.client.post(self.url, data=json.dumps(payload),
                             content_type='application/json')
        self.assertEqual(r.status_code, 403)
        self.victim_tg.refresh_from_db()
        self.assertIsNone(self.victim_tg.user)

    def test_valid_signed_payload_links(self):
        payload = self._signed_payload(777000111)
        r = self.client.post(self.url, data=json.dumps(payload),
                             content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.victim_tg.refresh_from_db()
        self.assertEqual(self.victim_tg.user, self.attacker)


class ErrorPagesTests(TestCase):
    """Аудит 2026-06-10 M18: страницы 404/500 вместо голых «Not Found»."""

    def test_404_renders_branded_page(self):
        with override_settings(DEBUG=False, STORAGES=SIMPLE_STATIC_STORAGES):
            r = self.client.get('/definitely-not-a-page-xyz/')
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, 'UstozHub', status_code=404)
        self.assertContains(r, '404', status_code=404)

    def test_500_template_renders_standalone(self):
        # Шаблон 500 должен рендериться пустым контекстом (как делает Django
        # при реальной 500-ке, когда контекст-процессоры могут быть мертвы).
        from django.template.loader import get_template
        html = get_template('500.html').render({})
        self.assertIn('500', html)
        self.assertIn('UstozHub', html)
        self.assertNotIn('{#', html)  # комментарий не утёк в HTML


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class SlotOverlapApiTests(TestCase):
    """Аудит 2026-06-10 H12 (приложение): API отдаёт 409 на пересечение.

    Exclusion-констрейнт работает только на PostgreSQL; здесь проверяем
    python-проверки и обработку конфликтов в обоих путях (create/PATCH).
    """

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('ovl_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.client.force_login(self.teacher.user)
        start = timezone.now() + timedelta(days=2)
        self.existing = TimeSlot.objects.create(
            teacher=self.teacher, start_at=start,
            end_at=start + timedelta(minutes=60), status='free')

    def _create(self, offset_min, dur=60):
        start = self.existing.start_at + timedelta(minutes=offset_min)
        return self.client.post(
            reverse('slots_create_api'),
            data=json.dumps({'start': start.isoformat(),
                             'end': (start + timedelta(minutes=dur)).isoformat()}),
            content_type='application/json')

    def test_overlapping_create_409(self):
        r = self._create(30)  # пересекается на 30 минут
        self.assertEqual(r.status_code, 409)

    def test_adjacent_create_ok(self):
        r = self._create(60)  # впритык — не пересечение
        self.assertEqual(r.status_code, 201)

    def test_patch_into_overlap_409(self):
        other_start = self.existing.end_at + timedelta(hours=2)
        other = TimeSlot.objects.create(
            teacher=self.teacher, start_at=other_start,
            end_at=other_start + timedelta(minutes=60), status='free')
        r = self.client.patch(
            reverse('slots_detail_api', args=[other.pk]),
            data=json.dumps({
                'start': (self.existing.start_at + timedelta(minutes=15)).isoformat(),
                'end': (self.existing.start_at + timedelta(minutes=75)).isoformat(),
            }),
            content_type='application/json')
        self.assertEqual(r.status_code, 409)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class NoShowNotificationTextsTests(TestCase):
    """Аудит 2026-06-10 M15: тексты уведомлений соответствуют движению денег."""

    def setUp(self):
        from billing.platform_account import get_or_create_platform_user
        get_or_create_platform_user()
        self.teacher, self.subject = _make_teacher_with_subject('m15_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('m15_s', balance=Decimal('1000000'))

    def _subscription_booking(self):
        from billing.services import SubscriptionService
        from billing.tests import _make_tariff
        tariff = _make_tariff(self.teacher, self.subject,
                              lessons_per_week=2, duration_months=1,
                              price=Decimal('800000'))
        sub = SubscriptionService.purchase(
            student=self.student, tariff=tariff, idempotency_key='m15-p')
        return Booking.objects.filter(subscription=sub).first()

    def test_teacher_no_show_text_mentions_balance_refund(self):
        from teachers.tasks import _notify_teacher_no_show
        from teachers.models import Notification
        b = self._subscription_booking()
        _notify_teacher_no_show(b)
        n = Notification.objects.filter(
            target_user=self.student, title='Преподаватель не подключился').first()
        self.assertIsNotNone(n)
        self.assertIn('возвращена на ваш баланс', n.full_text)
        self.assertNotIn('выберите новую дату в расписании', n.full_text)

    def test_forgiven_no_show_cta_points_to_schedule(self):
        from teachers.tasks import _handle_student_no_show
        from teachers.models import Notification
        b = self._subscription_booking()
        Booking.objects.filter(pk=b.pk).update(
            status='no_show_student', no_show_forgiven=True)
        b.refresh_from_db()
        _handle_student_no_show(b)
        n = Notification.objects.filter(
            target_user=self.student, title='Вы пропустили урок').first()
        self.assertIsNotNone(n)
        # CTA — на страницу добора расписания, а не на список броней.
        self.assertIn(f'/subscriptions/{b.subscription_id}/schedule/', n.action_url)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class SmartMatchConsistencyTests(TestCase):
    """Аудит 2026-06-10 M6: оптимизация matching не меняет результат."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('m6_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('m6_s', balance=Decimal('0'))
        profile = self.student.student_profile
        profile.desired_subjects.add(self.subject)

    def test_score_same_with_and_without_precomputed_ids(self):
        profile = self.student.student_profile
        plain = self.teacher.calculate_match_score(profile)
        ids = set(profile.desired_subjects.values_list('id', flat=True))
        fast = self.teacher.calculate_match_score(profile, _desired_ids=ids)
        self.assertEqual(plain['score'], fast['score'])
        self.assertEqual(
            {s.id for s in plain['matched_subjects']},
            {s.id for s in fast['matched_subjects']},
        )
        self.assertGreaterEqual(plain['score'], 40)  # предмет совпал

    def test_get_smart_matches_returns_teacher(self):
        profile = self.student.student_profile
        from teachers.models import TeacherProfile
        matches = TeacherProfile.get_smart_matches(profile, limit=5)
        self.assertTrue(any(
            m['teacher'].pk == self.teacher.pk for m in matches))


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class BroadcastViaSharedGroupTests(TestCase):
    """Аудит 2026-06-10 H15: broadcast — один group_send в общую группу."""

    def test_broadcast_task_sends_to_shared_group(self):
        from unittest.mock import patch as _patch, AsyncMock, MagicMock
        from teachers.models import Notification
        from teachers.tasks import broadcast_notification_push

        n = Notification.objects.create(
            title='Всем привет', short_text='т', full_text='т',
            target='students', is_active=True,
        )
        fake_layer = MagicMock()
        fake_layer.group_send = AsyncMock()
        with _patch('channels.layers.get_channel_layer', return_value=fake_layer):
            res = broadcast_notification_push(n.id)
        self.assertEqual(res, 1)
        args, kwargs = fake_layer.group_send.call_args
        self.assertEqual(args[0], 'broadcast_students')
        self.assertEqual(args[1]['payload']['id'], n.id)

    def test_cleanup_deactivates_old_notifications(self):
        from teachers.models import Notification
        from teachers.tasks import cleanup_old_inapp_notifications
        old = Notification.objects.create(
            title='старое', short_text='s', full_text='f',
            target='all', is_active=True,
        )
        Notification.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=120))
        fresh = Notification.objects.create(
            title='свежее', short_text='s', full_text='f',
            target='all', is_active=True,
        )
        res = cleanup_old_inapp_notifications()
        self.assertEqual(res['deactivated'], 1)
        old.refresh_from_db(); fresh.refresh_from_db()
        self.assertFalse(old.is_active)
        self.assertTrue(fresh.is_active)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class HomeShuffleTests(TestCase):
    """Аудит 2026-06-10 H13: shuffle главной без CASE-по-всем-id и без
    создания сессии каждому анониму."""

    def setUp(self):
        for i in range(3):
            t, _s = _make_teacher_with_subject(f'shf_t{i}')
            t.moderation_status = 'approved'
            t.is_active = True
            t.save()

    def test_anonymous_home_renders_and_creates_no_session(self):
        from django.contrib.sessions.models import Session
        before = Session.objects.count()
        r = self.client.get(reverse('home'))
        self.assertEqual(r.status_code, 200)
        # Просмотр главной анонимом не пишет строку в django_session.
        self.assertEqual(Session.objects.count(), before)
        # Учителя на странице есть.
        page = r.context['teachers']
        self.assertGreaterEqual(len(list(page.object_list)), 3)

    def test_anonymous_order_stable_within_hour(self):
        r1 = self.client.get(reverse('home'))
        r2 = self.client.get(reverse('home'))
        ids1 = [t.pk for t in r1.context['teachers'].object_list]
        ids2 = [t.pk for t in r2.context['teachers'].object_list]
        self.assertEqual(ids1, ids2)  # стабильная пагинация в пределах часа

    def test_authenticated_home_renders(self):
        student = _make_student_with_balance('shf_s', balance=Decimal('0'))
        self.client.force_login(student)
        r = self.client.get(reverse('home'))
        self.assertEqual(r.status_code, 200)
        self.assertIn('teacher_shuffle_seed', self.client.session)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class ConversationsListLastMessageTests(TestCase):
    """Аудит 2026-06-10 H14: последнее сообщение через Subquery, не prefetch
    всей истории."""

    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('cnv_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('cnv_s', balance=Decimal('0'))
        self.conv = Conversation.objects.create(
            teacher=self.teacher, student=self.student, subject=self.subject)
        for i in range(5):
            Message.objects.create(
                conversation=self.conv, sender=self.student, content=f'm{i}')
        self.last = Message.objects.create(
            conversation=self.conv, sender=self.teacher.user, content='последнее')

    def test_student_sees_last_message(self):
        self.client.force_login(self.student)
        r = self.client.get(reverse('conversations_list'))
        self.assertEqual(r.status_code, 200)
        info = r.context['conversations']
        self.assertEqual(len(info), 1)
        self.assertEqual(info[0]['last_message'].pk, self.last.pk)
        # Непрочитанные: 1 сообщение учителя.
        self.assertEqual(info[0]['unread_count'], 1)

    def test_teacher_sees_last_message(self):
        self.client.force_login(self.teacher.user)
        r = self.client.get(reverse('conversations_list'))
        self.assertEqual(r.status_code, 200)
        info = r.context['conversations']
        self.assertEqual(info[0]['last_message'].pk, self.last.pk)
        self.assertEqual(info[0]['unread_count'], 5)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class LeadCountsConsistencyTests(TestCase):
    """Аудит 2026-06-10 M7: счётчики лидов из переданного списка == свежим."""

    def test_counts_match_with_precomputed_leads(self):
        from teachers.leads import count_teacher_leads, get_teacher_leads
        teacher, _subject = _make_teacher_with_subject('lc_t')
        teacher.moderation_status = 'approved'
        teacher.is_active = True
        teacher.save()
        student = _make_student_with_balance('lc_s', balance=Decimal('0'))
        from teachers.models import Favorite
        Favorite.objects.create(student=student, teacher=teacher)

        leads = get_teacher_leads(teacher)
        with_list = count_teacher_leads(teacher, leads=leads)
        fresh = count_teacher_leads(teacher)  # через кэш
        self.assertEqual(with_list, fresh)
        self.assertEqual(with_list['warm'], 1)
        self.assertEqual(with_list['total'], 1)


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class UiHardeningTests(TestCase):
    """Аудит 2026-06-10 M20/M21: мобильное меню учителя и data-lock."""

    def test_teacher_mobile_menu_has_calendar_items(self):
        teacher, _s = _make_teacher_with_subject('ui_t')
        teacher.moderation_status = 'approved'
        teacher.is_active = True
        teacher.save()
        self.client.force_login(teacher.user)
        r = self.client.get(reverse('home'))
        self.assertEqual(r.status_code, 200)
        # Ссылка на календарь встречается и в desktop-дропдауне, и в мобильном меню.
        import re as _re
        calendar_links = _re.findall(r'href="[^"]*?/teacher/calendar/"', r.content.decode())
        self.assertGreaterEqual(len(calendar_links), 2)

    def test_topup_form_has_data_lock(self):
        student = _make_student_with_balance('ui_s', balance=Decimal('0'))
        self.client.force_login(student)
        r = self.client.get(reverse('wallet_topup_request'))
        if r.status_code == 200:
            self.assertContains(r, 'data-lock')
