"""
Материалы урока (LessonFile): загрузка/список/удаление файлов внутри кабинета.

Архитектура повторяет видео-визитку (см. video_views.py):
- Файл грузится НАПРЯМУЮ в S3/R2 через presigned PUT — Django не пропускает
  бинарные данные через себя.
- Django хранит только метаданные и публичную ссылку (модель LessonFile).

Доступ: только участники брони (учитель и ученик). Удалять файл может тот,
кто его загрузил. Формат и размер валидируются по белому списку из settings.
"""
import json
import uuid
import logging

from botocore.exceptions import ClientError

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_http_methods

from .models import Booking, LessonFile
from .video_views import _get_s3_client

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _participant(user, booking):
    """Возвращает (is_participant, is_teacher) для пользователя и брони."""
    tp = getattr(user, 'teacher_profile', None)
    is_teacher = bool(tp and booking.slot.teacher_id == tp.pk)
    is_student = (booking.student_id == user.pk)
    return (is_teacher or is_student), is_teacher


def _storage_configured() -> bool:
    return bool(getattr(settings, 'S3_BUCKET_NAME', '') and getattr(settings, 'S3_PUBLIC_URL', ''))


def _file_to_dict(f: LessonFile, *, current_user_id=None) -> dict:
    return {
        'id': f.pk,
        'file_name': f.file_name,
        'file_url': f.file_url,
        'content_type': f.content_type,
        'size': f.size,
        'uploaded_by_id': f.uploaded_by_id,
        'can_delete': (current_user_id is not None and f.uploaded_by_id == current_user_id),
        'created_at': f.created_at.isoformat(),
    }


def _get_booking_for_participant(request, booking_id):
    """Загружает бронь и проверяет, что пользователь — её участник.

    Возвращает (booking, is_teacher) или (None, error_response).
    """
    booking = get_object_or_404(
        Booking.objects.select_related('slot__teacher'), pk=booking_id,
    )
    is_participant, is_teacher = _participant(request.user, booking)
    if not is_participant:
        return None, JsonResponse({'error': _('Доступ запрещён')}, status=403)
    return (booking, is_teacher), None


# --------------------------------------------------------------------------- #
# views
# --------------------------------------------------------------------------- #

@login_required
@require_http_methods(['GET'])
def lesson_file_list(request, booking_id):
    """Список материалов урока (для обеих сторон)."""
    result, err = _get_booking_for_participant(request, booking_id)
    if err:
        return err
    booking, _is_teacher = result
    files = LessonFile.objects.filter(booking=booking).select_related('uploaded_by')
    return JsonResponse({
        'files': [_file_to_dict(f, current_user_id=request.user.pk) for f in files],
    })


@login_required
@require_POST
def lesson_file_presigned_url(request, booking_id):
    """Presigned PUT для прямой загрузки файла урока в S3/R2."""
    result, err = _get_booking_for_participant(request, booking_id)
    if err:
        return err
    booking, _is_teacher = result

    if not _storage_configured():
        return JsonResponse({'error': _('Загрузка файлов временно недоступна')}, status=503)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': _('Некорректный JSON')}, status=400)

    content_type = (data.get('content_type') or '').strip()
    file_name = (data.get('file_name') or '').strip()
    try:
        file_size = int(data.get('file_size') or 0)
    except (TypeError, ValueError):
        file_size = 0

    allowed = settings.LESSON_FILE_ALLOWED_TYPES
    ext_to_mime = settings.LESSON_FILE_EXT_TO_MIME
    # Канонический MIME: сначала по заявленному типу, иначе по расширению имени
    # (телефоны/некоторые браузеры шлют пустой или application/octet-stream тип —
    # из-за этого валидный PDF/книга отклонялись как «недопустимый формат»).
    if content_type in allowed:
        canonical_ct = content_type
    else:
        name_ext = file_name.rsplit('.', 1)[-1].lower() if '.' in file_name else ''
        canonical_ct = ext_to_mime.get(name_ext, '')
    if canonical_ct not in allowed:
        return JsonResponse(
            {'error': _('Недопустимый формат файла')},
            status=400,
        )

    max_bytes = settings.LESSON_FILE_MAX_SIZE_MB * 1024 * 1024
    if file_size <= 0:
        return JsonResponse({'error': _('Размер файла не указан')}, status=400)
    if file_size > max_bytes:
        return JsonResponse(
            {'error': _('Максимальный размер файла: %(mb)sMB') % {'mb': settings.LESSON_FILE_MAX_SIZE_MB}},
            status=400,
        )

    ext = allowed[canonical_ct]
    file_key = f"lessons/{booking.pk}/{uuid.uuid4().hex}.{ext}"

    try:
        s3_client = _get_s3_client()
        # ВАЖНО: подписываем тем же ContentType, которым клиент затем сделает PUT
        # (канонический). Иначе подпись не сойдётся и хранилище отклонит загрузку.
        upload_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': settings.S3_BUCKET_NAME,
                'Key': file_key,
                'ContentType': canonical_ct,
                'ContentLength': file_size,
            },
            ExpiresIn=settings.LESSON_FILE_PRESIGNED_URL_EXPIRY,
        )
    except ClientError as e:
        logger.error(f"Lesson file presigned URL failed: {e}", exc_info=True)
        return JsonResponse({'error': _('Не удалось создать ссылку для загрузки')}, status=500)

    public_url = settings.S3_PUBLIC_URL.rstrip('/')
    file_url = f"{public_url}/{file_key}"

    return JsonResponse({
        'upload_url': upload_url,
        'file_url': file_url,
        'file_key': file_key,
        'file_name': file_name[:255],
        'content_type': canonical_ct,
        'size': file_size,
    })


