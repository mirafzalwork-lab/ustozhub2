"""Валидаторы файлов для домашних заданий.

Используется и в форме (preflight), и при сохранении (signal/save).
"""
from __future__ import annotations

import os

from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

ALLOWED_EXTENSIONS = {
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'txt', 'rtf',
    'jpg', 'jpeg', 'png', 'gif', 'webp',
    'mp4', 'mov', 'avi', 'webm',
    'mp3', 'wav', 'ogg',
    'zip', 'rar', '7z',
}

# Соответствие расширения → ожидаемые MIME-типы (для базовой sanity-check).
EXTENSION_MIME_HINTS = {
    'pdf':  ('application/pdf',),
    'docx': ('application/vnd.openxmlformats-officedocument.wordprocessingml.document',
             'application/octet-stream'),
    'xlsx': ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
             'application/octet-stream'),
    'jpg':  ('image/jpeg',),
    'jpeg': ('image/jpeg',),
    'png':  ('image/png',),
    'gif':  ('image/gif',),
    'webp': ('image/webp',),
    'mp4':  ('video/mp4',),
    'txt':  ('text/plain',),
    'zip':  ('application/zip', 'application/octet-stream'),
}


def validate_homework_file(uploaded_file) -> None:
    """Валидация загружаемого файла: размер + расширение + MIME.

    Бросает ValidationError при нарушении.
    """
    name = getattr(uploaded_file, 'name', '') or ''
    size = getattr(uploaded_file, 'size', 0) or 0
    mime = getattr(uploaded_file, 'content_type', '') or ''

    if size <= 0:
        raise ValidationError(_('Файл пустой.'))

    if size > MAX_FILE_SIZE_BYTES:
        mb = size / (1024 * 1024)
        raise ValidationError(
            _('Файл слишком большой: %(mb).1f MB (максимум %(max)s MB).')
            % {'mb': mb, 'max': MAX_FILE_SIZE_BYTES // (1024 * 1024)}
        )

    ext = os.path.splitext(name)[1].lstrip('.').lower()
    if not ext:
        raise ValidationError(_('У файла «%(name)s» не указано расширение.') % {'name': name})

    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            _('Расширение .%(ext)s не разрешено. Допустимы: pdf, doc/docx, xls/xlsx, '
              'txt, jpg/png, mp4, zip и др.') % {'ext': ext}
        )

    expected_mimes = EXTENSION_MIME_HINTS.get(ext)
    if expected_mimes and mime and mime not in expected_mimes:
        # Не падаем жёстко — браузеры иногда говорят application/octet-stream.
        # Жёсткий контроль расширения уже сделан выше; MIME — мягкое предупреждение.
        if 'octet-stream' not in mime:
            raise ValidationError(
                _('MIME-тип «%(mime)s» не соответствует расширению .%(ext)s.')
                % {'mime': mime, 'ext': ext}
            )
