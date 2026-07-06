"""
Тесты валидации загрузки сертификатов (шаг 6 регистрации учителя).

Регрессия: учителя жаловались, что не могут загрузить сертификат — фото диплома
с iPhone (HEIC) / WhatsApp (WEBP) отклонялись сервером. Теперь принимаем реальные
телефонные форматы, но проверяем сигнатуру файла (magic bytes) для безопасности.
"""
from django.test import TestCase
from django.core.files.uploadedfile import SimpleUploadedFile

from teachers.registration_forms import Step6CertificatesForm


# Валидные заголовки реальных форматов
JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF' + b'\x00' * 32
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32
PDF = b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n' + b'0' * 32
WEBP = b'RIFF\x24\x00\x00\x00WEBPVP8 ' + b'\x00' * 16
# HEIC: ISO-BMFF box, на смещении 4 — 'ftyp', бренд 'heic'
HEIC = b'\x00\x00\x00\x18ftypheic\x00\x00\x00\x00heic' + b'\x00' * 16


def make(name, content, ct):
    return SimpleUploadedFile(name, content, content_type=ct)


def validate(files_map, data_extra):
    form = Step6CertificatesForm(data=data_extra, files=files_map)
    return form.is_valid(), dict(form.errors)


class CertUploadTest(TestCase):
    def _one(self, fname, content, ct):
        return validate(
            {'cert_file_1': make(fname, content, ct)},
            {'cert_name_1': 'Диплом', 'cert_issuer_1': 'НУУз'},
        )

    # --- принимаем реальные форматы ---
    def test_jpeg_ok(self):
        ok, err = self._one('scan.jpg', JPEG, 'image/jpeg')
        self.assertTrue(ok, err)

    def test_png_ok(self):
        ok, err = self._one('scan.png', PNG, 'image/png')
        self.assertTrue(ok, err)

    def test_pdf_ok(self):
        ok, err = self._one('diploma.pdf', PDF, 'application/pdf')
        self.assertTrue(ok, err)

    def test_heic_iphone_ok(self):
        """Главная регрессия: фото диплома с iPhone (HEIC)."""
        ok, err = self._one('photo.heic', HEIC, 'image/heic')
        self.assertTrue(ok, err)

    def test_webp_ok(self):
        ok, err = self._one('photo.webp', WEBP, 'image/webp')
        self.assertTrue(ok, err)

    def test_uppercase_ext_ok(self):
        ok, err = self._one('SCAN.JPG', JPEG, 'image/jpeg')
        self.assertTrue(ok, err)

    # --- безопасность: сигнатура должна совпадать с расширением-белым списком ---
    def test_renamed_executable_rejected(self):
        """.jpg-расширение, но внутри исполняемый файл (MZ/ELF) — отклоняем."""
        ok, err = self._one('virus.jpg', b'MZ\x90\x00\x03' + b'\x00' * 32, 'image/jpeg')
        self.assertFalse(ok)
        self.assertIn('__all__', err)

    def test_disallowed_extension_rejected(self):
        ok, err = self._one('doc.docx', PDF, 'application/pdf')
        self.assertFalse(ok)
        self.assertIn('__all__', err)

    def test_svg_rejected(self):
        """SVG может нести скрипт — не в белом списке расширений."""
        ok, err = self._one('x.svg', b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', 'image/svg+xml')
        self.assertFalse(ok)

    def test_too_big_rejected(self):
        big = JPEG + b'\x00' * (10 * 1024 * 1024 + 10)
        ok, err = self._one('big.jpg', big, 'image/jpeg')
        self.assertFalse(ok)
        self.assertIn('__all__', err)

    # --- необязательность блока ---
    def test_all_empty_ok(self):
        ok, err = validate({}, {})
        self.assertTrue(ok, err)

    def test_name_without_file_rejected(self):
        ok, err = validate({}, {'cert_name_1': 'X', 'cert_issuer_1': 'Y'})
        self.assertFalse(ok)
        self.assertIn('__all__', err)

    # --- несколько сертификатов ---
    def test_two_certs_ok(self):
        ok, err = validate(
            {
                'cert_file_1': make('a.jpg', JPEG, 'image/jpeg'),
                'cert_file_2': make('b.heic', HEIC, 'image/heic'),
            },
            {
                'cert_name_1': 'IELTS', 'cert_issuer_1': 'British Council',
                'cert_name_2': 'Диплом', 'cert_issuer_2': 'НУУз',
            },
        )
        self.assertTrue(ok, err)
