"""
E2E-тест teacher-wizard: загрузка сертификата на шаге 6.
Проходит все 6 шагов через тестовый клиент и проверяет, что Certificate
реально создаётся, привязывается к профилю, а содержимое файла не портится
(валидация читает magic-bytes, поэтому важно, что seek() возвращает позицию).
"""
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth import get_user_model

from billing.tests import SIMPLE_STATIC_STORAGES
from teachers.models import City, Subject, SubjectCategory, TeacherProfile

User = get_user_model()

PDF = b'%PDF-1.4 minimal test content\n' + b'X' * 500 + b'\n%%EOF'
HEIC = b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heic' + b'Z' * 400


@override_settings(STORAGES=SIMPLE_STATIC_STORAGES)
class WizardCertificateTest(TestCase):
    def setUp(self):
        cache.clear()
        self.city = City.objects.create(name='Ташкент')
        self.cat = SubjectCategory.objects.create(name='Языки')
        self.subject = Subject.objects.create(name='Английский', category=self.cat)
        self.url = reverse('teacher_register')

    def _mgmt(self, step):
        return {'teacher_registration_wizard-current_step': step}

    def _walk_wizard(self, email, cert_filename, cert_bytes, cert_ct):
        """Проходит все 6 шагов и возвращает созданный TeacherProfile."""
        c = self.client
        self.assertEqual(c.get(self.url).status_code, 200)

        self.assertEqual(c.post(self.url, {
            **self._mgmt('basic_profile'),
            'basic_profile-first_name': 'Иван',
            'basic_profile-last_name': 'Петров',
            'basic_profile-teaching_languages': ['ru'],
            'basic_profile-phone': '+998901112233',
        }).status_code, 200, 'step1 failed')

        self.assertEqual(c.post(self.url, {
            **self._mgmt('account_security'),
            'account_security-email': email,
            'account_security-password1': 'Vortex!9241kp',
            'account_security-password2': 'Vortex!9241kp',
        }).status_code, 200, 'step2 failed')

        self.assertEqual(c.post(self.url, {
            **self._mgmt('education'),
            'education-experience_years': '3',
            'education-bio': 'Опытный преподаватель английского языка с большим стажем.',
        }).status_code, 200, 'step3 failed')

        self.assertEqual(c.post(self.url, {
            **self._mgmt('availability'),
            'availability-telegram': '@ivanteach',
            'availability-teaching_format': 'online',
            'availability-city': str(self.city.id),
        }).status_code, 200, 'step4 failed')

        self.assertEqual(c.post(self.url, {
            **self._mgmt('subjects'),
            'subjects-subject_1': str(self.subject.id),
            'subjects-hourly_rate_1': '50000',
            'subjects-trial_duration_1': '60',
            'subjects-is_free_trial_1': 'on',
        }).status_code, 200, 'step5 failed')

        cert = SimpleUploadedFile(cert_filename, cert_bytes, content_type=cert_ct)
        r = c.post(self.url, {
            **self._mgmt('certificates'),
            'certificates-cert_name_1': 'IELTS 8.0',
            'certificates-cert_issuer_1': 'British Council',
            'certificates-cert_file_1': cert,
        })
        if r.status_code == 200 and getattr(r, 'context', None) and 'form' in r.context:
            self.fail('step6 не прошёл: %s / %s' % (
                r.context['form'].errors, r.context['form'].non_field_errors()))
        self.assertEqual(r.status_code, 302, 'ожидался редирект на complete')

        user = User.objects.filter(email=email).first()
        self.assertIsNotNone(user, 'Пользователь не создан')
        return TeacherProfile.objects.get(user=user)

    def _assert_cert(self, profile, expected_bytes):
        certs = list(profile.certificates.all())
        self.assertEqual(len(certs), 1, 'Сертификат не сохранён')
        cert = certs[0]
        self.assertTrue(cert.file, 'Файл сертификата пустой')
        self.assertEqual(cert.name, 'IELTS 8.0')
        # Ключевое: содержимое не обрезано чтением magic-bytes.
        cert.file.open('rb')
        try:
            saved = cert.file.read()
        finally:
            cert.file.close()
        self.assertEqual(saved, expected_bytes, 'Содержимое файла повреждено при сохранении')

    def test_wizard_with_pdf(self):
        profile = self._walk_wizard('ivan.pdf@test.com', 'diploma.pdf', PDF, 'application/pdf')
        self._assert_cert(profile, PDF)

    def test_wizard_with_heic_iphone(self):
        """Регрессия: фото диплома с iPhone (HEIC) должно проходить весь wizard."""
        profile = self._walk_wizard('ivan.heic@test.com', 'photo.heic', HEIC, 'image/heic')
        self._assert_cert(profile, HEIC)
