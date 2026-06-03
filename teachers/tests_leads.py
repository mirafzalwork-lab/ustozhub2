"""
Тесты функции «Потенциальные ученики» (лиды):
доменный слой, гейтинг инициации, антиспам, opt-out, страница, маскирование.
"""
from datetime import timedelta

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from teachers.models import (
    TeacherProfile, StudentProfile, City, Subject, SubjectCategory,
    Favorite, LeadOptOut, TimeSlot, Booking, Conversation, Message,
)
from teachers import leads

User = get_user_model()


class LeadsBaseTestCase(TestCase):
    def setUp(self):
        self.city = City.objects.create(name='Ташкент')
        self.cat = SubjectCategory.objects.create(name='Языки')
        self.subject = Subject.objects.create(name='Английский', category=self.cat)

        self.teacher_user = User.objects.create_user(
            username='t1', password='pass12345!', email='t1@test.com',
            user_type='teacher', first_name='Иван', last_name='Петров',
        )
        self.teacher = TeacherProfile.objects.create(
            user=self.teacher_user, city=self.city, experience_years=5,
            moderation_status='approved', is_active=True,
        )
        # Ученик-лид
        self.student_user = User.objects.create_user(
            username='s1', password='pass12345!', email='s1@test.com',
            user_type='student', first_name='Мария', last_name='Иванова',
        )
        self.student = StudentProfile.objects.create(user=self.student_user, city=self.city)
        # Посторонний ученик (НЕ лид)
        self.stranger_user = User.objects.create_user(
            username='s2', password='pass12345!', email='s2@test.com',
            user_type='student', first_name='Олег', last_name='Сидоров',
        )
        self.stranger = StudentProfile.objects.create(user=self.stranger_user, city=self.city)
        self.client = Client()

    def _make_trial_booking(self, student_user, status='pending'):
        slot = TimeSlot.objects.create(
            teacher=self.teacher,
            start_at=timezone.now() + timedelta(days=1),
            end_at=timezone.now() + timedelta(days=1, hours=1),
            status='booked',
        )
        return Booking.objects.create(
            slot=slot, student=student_user, subject=self.subject,
            is_trial=True, status=status,
        )


