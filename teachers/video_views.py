"""
Video upload views for teacher video business card.

Architecture:
- Video files are uploaded directly to S3-compatible storage (Cloudflare R2 / Amazon S3)
  via presigned URLs — Django server never handles the video binary data.
- Django only stores the public URL of the uploaded video.
"""
import uuid
import logging

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST, require_http_methods

from .models import TeacherProfile

logger = logging.getLogger(__name__)


def _get_s3_client():
    """Create and return a boto3 S3 client configured for R2/S3."""
    return boto3.client(
        's3',
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region_name=settings.S3_REGION,
        config=BotoConfig(signature_version='s3v4'),
    )


@login_required
@require_POST
def video_presigned_url(request):
    """
    Generate a presigned URL for direct video upload to S3/R2.

    POST JSON body:
        - content_type: string (must be video/mp4)
        - file_size: int (bytes, max 50MB)

    Returns JSON:
        - upload_url: presigned PUT URL
        - file_url: public URL where the video will be accessible
        - file_key: S3 object key
    """
    import json

    # Only teachers can upload videos
    if request.user.user_type != 'teacher':
        return JsonResponse({'error': _('Только учителя могут загружать видео')}, status=403)

    try:
        teacher_profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        return JsonResponse({'error': _('Профиль учителя не найден')}, status=404)

    # Parse request
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': _('Некорректный JSON')}, status=400)

    content_type = data.get('content_type', '')
    file_size = data.get('file_size', 0)

    # Validate content type
    if content_type not in settings.VIDEO_ALLOWED_CONTENT_TYPES:
        return JsonResponse(
            {'error': _('Допустимый формат: MP4 (video/mp4)')},
            status=400,
        )

    # Validate file size
    max_bytes = settings.VIDEO_MAX_SIZE_MB * 1024 * 1024
    if not file_size or file_size <= 0:
        return JsonResponse({'error': _('Размер файла не указан')}, status=400)
    if file_size > max_bytes:
        return JsonResponse(
            {'error': _('Максимальный размер файла: %(mb)sMB') % {'mb': settings.VIDEO_MAX_SIZE_MB}},
            status=400,
        )

    # Generate unique file key
    file_key = f"videos/teachers/{teacher_profile.pk}/{uuid.uuid4().hex}.mp4"

    # Generate presigned URL
    try:
        s3_client = _get_s3_client()
        upload_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': settings.S3_BUCKET_NAME,
                'Key': file_key,
                'ContentType': content_type,
                'ContentLength': file_size,
            },
            ExpiresIn=settings.VIDEO_PRESIGNED_URL_EXPIRY,
        )
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL: {e}", exc_info=True)
        return JsonResponse({'error': _('Не удалось создать ссылку для загрузки')}, status=500)

    # Build public file URL
    public_url = settings.S3_PUBLIC_URL.rstrip('/')
    file_url = f"{public_url}/{file_key}"

    return JsonResponse({
        'upload_url': upload_url,
        'file_url': file_url,
        'file_key': file_key,
    })


@login_required
@require_POST
def video_save(request):
    """
    Save video URL to teacher profile after successful upload.

    POST JSON body:
        - file_url: string (the public URL returned by video_presigned_url)
    """
    import json

    if request.user.user_type != 'teacher':
        return JsonResponse({'error': _('Только учителя могут загружать видео')}, status=403)

    try:
        teacher_profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        return JsonResponse({'error': _('Профиль учителя не найден')}, status=404)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': _('Некорректный JSON')}, status=400)

    file_url = data.get('file_url', '').strip()

    if not file_url:
        return JsonResponse({'error': _('URL видео не указан')}, status=400)

    # Validate that the URL points to our storage
    expected_prefix = settings.S3_PUBLIC_URL.rstrip('/')
    if not file_url.startswith(expected_prefix):
        return JsonResponse({'error': _('Недопустимый URL видео')}, status=400)

    # IDOR-защита: разрешаем ТОЛЬКО собственный путь учителя или загрузку
    # текущей сессии (видео, залитое в этой же сессии регистрации).
    # Раньше подстрочная проверка "new/" позволяла присвоить чужое видео.
    session_key = request.session.session_key or ''
    allowed_prefixes = [f"{expected_prefix}/videos/teachers/{teacher_profile.pk}/"]
    if session_key:
        allowed_prefixes.append(f"{expected_prefix}/videos/teachers/new/{session_key}/")
    if not any(file_url.startswith(p) for p in allowed_prefixes):
        return JsonResponse({'error': _('Нет доступа к этому файлу')}, status=403)

    # Delete old video from storage if replacing
    if teacher_profile.video_url:
        _delete_video_from_storage(teacher_profile.video_url)

    teacher_profile.video_url = file_url
    teacher_profile.save(update_fields=['video_url'])

    return JsonResponse({'status': 'ok', 'video_url': file_url})


