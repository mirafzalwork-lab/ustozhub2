"""
Телефон обязателен при регистрации студента — покрытие всех путей.

Основная форма StudentRegistrationForm уже требовала телефон; пробел был в
Google-онбординге (GoogleStudentOnboardingForm), который создавал студента без
телефона. Тесты фиксируют обязательность на ОБОИХ путях и краевые случаи.
"""
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from teachers.models import StudentProfile, TeacherProfile, City, Subject, SubjectCategory
from teachers.forms import GoogleStudentOnboardingForm, StudentRegistrationForm

User = get_user_model()


class PhoneRequiredBase(TestCase):
    def setUp(self):
        self.city = City.objects.create(name='Ташкент')
        self.cat = SubjectCategory.objects.create(name='Языки')
        self.subject = Subject.objects.create(name='Английский', category=self.cat)


class GoogleOnboardingFormTest(PhoneRequiredBase):
    def _data(self, **over):
        d = {
            'first_name': 'Мария',
            'last_name': 'Иванова',
            'phone': '+998901112233',
            'interests': [self.subject.id],
        }
        d.update(over)
        return d

    def test_valid_with_phone(self):
        form = GoogleStudentOnboardingForm(data=self._data())
        self.assertTrue(form.is_valid(), form.errors)

    def test_missing_phone_invalid(self):
        form = GoogleStudentOnboardingForm(data=self._data(phone=''))
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)

    def test_whitespace_phone_invalid(self):
        form = GoogleStudentOnboardingForm(data=self._data(phone='   '))
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)

    def test_duplicate_phone_invalid(self):
        User.objects.create_user(username='other', password='p', phone='+998901112233')
        form = GoogleStudentOnboardingForm(data=self._data())
        self.assertFalse(form.is_valid())
        self.assertIn('phone', form.errors)


class GoogleOnboardingViewTest(PhoneRequiredBase):
    def setUp(self):
        super().setUp()
        # Google-пользователь: есть аккаунт, но НЕТ ни студ., ни учит. профиля.
        self.user = User.objects.create_user(
            username='guser', password='pass12345!', first_name='Гость',
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.url = reverse('google_student_onboarding')

    def test_get_renders_phone_field(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id_phone')

    def test_post_without_phone_no_profile(self):
        r = self.client.post(self.url, {
            'first_name': 'Мария', 'interests': [self.subject.id],
        })
        self.assertEqual(r.status_code, 200)  # форма перерисована с ошибкой
        self.assertFalse(StudentProfile.objects.filter(user=self.user).exists())

    def test_post_with_phone_creates_profile_and_saves_phone(self):
        r = self.client.post(self.url, {
            'first_name': 'Мария', 'last_name': 'Иванова',
            'phone': '+998901112233', 'interests': [self.subject.id],
        })
        self.assertEqual(r.status_code, 302)
        self.assertTrue(StudentProfile.objects.filter(user=self.user).exists())
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone, '+998901112233')
        self.assertEqual(self.user.user_type, 'student')

    def test_post_duplicate_phone_no_profile(self):
        User.objects.create_user(username='dup', password='p', phone='+998901112233')
        r = self.client.post(self.url, {
            'first_name': 'Мария', 'phone': '+998901112233',
            'interests': [self.subject.id],
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(StudentProfile.objects.filter(user=self.user).exists())


class MainRegistrationPhoneStillRequiredTest(PhoneRequiredBase):
    """Регрессия: основная форма регистрации по-прежнему требует телефон."""

    def test_phone_field_required(self):
        self.assertTrue(StudentRegistrationForm().fields['phone'].required)
