"""
Тесты presigned-загрузки материалов урока (teachers/lesson_files_views.py).

Фокус — устойчивость к типу файла: телефоны/некоторые браузеры присылают пустой
или application/octet-stream content_type, из-за чего валидная книга/PDF раньше
отклонялись как «недопустимый формат». Теперь тип определяется по расширению.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.tests import (
    SIMPLE_STATIC_STORAGES,
    _make_student_with_balance,
    _make_teacher_with_subject,
)
from teachers.models import Booking, TimeSlot

PWD = 'x' * 12


@override_settings(
    STORAGES=SIMPLE_STATIC_STORAGES,
    S3_BUCKET_NAME='test-bucket',
    S3_PUBLIC_URL='https://cdn.example.com',
)
class LessonFilePresignTest(TestCase):
    def setUp(self):
        self.teacher, self.subject = _make_teacher_with_subject('lf_t')
        self.teacher.moderation_status = 'approved'
        self.teacher.is_active = True
        self.teacher.save()
        self.student = _make_student_with_balance('lf_s', balance=0)
        slot = TimeSlot.objects.create(
            teacher=self.teacher,
            start_at=timezone.now() + timedelta(hours=1),
            end_at=timezone.now() + timedelta(hours=2),
            status='free',
        )
        self.booking = Booking.create_hold(
            slot_id=slot.id, student=self.student, subject=self.subject)
        self.booking.confirm()
        self.booking.refresh_from_db()
        self.url = reverse('lesson_file_presign', args=[self.booking.id])

    def _post(self, payload):
        self.client.login(username='lf_t', password=PWD)
        with mock.patch('teachers.lesson_files_views._get_s3_client') as m:
            m.return_value.generate_presigned_url.return_value = 'https://s3.example/put'
            return self.client.post(self.url, data=payload, content_type='application/json')

    def test_pdf_with_empty_mime_accepted_by_extension(self):
        # Телефон прислал пустой content_type — тип берём из расширения .pdf.
        r = self._post({'file_name': 'kitob.pdf', 'content_type': '', 'file_size': 5 * 1024 * 1024})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data['content_type'], 'application/pdf')
        self.assertTrue(data['file_key'].endswith('.pdf'))

    def test_octet_stream_epub_accepted(self):
        r = self._post({'file_name': 'book.epub', 'content_type': 'application/octet-stream',
                        'file_size': 3 * 1024 * 1024})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['content_type'], 'application/epub+zip')

    def test_unknown_extension_rejected(self):
        r = self._post({'file_name': 'virus.exe', 'content_type': 'application/octet-stream',
                        'file_size': 1024})
        self.assertEqual(r.status_code, 400)

    def test_oversize_rejected(self):
        big = (200) * 1024 * 1024  # 200 МБ > лимита 100
        r = self._post({'file_name': 'huge.pdf', 'content_type': 'application/pdf', 'file_size': big})
        self.assertEqual(r.status_code, 400)
