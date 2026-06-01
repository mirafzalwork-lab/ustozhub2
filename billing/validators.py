"""Валидаторы файлов для домашних заданий.

Используется и в форме (preflight), и при сохранении (signal/save).
"""
from __future__ import annotations

import os

from django.core.exceptions import ValidationError


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
        raise ValidationError('Файл пустой.')

    if size > MAX_FILE_SIZE_BYTES:
        mb = size / (1024 * 1024)
        raise ValidationError(
            f'Файл слишком большой: {mb:.1f} MB (максимум {MAX_FILE_SIZE_BYTES // (1024*1024)} MB).'
        )

    ext = os.path.splitext(name)[1].lstrip('.').lower()
    if not ext:
        raise ValidationError(f'У файла «{name}» не указано расширение.')

    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(
            f'Расширение .{ext} не разрешено. Допустимы: pdf, doc/docx, xls/xlsx, '
            f'txt, jpg/png, mp4, zip и др.'
        )

    expected_mimes = EXTENSION_MIME_HINTS.get(ext)
    if expected_mimes and mime and mime not in expected_mimes:
        # Не падаем жёстко — браузеры иногда говорят application/octet-stream.
        # Жёсткий контроль расширения уже сделан выше; MIME — мягкое предупреждение.
        if 'octet-stream' not in mime:
            raise ValidationError(
                f'MIME-тип «{mime}» не соответствует расширению .{ext}.'
            )