@login_required
@require_POST
def video_delete(request):
    """
    Delete video from teacher profile and from S3 storage.
    """
    if request.user.user_type != 'teacher':
        return JsonResponse({'error': _('Только учителя могут управлять видео')}, status=403)

    try:
        teacher_profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        return JsonResponse({'error': _('Профиль учителя не найден')}, status=404)

    if not teacher_profile.video_url:
        return JsonResponse({'error': _('Видео не найдено')}, status=404)

    # Delete from storage
    _delete_video_from_storage(teacher_profile.video_url)

    # Clear URL in DB
    teacher_profile.video_url = None
    teacher_profile.save(update_fields=['video_url'])

    return JsonResponse({'status': 'ok'})


@require_POST
def video_presigned_url_register(request):
    """
    Generate a presigned URL for video upload during registration (no auth required).
    Uses a session-based identifier for the upload path to prevent conflicts.
    """
    import json

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': _('Некорректный JSON')}, status=400)

    content_type = data.get('content_type', '')
    file_size = data.get('file_size', 0)

    if content_type not in settings.VIDEO_ALLOWED_CONTENT_TYPES:
        return JsonResponse({'error': _('Допустимый формат: MP4 (video/mp4)')}, status=400)

    max_bytes = settings.VIDEO_MAX_SIZE_MB * 1024 * 1024
    if not file_size or file_size <= 0:
        return JsonResponse({'error': _('Размер файла не указан')}, status=400)
    if file_size > max_bytes:
        return JsonResponse(
            {'error': _('Максимальный размер файла: %(mb)sMB') % {'mb': settings.VIDEO_MAX_SIZE_MB}},
            status=400,
        )

    # Use session key as temporary identifier
    session_key = request.session.session_key
    if not session_key:
        request.session.create()
        session_key = request.session.session_key

    file_key = f"videos/teachers/new/{session_key}/{uuid.uuid4().hex}.mp4"

    try:
        s3_client = _get_s3_client()
        upload_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': settings.S3_BUCKET_NAME,
                'Key': file_key,
                'ContentType': content_type,
                'ContentLength': file_size,
            },
            ExpiresIn=settings.VIDEO_PRESIGNED_URL_EXPIRY,
        )
    except ClientError as e:
        logger.error(f"Failed to generate presigned URL (register): {e}", exc_info=True)
        return JsonResponse({'error': _('Не удалось создать ссылку для загрузки')}, status=500)

    public_url = settings.S3_PUBLIC_URL.rstrip('/')
    file_url = f"{public_url}/{file_key}"

    return JsonResponse({
        'upload_url': upload_url,
        'file_url': file_url,
        'file_key': file_key,
    })


def _delete_video_from_storage(video_url):
    """Delete a video file from S3/R2 storage by its public URL."""
    try:
        public_url = settings.S3_PUBLIC_URL.rstrip('/')
        file_key = video_url.replace(f"{public_url}/", '', 1)
        if file_key:
            s3_client = _get_s3_client()
            s3_client.delete_object(
                Bucket=settings.S3_BUCKET_NAME,
                Key=file_key,
            )
    except Exception as e:
        logger.error(f"Failed to delete video from storage: {e}", exc_info=True)