@login_required
@require_POST
def lesson_file_save(request, booking_id):
    """Сохранить метаданные файла после успешной загрузки в хранилище."""
    result, err = _get_booking_for_participant(request, booking_id)
    if err:
        return err
    booking, _is_teacher = result

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': _('Некорректный JSON')}, status=400)

    file_url = (data.get('file_url') or '').strip()
    file_key = (data.get('file_key') or '').strip()
    file_name = (data.get('file_name') or '').strip()[:255]
    content_type = (data.get('content_type') or '').strip()[:120]
    try:
        size = int(data.get('size') or 0)
    except (TypeError, ValueError):
        size = 0

    if not file_url or not file_key or not file_name:
        return JsonResponse({'error': _('Недостаточно данных о файле')}, status=400)

    # IDOR-защита: ключ и URL обязаны лежать в пределах пути ЭТОЙ брони.
    expected_prefix = settings.S3_PUBLIC_URL.rstrip('/')
    booking_path = f"lessons/{booking.pk}/"
    if not file_key.startswith(booking_path):
        return JsonResponse({'error': _('Недопустимый путь файла')}, status=400)
    if file_url != f"{expected_prefix}/{file_key}":
        return JsonResponse({'error': _('Недопустимый URL файла')}, status=400)
    if content_type and content_type not in settings.LESSON_FILE_ALLOWED_TYPES:
        return JsonResponse({'error': _('Недопустимый формат файла')}, status=400)

    lf = LessonFile.objects.create(
        booking=booking,
        uploaded_by=request.user,
        file_name=file_name,
        file_key=file_key,
        file_url=file_url,
        content_type=content_type,
        size=max(0, size),
    )
    return JsonResponse({'status': 'ok', 'file': _file_to_dict(lf, current_user_id=request.user.pk)})


@login_required
@require_POST
def lesson_file_delete(request, booking_id, file_id):
    """Удалить файл урока. Разрешено только тому, кто его загрузил."""
    result, err = _get_booking_for_participant(request, booking_id)
    if err:
        return err
    booking, _is_teacher = result

    lf = get_object_or_404(LessonFile, pk=file_id, booking=booking)
    if lf.uploaded_by_id != request.user.pk:
        return JsonResponse({'error': _('Удалить файл может только тот, кто его загрузил')}, status=403)

    # Удаляем из хранилища (не критично, если не получилось — метаданные всё равно убираем).
    try:
        if _storage_configured():
            s3_client = _get_s3_client()
            s3_client.delete_object(Bucket=settings.S3_BUCKET_NAME, Key=lf.file_key)
    except Exception as e:
        logger.warning(f"Lesson file storage delete failed for {lf.pk}: {e}")

    lf.delete()
    return JsonResponse({'status': 'ok'})