class LeadDomainTest(LeadsBaseTestCase):
    def test_no_interest_is_not_a_lead(self):
        self.assertIsNone(leads.get_lead_status(self.teacher, self.stranger_user))
        self.assertFalse(leads.can_teacher_initiate(self.teacher, self.stranger_user))

    def test_favorite_is_warm(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        self.assertEqual(leads.get_lead_status(self.teacher, self.student_user), leads.LEAD_WARM)
        self.assertTrue(leads.can_teacher_initiate(self.teacher, self.student_user))

    def test_trial_booking_is_hot(self):
        self._make_trial_booking(self.student_user)
        self.assertEqual(leads.get_lead_status(self.teacher, self.student_user), leads.LEAD_HOT)

    def test_hot_beats_warm(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        self._make_trial_booking(self.student_user)
        self.assertEqual(leads.get_lead_status(self.teacher, self.student_user), leads.LEAD_HOT)

    def test_student_cancelled_trial_not_hot(self):
        self._make_trial_booking(self.student_user, status='cancelled_by_student')
        # отменённый учеником пробный не делает его горячим лидом
        self.assertIsNone(leads.get_lead_status(self.teacher, self.student_user))

    def test_opt_out_hides_lead(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        LeadOptOut.objects.create(student=self.student_user, teacher=self.teacher)
        self.assertIsNone(leads.get_lead_status(self.teacher, self.student_user))
        self.assertFalse(leads.can_teacher_initiate(self.teacher, self.student_user))

    def test_teacher_user_is_never_a_lead(self):
        # передаём не-ученика
        self.assertIsNone(leads.get_lead_status(self.teacher, self.teacher_user))

    def test_get_teacher_leads_ordering_and_counts(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        self._make_trial_booking(self.stranger_user)
        result = leads.get_teacher_leads(self.teacher)
        self.assertEqual(len(result), 2)
        # горячий (stranger) идёт первым
        self.assertEqual(result[0]['status'], leads.LEAD_HOT)
        self.assertEqual(result[0]['student_user'], self.stranger_user)
        self.assertEqual(result[1]['status'], leads.LEAD_WARM)

        counts = leads.count_teacher_leads(self.teacher)
        self.assertEqual(counts, {'hot': 1, 'warm': 1, 'total': 2})

    def test_get_teacher_leads_excludes_opted_out(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        LeadOptOut.objects.create(student=self.student_user, teacher=self.teacher)
        self.assertEqual(leads.get_teacher_leads(self.teacher), [])


class LeadGatingTest(LeadsBaseTestCase):
    def test_teacher_cannot_initiate_with_non_lead(self):
        self.client.login(username='t1', password='pass12345!')
        resp = self.client.get(
            reverse('start_conversation', args=[self.stranger_user.id])
        )
        # редирект назад, переписка не создана
        self.assertFalse(
            Conversation.objects.filter(
                teacher=self.teacher, student=self.stranger_user
            ).exists()
        )

    def test_teacher_can_initiate_with_warm_lead(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        self.client.login(username='t1', password='pass12345!')
        resp = self.client.get(
            reverse('start_conversation', args=[self.student_user.id])
        )
        self.assertTrue(
            Conversation.objects.filter(
                teacher=self.teacher, student=self.student_user
            ).exists()
        )

    def test_student_can_always_initiate_with_teacher(self):
        # ученик пишет учителю — гейтинг на него не распространяется
        self.client.login(username='s2', password='pass12345!')
        resp = self.client.get(
            reverse('start_conversation', args=[self.teacher_user.id])
        )
        self.assertTrue(
            Conversation.objects.filter(
                teacher=self.teacher, student=self.stranger_user
            ).exists()
        )


class AntiSpamFirstMessageTest(LeadsBaseTestCase):
    def setUp(self):
        super().setUp()
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        self.conversation = Conversation.objects.create(
            teacher=self.teacher, student=self.student_user, is_active=True
        )

    def _send(self, content):
        return self.client.post(
            reverse('send_message_ajax', args=[self.conversation.id]),
            data={'content': content},
        )

    def test_teacher_first_message_allowed_second_blocked(self):
        self.client.login(username='t1', password='pass12345!')
        r1 = self._send('Здравствуйте! Готов помочь с английским.')
        self.assertEqual(r1.json().get('success'), True)
        r2 = self._send('Ещё раз здравствуйте?')
        self.assertEqual(r2.status_code, 403)
        self.assertFalse(r2.json().get('success'))
        # в базе ровно одно сообщение от учителя
        self.assertEqual(
            self.conversation.messages.filter(sender=self.teacher_user).count(), 1
        )

    def test_teacher_can_continue_after_student_reply(self):
        # учитель пишет первое
        self.client.login(username='t1', password='pass12345!')
        self._send('Здравствуйте!')
        # ученик отвечает
        self.client.logout()
        self.client.login(username='s1', password='pass12345!')
        self._send('Здравствуйте, расскажите про формат.')
        # учитель снова может писать
        self.client.logout()
        self.client.login(username='t1', password='pass12345!')
        r = self._send('Конечно! Уроки по 60 минут.')
        self.assertEqual(r.json().get('success'), True)
        self.assertEqual(
            self.conversation.messages.filter(sender=self.teacher_user).count(), 2
        )

    def test_student_not_rate_limited_by_first_message_rule(self):
        # ученик может слать несколько сообщений подряд
        self.client.login(username='s1', password='pass12345!')
        self.assertEqual(self._send('Первое').json().get('success'), True)
        self.assertEqual(self._send('Второе').json().get('success'), True)


class LeadOptOutTest(LeadsBaseTestCase):
    def test_opt_out_toggle_and_blocks_initiation(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        self.client.login(username='s1', password='pass12345!')
        url = reverse('lead_opt_out', args=[self.teacher.id])

        r1 = self.client.post(url)
        self.assertEqual(r1.json(), {'success': True, 'opted_out': True})
        self.assertTrue(
            LeadOptOut.objects.filter(student=self.student_user, teacher=self.teacher).exists()
        )
        # учитель больше не может инициировать
        self.assertFalse(leads.can_teacher_initiate(self.teacher, self.student_user))

        # повторный вызов — снимает отказ
        r2 = self.client.post(url)
        self.assertEqual(r2.json(), {'success': True, 'opted_out': False})
        self.assertTrue(leads.can_teacher_initiate(self.teacher, self.student_user))

    def test_opt_out_requires_student(self):
        self.client.login(username='t1', password='pass12345!')
        r = self.client.post(reverse('lead_opt_out', args=[self.teacher.id]))
        self.assertFalse(r.json().get('success'))

    def test_teacher_initiation_blocked_after_opt_out(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        LeadOptOut.objects.create(student=self.student_user, teacher=self.teacher)
        self.client.login(username='t1', password='pass12345!')
        self.client.get(reverse('start_conversation', args=[self.student_user.id]))
        self.assertFalse(
            Conversation.objects.filter(
                teacher=self.teacher, student=self.student_user
            ).exists()
        )


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class PotentialStudentsPageTest(LeadsBaseTestCase):
    def test_page_renders_with_counts(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        self._make_trial_booking(self.stranger_user)
        self.client.login(username='t1', password='pass12345!')
        resp = self.client.get(reverse('potential_students'))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['counts'], {'hot': 1, 'warm': 1, 'total': 2})
        self.assertEqual(len(resp.context['items']), 2)
        # горячий лид первым
        self.assertEqual(resp.context['items'][0]['status'], leads.LEAD_HOT)

    def test_status_filter_hot(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        self._make_trial_booking(self.stranger_user)
        self.client.login(username='t1', password='pass12345!')
        resp = self.client.get(reverse('potential_students'), {'status': 'hot'})
        self.assertEqual(len(resp.context['items']), 1)
        self.assertEqual(resp.context['items'][0]['student_user'], self.stranger_user)

    def test_chat_state_can_write_then_awaiting(self):
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        self.client.login(username='t1', password='pass12345!')
        # пока чата нет — можно писать
        resp = self.client.get(reverse('potential_students'))
        self.assertEqual(resp.context['items'][0]['chat_state'], 'can_write')
        # учитель отправляет первое сообщение
        conv = Conversation.objects.create(
            teacher=self.teacher, student=self.student_user, is_active=True
        )
        Message.objects.create(conversation=conv, sender=self.teacher_user, content='Привет!')
        resp2 = self.client.get(reverse('potential_students'))
        self.assertEqual(resp2.context['items'][0]['chat_state'], 'awaiting_reply')

    def test_student_cannot_access_page(self):
        self.client.login(username='s1', password='pass12345!')
        resp = self.client.get(reverse('potential_students'))
        self.assertEqual(resp.status_code, 302)  # редирект на home


class ContactMaskingTest(LeadsBaseTestCase):
    """Анти-обход: контакты в сообщении лида маскируются (contact_filter)."""

    def setUp(self):
        super().setUp()
        Favorite.objects.create(student=self.student_user, teacher=self.teacher)
        self.conversation = Conversation.objects.create(
            teacher=self.teacher, student=self.student_user, is_active=True
        )

    def test_mask_contacts_unit(self):
        from teachers.contact_filter import mask_contacts
        masked, changed = mask_contacts('Пишите мне @ivan_tutor или +998 90 123 45 67')
        self.assertTrue(changed)
        self.assertNotIn('@ivan_tutor', masked)
        self.assertNotIn('90 123 45 67', masked)

    def test_first_message_to_lead_is_masked(self):
        self.client.login(username='t1', password='pass12345!')
        resp = self.client.post(
            reverse('send_message_ajax', args=[self.conversation.id]),
            data={'content': 'Здравствуйте! Мой телеграм @ivan_tutor'},
        )
        self.assertTrue(resp.json().get('success'))
        msg = self.conversation.messages.get(sender=self.teacher_user)
        self.assertNotIn('@ivan_tutor', msg.content)
