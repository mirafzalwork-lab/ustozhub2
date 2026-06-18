from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import F, Q, Min, Max, Avg, Count, Sum, Case, When, Value, IntegerField
from django.db.models.functions import Coalesce
from django.contrib.auth.decorators import login_required
from django.contrib.admin.views.decorators import staff_member_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.db import transaction
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.core.cache import cache
from django.conf import settings
from django.contrib.auth import login, logout, authenticate
from django.utils import timezone
from django.http import JsonResponse, HttpResponse, Http404
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
import csv
import logging
import random
from datetime import datetime, timedelta

# Глобальный logger для всего модуля
logger = logging.getLogger(__name__)

from .models import (
    TeacherProfile, StudentProfile, Subject, City, ProfileView,
    TeacherSubject, Certificate, User, Favorite, FavoriteStudent,
    Conversation, Message, Review, ViewCounter, TelegramUser,
    SubjectCategory, SubjectSearchLog, Notification, NotificationRead,
    DailyReminderTemplate, Booking, LeadOptOut,
)
from .search import (
    normalize_query, build_teacher_search_q,
    build_teacher_relevance_annotations, build_subject_search_q,
    build_subject_relevance_annotation, build_student_search_q,
)
from .forms import (
    TeacherRegistrationForm,
    TeacherSubjectsForm,
    CertificateUploadForm,
    MessageForm,
    LoginForm,
    StudentRegistrationForm,
    TeacherProfileEditForm,
    StudentProfileEditForm,
    UserProfileEditForm,
    TeacherSubjectEditForm,
    GoogleStudentOnboardingForm,
)

def _safe_int(value, default=None):
    """Safely convert a query parameter to int, returning default on failure."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=None):
    """Safely convert a query parameter to float, returning default on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def track_view(request, page_name):
    ViewCounter.add_view(request, page_name)


def _get_cached_subjects():
    """Return cached list of active subjects for filter dropdowns."""
    result = cache.get('all_subjects')
    if result is None:
        result = list(Subject.objects.filter(is_active=True).only('id', 'name').order_by('name'))
        cache.set('all_subjects', result, getattr(settings, 'CACHE_TTL', 900))
    return result


def _get_cached_cities():
    """Return cached list of active cities for filter dropdowns."""
    result = cache.get('all_cities')
    if result is None:
        result = list(City.objects.filter(is_active=True).only('id', 'name').order_by('name'))
        cache.set('all_cities', result, getattr(settings, 'CACHE_TTL', 900))
    return result


def _get_user_conversation(user, conversation_id, require_active=True):
    """
    Get a conversation with access check based on user type.
    Returns the Conversation or raises Http404.
    """
    filters = {'id': conversation_id}
    if require_active:
        filters['is_active'] = True

    if user.user_type == 'teacher':
        filters['teacher'] = user.teacher_profile
    else:
        filters['student'] = user

    return get_object_or_404(Conversation, **filters)


def can_view_contact_info(request, profile_owner):
    """
    Определяет, может ли пользователь видеть контактную информацию.
    
    Args:
        request: HTTP запрос
        profile_owner: владелец профиля (User объект)
    
    Returns:
        bool: True если пользователь может видеть контакты, False иначе
    """
    # Гость (не авторизованный пользователь) НЕ может видеть контакты
    if not request.user.is_authenticated:
        return False
    
    # Владелец профиля всегда видит свои контакты
    if request.user == profile_owner:
        return True
    
    # Администратор всегда видит контакты
    if request.user.is_staff or request.user.is_superuser:
        return True

    # Анти-обход (v2 Шаг 7): контакты открываются только после порога доверия —
    # когда между учеником и учителем проведено достаточно оплаченных уроков
    # (платформа уже заработала на связке). До этого — общение только в чате.
    try:
        from .contact_filter import paid_lessons_between, should_mask_for_pair
        viewer = request.user
        owner = profile_owner
        teacher_profile = None
        student_user = None
        if getattr(viewer, 'user_type', None) == 'teacher' and getattr(owner, 'user_type', None) == 'student':
            teacher_profile = getattr(viewer, 'teacher_profile', None)
            student_user = owner
        elif getattr(viewer, 'user_type', None) == 'student' and getattr(owner, 'user_type', None) == 'teacher':
            teacher_profile = getattr(owner, 'teacher_profile', None)
            student_user = viewer
        if teacher_profile and student_user:
            # should_mask=False ⇒ порог пройден ⇒ контакты можно показать.
            return not should_mask_for_pair(student_user, teacher_profile)
    except Exception:
        pass

    # По умолчанию — контакты скрыты, общение только через платформу.
    return False


def get_client_ip(request):
    """Получить IP адрес клиента.

    Доверять можно только ПОСЛЕДНЕМУ элементу X-Forwarded-For — его добавил
    наш nginx ($proxy_add_x_forwarded_for); левые элементы клиент задаёт сам.
    Первый элемент позволял накручивать просмотры/обходить дедуп статистики
    подделкой заголовка (аудит 2026-06-10 M13; так же сделано в billing).
    """
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[-1].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def record_profile_view(request, profile, profile_type):
    """
    Записать просмотр профиля
    
    Args:
        request: HTTP запрос
        profile: объект TeacherProfile или StudentProfile
        profile_type: 'teacher' или 'student'
    """
    # Получаем IP и User Agent
    ip_address = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    
    # Получаем текущего пользователя (если авторизован)
    viewer_user = request.user if request.user.is_authenticated else None
    
    # Проверяем, не смотрит ли пользователь свой собственный профиль
    if viewer_user:
        if profile_type == 'teacher' and hasattr(viewer_user, 'teacher_profile'):
            if viewer_user.teacher_profile == profile:
                return  # Не записываем просмотр своего профиля
        elif profile_type == 'student' and hasattr(viewer_user, 'student_profile'):
            if viewer_user.student_profile == profile:
                return  # Не записываем просмотр своего профиля
    
    # Дедуплицируем по (профиль, viewer, дата): если просмотр за сегодня от того же
    # пользователя/IP уже есть — увеличиваем views_count, иначе создаём новую строку.
    # Это снимает DDoS-нагрузку с таблицы (F5-флуд) и не раздувает её.
    try:
        today = timezone.localdate()
        lookup = {
            'profile_type': profile_type,
            'viewed_date': today,
        }
        if profile_type == 'teacher':
            lookup['teacher_profile'] = profile
        else:
            lookup['student_profile'] = profile

        if viewer_user is not None:
            lookup['viewer_user'] = viewer_user
            lookup['viewer_ip__isnull'] = False  # не критично, но фильтрует чище
            # Уберём кавычки isnull-фильтра — он только усложняет:
            lookup.pop('viewer_ip__isnull')
        else:
            lookup['viewer_user__isnull'] = True
            lookup['viewer_ip'] = ip_address

        defaults = {
            'user_agent': user_agent[:500] if user_agent else '',
            'last_viewed_at': timezone.now(),
        }
        # update_or_create НЕ умеет инкрементить F-выражениями в одном запросе,
        # поэтому делаем get_or_create + UPDATE с F('views_count')+1 при существовании.
        view, created = ProfileView.objects.get_or_create(
            defaults={**defaults, 'viewer_ip': ip_address, 'viewer_user': viewer_user},
            **lookup,
        )
        if not created:
            ProfileView.objects.filter(pk=view.pk).update(
                views_count=F('views_count') + 1,
                last_viewed_at=timezone.now(),
            )
    except Exception as e:
        # Логируем ошибку, но не прерываем работу приложения
        logger.error(f"Error recording profile view: {e}", exc_info=True)


def _apply_sort(queryset, sort_by):
    """Применяет сортировку к queryset учителей. Рекомендуемые НЕ выносятся наверх —
    они смешаны с остальными в обычном порядке (отдельно показываются в слайдере)."""
    if sort_by == 'price_low':
        return queryset.annotate(
            min_price=Min('teachersubject__hourly_rate')
        ).order_by('min_price', '-ranking_score')
    elif sort_by == 'price_high':
        return queryset.annotate(
            min_price=Min('teachersubject__hourly_rate')
        ).order_by('-min_price', '-ranking_score')
    elif sort_by == 'rating':
        return queryset.order_by('-rating', '-total_reviews', '-ranking_score')
    elif sort_by == 'experience':
        return queryset.order_by('-experience_years', '-rating')
    elif sort_by == 'newest':
        return queryset.order_by('-created_at')
    else:  # recommended (default)
        return queryset.order_by('-ranking_score', '-rating', '-created_at')


def home(request):
    """
    Главная страница с учителями
    """

    track_view(request, 'home')
    # Базовый queryset: все активные и одобренные учителя (включая рекомендуемых).
    # Рекомендуемые показываются дополнительно в слайдере сверху, но также присутствуют
    # в общей сетке — в обычном порядке, без приоритета наверху.
    # Учителя без TeacherSubject не имеют ни цен, ни предметов — показывать бесполезно.
    teachers = TeacherProfile.objects.filter(
        is_active=True, moderation_status='approved',
        teachersubject__isnull=False,
    ).select_related(
        'user', 'city'
    ).prefetch_related(
        'subjects', 'teachersubject_set__subject', 'reviews'
    ).distinct()
    
    # Получаем параметры фильтрации
    subject_id = request.GET.get('subject')
    city_id = request.GET.get('city')
    teaching_format = request.GET.get('format')
    min_price = request.GET.get('min_price')
    max_price = request.GET.get('max_price')
    min_rating = request.GET.get('min_rating')
    min_experience = request.GET.get('min_experience')
    search_query = request.GET.get('search') or request.GET.get('q')
    suggest = request.GET.get('suggest')
    sort_by = request.GET.get('sort', 'recommended')
    
    # Применяем фильтры
    if subject_id:
        teachers = teachers.filter(subjects__id=subject_id)
    elif suggest and request.user.is_authenticated and request.user.user_type == 'student':
        # Авто-подбор по желаемым предметам ученика
        try:
            desired_subjects = request.user.student_profile.desired_subjects.all()
            if desired_subjects.exists():
                teachers = teachers.filter(subjects__in=desired_subjects)
        except StudentProfile.DoesNotExist:
            pass
    
    if city_id:
        teachers = teachers.filter(city_id=city_id)
    
    if teaching_format:
        teachers = teachers.filter(teaching_format=teaching_format)
    
    if min_rating:
        val = _safe_float(min_rating)
        if val is not None:
            teachers = teachers.filter(rating__gte=val)

    if min_experience:
        val = _safe_int(min_experience)
        if val is not None:
            teachers = teachers.filter(experience_years__gte=val)

    # Фильтр по цене (через свя��анную модель TeacherSubject)
    if min_price:
        val = _safe_float(min_price)
        if val is not None:
            teachers = teachers.filter(teachersubject__hourly_rate__gte=val)

    if max_price:
        val = _safe_float(max_price)
        if val is not None:
            teachers = teachers.filter(teachersubject__hourly_rate__lte=val)
    
    # ========== SMART SEARCH WITH SYNONYMS & RELEVANCE RANKING ==========
    # Умный поиск с поддержкой синонимов (RU/EN/UZ), сокращений и ранжированием
    if search_query:
        q = normalize_query(search_query)
        if q:
            # Получаем аннотации релевантности (с учётом синонимов)
            subject_rank, name_rank, bio_rank = build_teacher_relevance_annotations(search_query)

            # Annotate with relevance scores and filter
            teachers = teachers.annotate(
                subject_rank=subject_rank,
                name_rank=name_rank,
                bio_rank=bio_rank,
            ).annotate(
                # Weighted relevance: subjects (3x), names (2x), bio (1x)
                relevance=F('subject_rank') * 3 + F('name_rank') * 2 + F('bio_rank')
            ).filter(
                build_teacher_search_q(search_query)
            ).distinct()

            # При поиске: сортировка по релевантности (featured не выносятся наверх)
            if sort_by == 'price_low':
                teachers = teachers.annotate(
                    min_price=Min('teachersubject__hourly_rate')
                ).order_by('-relevance', 'min_price')
            elif sort_by == 'price_high':
                teachers = teachers.annotate(
                    min_price=Min('teachersubject__hourly_rate')
                ).order_by('-relevance', '-min_price')
            elif sort_by == 'rating':
                teachers = teachers.order_by('-relevance', '-rating', '-total_reviews')
            elif sort_by == 'experience':
                teachers = teachers.order_by('-relevance', '-experience_years', '-rating')
            elif sort_by == 'newest':
                teachers = teachers.order_by('-relevance', '-created_at')
            else:  # recommended (default)
                teachers = teachers.order_by('-relevance', '-ranking_score', '-rating', '-total_reviews')
        else:
            teachers = teachers.distinct()
            teachers = _apply_sort(teachers, sort_by)
    else:
        teachers = teachers.distinct()
        teachers = _apply_sort(teachers, sort_by)

    # ========== УМНЫЙ SHUFFLE ДЛЯ СЕТКИ (без повторов между визитами) ==========
    # При дефолтной сортировке и отсутствии фильтров — перемешиваем порядок,
    # чтобы разные юзеры/сессии видели разных учителей первыми.
    # Seed живёт в session и ротируется раз в час → пагинация стабильна
    # внутри сессии, но через час порядок обновляется автоматически.
    _has_filters = any([
        subject_id, city_id, teaching_format, min_price, max_price,
        min_rating, min_experience, search_query, suggest
    ])
    # Featured показываем в слайдере «Рекомендуемые» сверху; в основном гриде
    # прячем их ТОЛЬКО на чистой главной (без поиска/фильтров), чтобы не было
    # дублей слайдер+список. При поиске/фильтрации featured участвуют в выдаче.
    if not _has_filters:
        teachers = teachers.exclude(is_featured=True)
    _shuffled = False
    if sort_by == 'recommended' and not _has_filters:
        now_ts = int(timezone.now().timestamp())
        if request.user.is_authenticated:
            seed = request.session.get('teacher_shuffle_seed')
            seed_ts = request.session.get('teacher_shuffle_ts', 0)
            if not seed or (now_ts - seed_ts) > 3600:
                seed = random.randint(1, 10 ** 9)
                request.session['teacher_shuffle_seed'] = seed
                request.session['teacher_shuffle_ts'] = now_ts
        else:
            # Аноним: seed = час + IP, БЕЗ записи в сессию — раньше каждый
            # гость главной получал строку в django_session (аудит 2026-06-10
            # H13). Порядок стабилен в пределах часа (пагинация не скачет).
            import zlib
            seed = zlib.crc32(
                f'{get_client_ip(request)}:{now_ts // 3600}'.encode()
            ) or 1

        cache_key = f'teacher_shuffle_ids_{seed}'
        shuffled_ids = cache.get(cache_key)
        if shuffled_ids is None:
            shuffled_ids = list(teachers.values_list('id', flat=True))
            random.Random(seed).shuffle(shuffled_ids)
            cache.set(cache_key, shuffled_ids, 3600)

        if shuffled_ids:
            # Пагинируем СПИСОК id; объекты добираем ниже только для текущей
            # страницы (12 шт.). Раньше ORDER BY CASE WHEN строился по ВСЕМ id
            # учителей и ехал в COUNT пагинатора и page-запрос — на тысячах
            # учителей это был SQL-монстр на каждый просмотр главной (H13).
            teachers = shuffled_ids
            _shuffled = True

    # ========== ПАГИНАЦИЯ ==========
    # Создаем объект пагинатора (12 учителей на страницу)
    paginator = Paginator(teachers, 12)
    page = request.GET.get('page', 1)
    
    try:
        teachers_page = paginator.page(page)
    except PageNotAnInteger:
        # Если page не является целым числом, показываем первую страницу
        teachers_page = paginator.page(1)
    except EmptyPage:
        # Если page выходит за пределы диапазона, показываем последнюю страницу
        teachers_page = paginator.page(paginator.num_pages)

    if _shuffled:
        # Подменяем id текущей страницы реальными объектами: CASE-сортировка
        # теперь только по 12 элементам, а не по всей таблице.
        _page_ids = list(teachers_page.object_list)
        _order = Case(
            *[When(pk=pk, then=pos) for pos, pk in enumerate(_page_ids)],
            output_field=IntegerField(),
        )
        teachers_page.object_list = (
            TeacherProfile.objects.filter(id__in=_page_ids)
            .select_related('user', 'city')
            .prefetch_related('subjects', 'teachersubject_set__subject', 'reviews')
            .order_by(_order)
        )

    all_subjects = _get_cached_subjects()
    all_cities = _get_cached_cities()

    # Кэширование диапазона цен
    price_range = cache.get('price_range')
    if price_range is None:
        price_range = TeacherProfile.objects.filter(is_active=True).aggregate(
            min_price=Min('teachersubject__hourly_rate'),
            max_price=Max('teachersubject__hourly_rate')
        )
        cache.set('price_range', price_range, getattr(settings, 'CACHE_TTL', 900))
    
    sort_options = [
        ('recommended', 'Рекомендуемые'),
        ('rating', 'По рейтингу'),
        ('price_low', 'Цена: по возрастанию'),
        ('price_high', 'Цена: по убыванию'),
        ('experience', 'По опыту'),
        ('newest', 'Новые'),
    ]

    # Рекомендуемые учителя — тоже в рандомном порядке на сессию (ротация раз в час).
    featured_qs = TeacherProfile.objects.filter(
        is_active=True, moderation_status='approved', is_featured=True
    ).select_related('user', 'city').prefetch_related(
        'subjects', 'teachersubject_set__subject'
    )

    _now_ts = int(timezone.now().timestamp())
    if request.user.is_authenticated:
        _featured_seed = request.session.get('featured_shuffle_seed')
        _featured_ts = request.session.get('featured_shuffle_ts', 0)
        if not _featured_seed or (_now_ts - _featured_ts) > 3600:
            _featured_seed = random.randint(1, 10 ** 9)
            request.session['featured_shuffle_seed'] = _featured_seed
            request.session['featured_shuffle_ts'] = _now_ts
    else:
        # Аноним: seed без записи в сессию (см. shuffle сетки выше — H13).
        import zlib
        _featured_seed = zlib.crc32(
            f'feat:{get_client_ip(request)}:{_now_ts // 3600}'.encode()
        ) or 1

    _featured_cache_key = f'featured_shuffle_ids_{_featured_seed}'
    _featured_ids = cache.get(_featured_cache_key)
    if _featured_ids is None:
        _featured_ids = list(featured_qs.values_list('id', flat=True))
        random.Random(_featured_seed).shuffle(_featured_ids)
        _featured_ids = _featured_ids[:12]
        cache.set(_featured_cache_key, _featured_ids, 3600)

    if _featured_ids:
        _featured_order = Case(
            *[When(pk=pk, then=pos) for pos, pk in enumerate(_featured_ids)],
            output_field=IntegerField()
        )
        featured_teachers = list(
            featured_qs.filter(id__in=_featured_ids).order_by(_featured_order)
        )
    else:
        featured_teachers = []

    # Метрики доверия для hero-блока гостя (кэш 10 мин — общие, не персональные)
    platform_stats = cache.get('home_platform_stats')
    if platform_stats is None:
        platform_stats = {
            'teachers': TeacherProfile.objects.filter(
                is_active=True, moderation_status='approved', teachersubject__isnull=False
            ).distinct().count(),
            'students': StudentProfile.objects.filter(is_active=True).count(),
            'subjects': len(all_subjects),
        }
        cache.set('home_platform_stats', platform_stats, 600)

    # Избранные учителя текущего ученика (для ♥ на карточках)
    favorite_teacher_ids = set()
    if request.user.is_authenticated and getattr(request.user, 'user_type', None) == 'student':
        favorite_teacher_ids = set(
            Favorite.objects.filter(student=request.user).values_list('teacher_id', flat=True)
        )

    # Активные фильтры в виде чипсов (param — ключ GET для удаления)
    _subj_names = {str(s.id): s.get_display_name() for s in all_subjects}
    _city_names = {str(c.id): c.name for c in all_cities}
    _format_names = {k: v for k, v in TeacherProfile.TEACHING_FORMATS}
    active_filters = []
    if search_query:
        active_filters.append({'param': 'search', 'label': '«%s»' % search_query})
    if subject_id and subject_id in _subj_names:
        active_filters.append({'param': 'subject', 'label': _subj_names[subject_id]})
    if city_id and city_id in _city_names:
        active_filters.append({'param': 'city', 'label': _city_names[city_id]})
    if teaching_format and teaching_format in _format_names:
        active_filters.append({'param': 'format', 'label': _format_names[teaching_format]})
    if min_price:
        active_filters.append({'param': 'min_price', 'label': _('от %(p)s сум') % {'p': min_price}})
    if max_price:
        active_filters.append({'param': 'max_price', 'label': _('до %(p)s сум') % {'p': max_price}})
    if min_rating:
        active_filters.append({'param': 'min_rating', 'label': _('от %(r)s★') % {'r': min_rating}})
    if min_experience:
        active_filters.append({'param': 'min_experience', 'label': _('опыт от %(y)s лет') % {'y': min_experience}})

    # Контентные секции лендинга (только на «чистой» главной — без поиска/фильтров)
    popular_subjects = []
    home_reviews = []
    platform_extra = {}
    if not active_filters:
        popular_subjects = cache.get('home_popular_subjects')
        if popular_subjects is None:
            popular_subjects = list(
                Subject.objects.filter(is_active=True).annotate(
                    n=Count('teacherprofile', filter=Q(
                        teacherprofile__is_active=True,
                        teacherprofile__moderation_status='approved',
                    ), distinct=True)
                ).filter(n__gt=0).order_by('-n')[:8]
            )
            cache.set('home_popular_subjects', popular_subjects, 600)

        home_reviews = list(
            Review.objects.filter(is_verified=True, rating__gte=4)
            .exclude(comment='')
            .select_related('student', 'teacher__user', 'subject')
            .order_by('-created_at')[:6]
        )

        platform_extra = cache.get('home_platform_extra')
        if platform_extra is None:
            _agg = Review.objects.aggregate(avg=Avg('rating'), cnt=Count('id'))
            platform_extra = {
                'reviews_total': _agg['cnt'] or 0,
                'avg_rating': round(_agg['avg'], 1) if _agg['avg'] else None,
            }
            cache.set('home_platform_extra', platform_extra, 600)

    context = {
        'teachers': teachers_page,
        'featured_teachers': featured_teachers,
        'total_teachers': paginator.count,
        'favorite_teacher_ids': favorite_teacher_ids,
        'active_filters': active_filters,
        'popular_subjects': popular_subjects,
        'home_reviews': home_reviews,
        'platform_extra': platform_extra,
        'platform_stats': platform_stats,
        'subjects': all_subjects,
        'cities': all_cities,
        'teaching_formats': TeacherProfile.TEACHING_FORMATS,
        'price_range': price_range,
        'selected_subject': subject_id,
        'selected_city': city_id,
        'selected_format': teaching_format,
        'selected_min_price': min_price,
        'selected_max_price': max_price,
        'selected_min_rating': min_rating,
        'selected_min_experience': min_experience,
        'search_query': search_query,
        'suggest': suggest,
        'sort_by': sort_by,
        'sort_options': sort_options,
    }

    return render(request, 'logic/home.html', context)

@login_required(login_url='login')
def admin_dashboard(request):
    """Dashboard для администратора с полной статистикой платформы"""
    # Проверка прав доступа
    if not request.user.is_staff:
        messages.error(request, _('У вас нет доступа к админ панели'))
        return redirect('home')

    # ~50 агрегатов на загрузку → кэшируем платформенную сводку на 45с
    # (данные общие для всех админов, не персональные).
    from django.core.cache import cache as _cache
    _cached_ctx = _cache.get('admin_dashboard_ctx')
    if _cached_ctx is not None:
        return render(request, 'admin/admin_dashboard.html', _cached_ctx)

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    
    # ========== МЕТРИКИ ПОЛЬЗОВАТЕЛЕЙ ==========
    total_teachers = TeacherProfile.objects.count()
    active_teachers = TeacherProfile.objects.filter(is_active=True).count()
    pending_teachers = TeacherProfile.objects.filter(moderation_status='pending').count()
    approved_teachers = TeacherProfile.objects.filter(moderation_status='approved').count()
    
    total_students = StudentProfile.objects.count()
    active_students = StudentProfile.objects.filter(is_active=True).count()
    
    # Новые регистрации за неделю
    new_teachers_week = TeacherProfile.objects.filter(created_at__gte=week_ago).count()
    new_students_week = StudentProfile.objects.filter(created_at__gte=week_ago).count()
    
    # ========== МЕТРИКИ СООБЩЕНИЙ ==========
    total_messages = Message.objects.count()
    messages_today = Message.objects.filter(created_at__gte=today_start).count()
    messages_week = Message.objects.filter(created_at__gte=week_ago).count()
    unread_messages = Message.objects.filter(is_read=False).count()
    
    # Активные переписки (с сообщениями за последнюю неделю)
    active_conversations = Conversation.objects.filter(
        messages__created_at__gte=week_ago,
        is_active=True
    ).distinct().count()
    
    # ========== МЕТРИКИ ПРОСМОТРОВ ==========
    current_month = now.date().replace(day=1)
    monthly_views = ViewCounter.objects.filter(month=current_month).count()
    
    # Просмотры профилей
    # ProfileView хранит дедуплицированные строки (одна на день/viewer),
    # реальное число просмотров — это SUM(views_count).
    _pv_sum = lambda qs: qs.aggregate(total=Sum('views_count'))['total'] or 0
    total_profile_views = _pv_sum(ProfileView.objects.all())
    profile_views_today = _pv_sum(ProfileView.objects.filter(viewed_at__gte=today_start))
    profile_views_week = _pv_sum(ProfileView.objects.filter(viewed_at__gte=week_ago))
    profile_views_month = _pv_sum(ProfileView.objects.filter(viewed_at__gte=month_ago))
    
    # ========== МЕТРИКИ ОТЗЫВОВ ==========
    total_reviews = Review.objects.count()
    reviews_week = Review.objects.filter(created_at__gte=week_ago).count()
    
    # ========== МЕТРИКИ ИЗБРАННОГО ==========
    total_favorites = Favorite.objects.count() + FavoriteStudent.objects.count()
    
    # ========== TELEGRAM СТАТИСТИКА ==========
    telegram_users = TelegramUser.objects.count()
    telegram_active = TelegramUser.objects.filter(
        notifications_enabled=True,
        started_bot=True
    ).count()
    
    # ========== ПОСЛЕДНИЕ УЧИТЕЛЯ НА МОДЕРАЦИИ ==========
    pending_teachers_list = TeacherProfile.objects.filter(
        moderation_status='pending'
    ).select_related('user').order_by('-created_at')[:10]
    
    # ========== ПОСЛЕДНИЕ СООБЩЕНИЯ ==========
    recent_messages = Message.objects.select_related(
        'sender',
        'conversation__teacher__user',
        'conversation__student',
        'conversation',
    ).order_by('-created_at')[:15]
    
    # ========== ПОСЛЕДНИЕ РЕГИСТРАЦИИ ==========
    recent_teachers = TeacherProfile.objects.select_related('user', 'city').order_by('-created_at')[:8]
    recent_students = StudentProfile.objects.select_related('user', 'city').order_by('-created_at')[:8]

    # ========== ВИДЕО-ВИЗИТКИ УЧИТЕЛЕЙ ==========
    # Учителя, загрузившие видео-визитку — последние сверху.
    teachers_with_video_qs = TeacherProfile.objects.exclude(
        video_url__isnull=True
    ).exclude(video_url='').select_related('user').order_by('-updated_at')
    teachers_with_video_count = teachers_with_video_qs.count()
    teachers_with_video = teachers_with_video_qs[:15]
    
    # ========== СТАТИСТИКА ПО СТРАНИЦАМ ==========
    page_stats = ViewCounter.objects.filter(month=current_month).values('page').annotate(
        view_count=Count('id')
    ).order_by('-view_count')[:10]
    
    # ========== ТОП ПРЕДМЕТОВ ==========
    top_subjects = Subject.objects.annotate(
        teacher_count=Count('teacherprofile'),
        student_interest_count=Count('interested_students')
    ).order_by('-teacher_count')[:10]

    # ========== ФИНАНСЫ + «ТРЕБУЕТ ВНИМАНИЯ» ==========
    from decimal import Decimal as _D
    from billing.models import (
        LessonDispute, Subscription as _Sub, Transaction as _Tx, WithdrawalRequest as _Wd,
    )
    from billing.platform_account import get_or_create_platform_user
    _platform = get_or_create_platform_user()
    total_escrow = _Sub.objects.filter(
        status__in=_Sub.ACTIVE_STATUSES,
    ).aggregate(s=Sum('escrow_balance'))['s'] or _D('0')
    platform_balance = _platform.wallet.balance
    commission_month = _Tx.objects.filter(
        wallet=_platform.wallet, type=_Tx.Type.COMMISSION, created_at__gte=month_ago,
    ).aggregate(s=Sum('amount'))['s'] or _D('0')
    payouts_month = _Tx.objects.filter(
        type=_Tx.Type.LESSON_PAYOUT, created_at__gte=month_ago,
    ).aggregate(s=Sum('amount'))['s'] or _D('0')
    active_subscriptions = _Sub.objects.filter(status=_Sub.Status.ACTIVE).count()
    # «Требует внимания»
    pending_withdrawals = _Wd.objects.filter(status='pending').count()
    open_disputes = LessonDispute.objects.filter(status=LessonDispute.Status.OPEN).count()
    pending_requests = _Sub.objects.filter(status=_Sub.Status.PENDING_APPROVAL).count()
    attention_total = pending_teachers + pending_withdrawals + open_disputes + pending_requests

    context = {
        # Метрики пользователей
        'total_teachers': total_teachers,
        'active_teachers': active_teachers,
        'pending_teachers': pending_teachers,
        'approved_teachers': approved_teachers,
        'total_students': total_students,
        'active_students': active_students,
        'new_teachers_week': new_teachers_week,
        'new_students_week': new_students_week,
        
        # Метрики сообщений
        'total_messages': total_messages,
        'messages_today': messages_today,
        'messages_week': messages_week,
        'unread_messages': unread_messages,
        'active_conversations': active_conversations,
        
        # Метрики просмотров
        'monthly_views': monthly_views,
        'total_profile_views': total_profile_views,
        'profile_views_today': profile_views_today,
        'profile_views_week': profile_views_week,
        'profile_views_month': profile_views_month,
        
        # Метрики отзывов и избранного
        'total_reviews': total_reviews,
        'reviews_week': reviews_week,
        'total_favorites': total_favorites,
        
        # Telegram
        'telegram_users': telegram_users,
        'telegram_active': telegram_active,
        
        # Списки
        'pending_teachers_list': pending_teachers_list,
        'recent_messages': recent_messages,
        'recent_teachers': recent_teachers,
        'recent_students': recent_students,
        'teachers_with_video': teachers_with_video,
        'teachers_with_video_count': teachers_with_video_count,
        'page_stats': page_stats,
        'top_subjects': top_subjects,
    
        # Вспомогательные данные
        # Финансы платформы
        'total_escrow': total_escrow,
        'platform_balance': platform_balance,
        'commission_month': commission_month,
        'payouts_month': payouts_month,
        'active_subscriptions': active_subscriptions,
        # Требует внимания
        'pending_withdrawals': pending_withdrawals,
        'open_disputes': open_disputes,
        'pending_requests': pending_requests,
        'attention_total': attention_total,
        'current_month': current_month.strftime('%B %Y'),
        'now': now,
    }

    # Материализуем queryset'ы (списки), чтобы в кэш легли конкретные данные,
    # а не ленивые запросы; затем кэшируем сводку на 45с.
    for _k in ('pending_teachers_list', 'recent_messages', 'recent_teachers',
               'recent_students', 'recent_tx', 'page_stats', 'top_subjects',
               'teachers_with_video'):
        if _k in context:
            context[_k] = list(context[_k])
    _cache.set('admin_dashboard_ctx', context, 45)

    return render(request, 'admin/admin_dashboard.html', context)


@staff_member_required
def messages_management(request):
    """
    Админская страница управления переписками между пользователями и быстрый доступ к рассылкам Telegram
    """
    from django.db.models import Count, Case, When, IntegerField, Q

    # Статистика переписок
    total_conversations = Conversation.objects.count()
    active_conversations = Conversation.objects.filter(is_active=True).count()
    conversations_with_messages = Conversation.objects.filter(messages__isnull=False).distinct().count()

    # Получаем активные переписки с информацией об участниках и аннотациями для избежания N+1
    from django.db.models import Prefetch, Subquery, OuterRef
    latest_msg_ids = Message.objects.filter(
        conversation=OuterRef('pk')
    ).order_by('-created_at').values('id')[:1]

    recent_conversations = Conversation.objects.filter(
        is_active=True,
        messages__isnull=False,
    ).select_related(
        'teacher__user',
        'student',
        'subject'
    ).prefetch_related(
        Prefetch(
            'messages',
            queryset=Message.objects.select_related('sender').order_by('-created_at'),
            to_attr='prefetched_messages'
        )
    ).annotate(
        messages_count=Count('messages', distinct=True),
        unread_count=Count(
            Case(
                When(messages__is_read=False, then=1),
                output_field=IntegerField()
            )
        )
    ).distinct().order_by('-updated_at')[:50]

    conversations_info = []
    for conv in recent_conversations:
        last_message = conv.prefetched_messages[0] if conv.prefetched_messages else None
        conversations_info.append({
            'conversation': conv,
            'last_message': last_message,
            'messages_count': conv.messages_count,
            'unread_count': conv.unread_count,
        })

    # Список Telegram пользователей для персональных отправок
    telegram_users = TelegramUser.objects.select_related('user').order_by('-created_at')[:50]

    context = {
        'total_conversations': total_conversations,
        'active_conversations': active_conversations,
        'conversations_with_messages': conversations_with_messages,
        'conversations_info': conversations_info,
        'telegram_users': telegram_users,
    }

    return render(request, 'admin/messages_management.html', context)

@login_required
def students_list(request):
    """
    Страница со списком учеников, которые ищут учителей.
    Требует авторизации — содержит PII учеников.
    """
    # Базовый queryset - только активные ученики
    students = StudentProfile.objects.filter(is_active=True).select_related(
        'user', 'city'
    ).prefetch_related(
        'desired_subjects'
    )
    
    # Получаем параметры фильтрации
    subject_id = request.GET.get('subject')
    city_id = request.GET.get('city')
    learning_format = request.GET.get('format')
    min_budget = request.GET.get('min_budget')
    max_budget = request.GET.get('max_budget')
    education_level = request.GET.get('education_level')
    search_query = request.GET.get('search')
    
    # Применяем фильтры
    if subject_id:
        students = students.filter(desired_subjects__id=subject_id)
    
    if city_id:
        students = students.filter(city_id=city_id)
    
    if learning_format:
        students = students.filter(learning_format=learning_format)
    
    if education_level:
        students = students.filter(education_level=education_level)
    
    # Фильтр по бюджету
    if min_budget:
        val = _safe_float(min_budget)
        if val is not None:
            students = students.filter(budget_max__gte=val)

    if max_budget:
        val = _safe_float(max_budget)
        if val is not None:
            students = students.filter(budget_min__lte=val)
    
    # Умный поиск по имени, описанию, bio (с синонимами)
    if search_query:
        students = students.filter(build_student_search_q(search_query))
    
    # Убираем дубликаты и сортируем по дате создания (новые сначала)
    students = students.distinct().order_by('-created_at')
    
    # ========== ПАГИНАЦИЯ ==========
    # Создаем объект пагинатора (12 учеников на страницу)
    paginator = Paginator(students, 12)
    page = request.GET.get('page', 1)
    
    try:
        students_page = paginator.page(page)
    except PageNotAnInteger:
        # Если page не является целым числом, показываем первую страницу
        students_page = paginator.page(1)
    except EmptyPage:
        # Если page выходит за пределы диапазона, показываем последнюю страницу
        students_page = paginator.page(paginator.num_pages)
    
    all_subjects = _get_cached_subjects()
    all_cities = _get_cached_cities()

    # Кэширование диапазона бюджета
    budget_range = cache.get('budget_range')
    if budget_range is None:
        budget_range = StudentProfile.objects.filter(
            is_active=True,
            budget_max__isnull=False
        ).aggregate(
            min_budget=Min('budget_min'),
            max_budget=Max('budget_max')
        )
        cache.set('budget_range', budget_range, getattr(settings, 'CACHE_TTL', 900))
    
    context = {
        'students': students_page,  # Изменено: теперь используем объект Page
        'total_students': paginator.count,  # Общее количество учеников
        'subjects': all_subjects,
        'cities': all_cities,
        'learning_formats': StudentProfile.LEARNING_FORMATS,
        'education_levels': StudentProfile.EDUCATION_LEVELS,
        'budget_range': budget_range,
        'selected_subject': subject_id,
        'selected_city': city_id,
        'selected_format': learning_format,
        'selected_education_level': education_level,
        'selected_min_budget': min_budget,
        'selected_max_budget': max_budget,
        'search_query': search_query,
    }
    
    return render(request, 'logic/students_list.html', context)


def detail(request, id):
    """Детальная страница учителя.

    Публичная, но черновики/неодобренные профили скрыты — кроме случая,
    когда заходит сам владелец профиля (видит свой профиль в любом статусе).
    """
    qs = (
        TeacherProfile.objects.select_related('user', 'city')
        .prefetch_related(
            'teachersubject_set__subject',
            'certificates',
            'reviews__student',
            'reviews__subject',
        )
    )
    teacher = get_object_or_404(qs, id=id)
    is_owner = request.user.is_authenticated and request.user.id == teacher.user_id
    if not is_owner:
        if not teacher.is_active or teacher.moderation_status != 'approved':
            raise Http404()

    record_profile_view(request, teacher, 'teacher')

    reviews = teacher.reviews.select_related('student', 'subject').order_by('-created_at')

    # Один проход вместо AVG + 5×COUNT (6 запросов → 1) по отзывам учителя.
    _agg = reviews.aggregate(
        avg_knowledge=Avg('knowledge_rating'),
        avg_communication=Avg('communication_rating'),
        avg_punctuality=Avg('punctuality_rating'),
        r5=Count('id', filter=Q(rating=5)),
        r4=Count('id', filter=Q(rating=4)),
        r3=Count('id', filter=Q(rating=3)),
        r2=Count('id', filter=Q(rating=2)),
        r1=Count('id', filter=Q(rating=1)),
    )
    rating_stats = {
        'avg_knowledge': _agg['avg_knowledge'],
        'avg_communication': _agg['avg_communication'],
        'avg_punctuality': _agg['avg_punctuality'],
    }
    rating_distribution = {
        5: _agg['r5'], 4: _agg['r4'], 3: _agg['r3'], 2: _agg['r2'], 1: _agg['r1'],
    }

    is_favorite = False
    if request.user.is_authenticated:
        is_favorite = teacher.favorited_by.filter(student=request.user).exists()

    similar_teachers = TeacherProfile.objects.filter(
        subjects__in=teacher.subjects.all(),
        is_active=True,
        moderation_status='approved'
    ).exclude(id=teacher.id).select_related('user', 'city').distinct()[:3]

    can_view_contacts = can_view_contact_info(request, teacher.user)
    show_auth_prompt = not request.user.is_authenticated

    # Активные тарифы учителя (подписки, которые ученик может купить).
    active_tariffs = teacher.tariffs.filter(is_active=True).select_related('subject')

    # Phase 10.5: Если ученик прошёл пробный с этим учителем за последние 30 дней
    # и ещё не подписан → подсветить тарифы как «рекомендуется после пробного».
    completed_trial_subject_ids = set()
    has_completed_trial = False
    continue_subject_id = None
    if request.user.is_authenticated and request.user.user_type == 'student':
        from datetime import timedelta
        from django.utils import timezone as tz
        from billing.models import Subscription as SubModel
        cutoff = tz.now() - timedelta(days=30)
        trials_qs = Booking.objects.filter(
            student=request.user,
            slot__teacher=teacher,
            is_trial=True,
            status='completed',
            slot__end_at__gte=cutoff,
            slot__end_at__lt=tz.now(),
        ).values_list('subject_id', flat=True)
        # Исключаем предметы, по которым уже есть активная подписка к этому учителю
        active_sub_subj_ids = set(SubModel.objects.filter(
            student=request.user, teacher=teacher,
            status__in=SubModel.ACTIVE_STATUSES,
        ).values_list('subject_id', flat=True))
        completed_trial_ids_all = set(trials_qs)
        has_completed_trial = bool(completed_trial_ids_all)
        completed_trial_subject_ids = completed_trial_ids_all - active_sub_subj_ids
        # Предмет для кнопки «Продолжить обучение» (ещё не подписан по нему).
        continue_subject_id = next(iter(completed_trial_subject_ids), None)

    # Ближайшее реально свободное окно (а не шаблон недели) — чтобы ученик ещё
    # на профиле видел доступность до перехода на страницу бронирования.
    next_free_slot = (
        teacher.time_slots
        .filter(status='free', start_at__gte=timezone.now())
        .order_by('start_at')
        .values_list('start_at', flat=True)
        .first()
    )

    context = {
        'teacher': teacher,
        'reviews': reviews,
        'rating_stats': rating_stats,
        'rating_distribution': rating_distribution,
        'is_favorite': is_favorite,
        'similar_teachers': similar_teachers,
        'next_free_slot': next_free_slot,
        'can_view_contacts': can_view_contacts,
        'show_auth_prompt': show_auth_prompt,
        'active_tariffs': active_tariffs,
        'completed_trial_subject_ids': completed_trial_subject_ids,
        # ТЗ: после пробного — «Продолжить обучение» вместо «Пробный урок».
        'has_completed_trial': has_completed_trial,
        'continue_subject_id': continue_subject_id,
    }

    return render(request, 'logic/teacher_detail.html', context)


@login_required
def student_detail(request, id):
    """Детальная страница ученика с подсчетом просмотров. Требует авторизации (PII)."""
    student = get_object_or_404(
        StudentProfile.objects.select_related('user', 'city')
        .prefetch_related(
            'desired_subjects',
            'interests'
        ),
        id=id,
        is_active=True
    )

    record_profile_view(request, student, 'student')

    desired_subjects = student.desired_subjects.all()

    other_interests = student.interests.exclude(
        id__in=desired_subjects.values_list('id', flat=True)
    )

    similar_students = StudentProfile.objects.filter(
        desired_subjects__in=desired_subjects,
        is_active=True
    ).exclude(id=student.id).select_related('user', 'city').distinct()[:3]

    suggested_teachers = TeacherProfile.objects.filter(
        subjects__in=desired_subjects,
        is_active=True,
        moderation_status='approved'
    ).select_related('user', 'city').distinct().order_by('-is_featured', '-rating')[:6]

    is_favorited = False
    if request.user.is_authenticated and request.user.user_type == 'teacher':
        try:
            is_favorited = FavoriteStudent.objects.filter(
                teacher=request.user.teacher_profile,
                student=student
            ).exists()
        except TeacherProfile.DoesNotExist:
            pass
        
    can_view_contacts = can_view_contact_info(request, student.user)
    show_auth_prompt = not request.user.is_authenticated

    context = {
        'student': student,
        'desired_subjects': desired_subjects,
        'other_interests': other_interests,
        'similar_students': similar_students,
        'suggested_teachers': suggested_teachers,
        'is_favorited': is_favorited,
        'can_view_contacts': can_view_contacts,
        'show_auth_prompt': show_auth_prompt,
    }

    return render(request, 'logic/student_detail.html', context)


def login_view(request):
    """Вход в систему. Rate-limited: 10 POST/10мин по IP — защита от brute force."""
    from django_ratelimit.core import is_ratelimited
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        # Считаем только POST'ы (увеличиваем счётчик), GET-открытие страницы свободно
        limited = is_ratelimited(
            request=request, group='login', key='ip',
            rate='10/10m', method='POST', increment=True,
        )
        if limited:
            messages.error(request, _('Слишком много попыток входа. Подождите 10 минут и попробуйте снова.'))
            return render(request, 'logic/login.html', {'form': LoginForm(), 'next': request.GET.get('next', '')}, status=429)

        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            remember_me = form.cleaned_data.get('remember_me')

            # Форма принимает email ИЛИ username (любой регистр) и сама
            # резолвит идентификатор в пользователя при валидации
            # (LoginForm.clean_username), поэтому здесь берём уже
            # аутентифицированного пользователя из формы.
            user = form.get_user()

            if user is not None:
                login(request, user, backend='django.contrib.auth.backends.ModelBackend')

                if not remember_me:
                    request.session.set_expiry(0)

                messages.success(request, _('Добро пожаловать, %(name)s!') % {'name': user.get_full_name()})

                next_url = request.GET.get('next')
                if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                    return redirect(next_url)

                # Главная залогиненного пользователя — его дашборд (роутится по
                # user_type). Дашборд сам редиректит на онбординг, если профиля нет.
                return redirect('dashboard')
            else:
                messages.error(request, _('Неверный email/имя пользователя или пароль'))
    else:
        form = LoginForm()
    
    context = {
        'form': form,
        'next': request.GET.get('next', '')
    }
    return render(request, 'logic/login.html', context)


@login_required
def logout_view(request):
    """Выход из системы"""
    if request.method == 'POST':
        logout(request)
        messages.success(request, _('Вы успешно вышли из системы'))
        return redirect('home')
    
    return render(request, 'logic/logout_confirm.html')


def privacy_view(request):
    """Политика конфиденциальности — публичная страница."""
    return render(request, 'legal/privacy.html')


def terms_view(request):
    """Условия использования — публичная страница."""
    return render(request, 'legal/terms.html')


def robots_txt(request):
    """robots.txt — указатель для поисковых роботов на sitemap."""
    from django.http import HttpResponse
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin/\n"
        "Disallow: /accounts/\n"
        "Disallow: /api/\n"
        "Disallow: /ru/api/\n"
        "Disallow: /uz/api/\n"
        "Disallow: /en/api/\n"
        "Disallow: /admin-dashboard/\n"
        "\n"
        "Sitemap: https://ustozhubedu.uz/sitemap.xml\n"
    )
    return HttpResponse(content, content_type='text/plain')


def healthz(request):
    """Health-check для мониторинга/nginx: проверяет БД и Redis-кэш.

    Возвращает 200 если всё живо, иначе 503 (с деталями, какой компонент упал).
    Без аутентификации, без записи в БД — дёшево пинговать часто.
    """
    from django.http import JsonResponse
    from django.db import connection
    from django.core.cache import cache

    checks = {}
    ok = True

    try:
        with connection.cursor() as cur:
            cur.execute('SELECT 1')
            cur.fetchone()
        checks['db'] = 'ok'
    except Exception as e:
        checks['db'] = f'error: {e}'
        ok = False

    try:
        cache.set('healthz', '1', 5)
        checks['cache'] = 'ok' if cache.get('healthz') == '1' else 'error: readback failed'
        if checks['cache'] != 'ok':
            ok = False
    except Exception as e:
        checks['cache'] = f'error: {e}'
        ok = False

    return JsonResponse(
        {'status': 'ok' if ok else 'degraded', 'checks': checks},
        status=200 if ok else 503,
    )


def register_choose(request):
    """Выбор типа регистрации"""
    if request.user.is_authenticated:
        # Разрешаем доступ пользователям без профиля (после Google login)
        has_profile = False
        try:
            _ = request.user.teacher_profile.pk
            has_profile = True
        except Exception:
            pass
        try:
            _ = request.user.student_profile.pk
            has_profile = True
        except Exception:
            pass

        if has_profile:
            return redirect('dashboard')

    return render(request, 'logic/register_choose.html')


def register_student(request):
    """Регистрация ученика. Rate-limited: 5/час с одного IP — анти-спам."""
    if request.user.is_authenticated:
        # Разрешаем доступ Google-пользователям без профиля студента
        has_student = False
        try:
            _ = request.user.student_profile.pk
            has_student = True
        except Exception:
            pass
        if has_student:
            return redirect('dashboard')

    if request.method == 'POST':
        from django_ratelimit.core import is_ratelimited
        limited = is_ratelimited(
            request=request, group='register_student', key='ip',
            rate='5/h', method='POST', increment=True,
        )
        if limited:
            messages.error(request, _('Слишком много регистраций с этого IP. Попробуйте позже.'))
            return render(request, 'logic/register_student.html', {'form': StudentRegistrationForm()}, status=429)

        form = StudentRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')

            messages.success(
                request,
                _('Регистрация прошла успешно! Мы подобрали учителей, которые точно вам подойдут.')
            )

            # 🎯 Phase 8 — Magic moment: сразу показываем smart-matched учителей
            # вместо безликого редиректа на каталог.
            return redirect('student_suggestions')
    else:
        form = StudentRegistrationForm()
    
    context = {
        'form': form
    }
    return render(request, 'logic/register_student.html', context)


@login_required
def profile_view(request):
    """Просмотр профиля с подсчетом статистики просмотров"""
    if request.user.user_type == 'teacher':
        try:
            teacher_profile = request.user.teacher_profile
            # Получаем все связанные данные для полного отображения
            teacher_profile = TeacherProfile.objects.select_related(
                'user', 'city'
            ).prefetch_related(
                'teachersubject_set__subject',
                'certificates',
                'reviews'
            ).get(id=teacher_profile.id)
            
            # ✅ НОВОЕ: Получаем статистику просмотров
            views_stats = {
                'total': teacher_profile.get_views_count('all'),
                'week': teacher_profile.get_views_count('week'),
                'day': teacher_profile.get_views_count('day'),
                'unique_total': teacher_profile.get_unique_viewers_count('all'),
                'unique_week': teacher_profile.get_unique_viewers_count('week'),
            }

            # Phase 9 — Activity dashboard данные за 3 периода
            activity_7d  = teacher_profile.get_activity_stats('7d')
            activity_30d = teacher_profile.get_activity_stats('30d')
            activity_all = teacher_profile.get_activity_stats('all')
            funnel_7d    = teacher_profile.get_funnel_stats('7d')
            earnings_7d  = teacher_profile.get_earnings_stats('7d')
            earnings_30d = teacher_profile.get_earnings_stats('30d')
            earnings_all = teacher_profile.get_earnings_stats('all')

            first_booking_checklist = teacher_profile.get_first_booking_checklist()
            first_booking_done_count = (
                sum(1 for c in first_booking_checklist if c['done'])
                if first_booking_checklist else 0
            )

            # Счётчики лидов (ученики, проявившие интерес) — для бейджа на кнопке.
            # 'new' — новые (непросмотренные) лиды для индикатора-точки; считается
            # тем же проходом, без второй небаунженной загрузки лидов (аудит M7).
            from .leads import count_teacher_leads
            lead_counts = count_teacher_leads(teacher_profile)
            lead_new_count = lead_counts['new']

            return render(request, 'logic/teacher_profile.html', {
                'teacher': teacher_profile,
                'views_stats': views_stats,
                'lead_counts': lead_counts,
                'lead_new_count': lead_new_count,
                'activity_7d': activity_7d,
                'activity_30d': activity_30d,
                'activity_all': activity_all,
                'funnel_7d': funnel_7d,
                'earnings_7d': earnings_7d,
                'earnings_30d': earnings_30d,
                'earnings_all': earnings_all,
                'first_booking_checklist': first_booking_checklist,
                'first_booking_done_count': first_booking_done_count,
            })
        except TeacherProfile.DoesNotExist:
            messages.warning(request, _('Завершите регистрацию учителя'))
            return redirect('teacher_register')
    else:
        try:
            student_profile = request.user.student_profile
            
            # ✅ НОВОЕ: Получаем статистику просмотров
            views_stats = {
                'total': student_profile.get_views_count('all'),
                'week': student_profile.get_views_count('week'),
                'day': student_profile.get_views_count('day'),
                'unique_total': student_profile.get_unique_viewers_count('all'),
                'unique_week': student_profile.get_unique_viewers_count('week'),
            }
            
            return render(request, 'logic/student_profile.html', {
                'student': student_profile,
                'views_stats': views_stats,  # ✅ НОВОЕ
            })
        except StudentProfile.DoesNotExist:
            StudentProfile.objects.create(user=request.user)
            return redirect('profile')


@login_required
def profile_edit(request):
    """
    Редактирование профиля
    Перенаправляет на соответствующую страницу в зависимости от типа пользователя
    """
    if request.user.user_type == 'teacher':
        return redirect('teacher_profile_edit')
    else:
        return redirect('student_profile_edit')


@login_required
def teacher_profile_edit(request):
    """Редактирование профиля учителя"""
    try:
        teacher_profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        messages.error(request, _('Профиль учителя не найден'))
        return redirect('home')

    # Получаем существующие предметы учителя
    teacher_subjects = TeacherSubject.objects.filter(teacher=teacher_profile)
    
    if request.method == 'POST':
        user_form = UserProfileEditForm(request.POST, request.FILES, instance=request.user)
        profile_form = TeacherProfileEditForm(request.POST, instance=teacher_profile)
        
        # Обработка предметов
        subject_forms_valid = True
        subject_forms = []
        
        # Обработка существующих предметов
        for ts in teacher_subjects:
            form = TeacherSubjectEditForm(
                request.POST, 
                instance=ts, 
                prefix=f'subject_{ts.id}'
            )
            subject_forms.append(form)
            if not form.is_valid():
                subject_forms_valid = False
        
        # Обработка нового предмета (если добавляется)
        new_subject_form = TeacherSubjectEditForm(request.POST, prefix='new_subject')
        
        # Проверяем, заполнен ли хотя бы один обязательный параметр нового предмета
        subject_filled = new_subject_form.data.get('new_subject-subject')
        
        if subject_filled:
            # Пользователь пытается добавить новый предмет
            if new_subject_form.is_valid():
                subject_forms.append(new_subject_form)
            else:
                subject_forms_valid = False
                subject_forms.append(new_subject_form)
        
        if user_form.is_valid() and profile_form.is_valid() and subject_forms_valid:
            user_form.save()
            profile_form.save()
            
            # Сохраняем предметы
            for form in subject_forms:
                if form.instance.pk:  # Существующий предмет
                    form.save()
                else:  # Новый предмет
                    teacher_subject = form.save(commit=False)
                    teacher_subject.teacher = teacher_profile
                    teacher_subject.save()
            
            messages.success(request, _('Профиль успешно обновлен!'))
            return redirect('profile')
        else:
            # Выводим конкретные ошибки для отладки
            if not user_form.is_valid():
                for field, errors in user_form.errors.items():
                    messages.error(request, _('Ошибка в поле %(field)s: %(errors)s') % {'field': field, 'errors': ", ".join(errors)})
            if not profile_form.is_valid():
                for field, errors in profile_form.errors.items():
                    messages.error(request, _('Ошибка в поле %(field)s: %(errors)s') % {'field': field, 'errors': ", ".join(errors)})
            if not subject_forms_valid:
                messages.error(request, _('Проверьте правильность заполнения предметов'))
    else:
        user_form = UserProfileEditForm(instance=request.user)
        profile_form = TeacherProfileEditForm(instance=teacher_profile)
        
        # Формы для существующих предметов
        subject_forms = [
            TeacherSubjectEditForm(instance=ts, prefix=f'subject_{ts.id}')
            for ts in teacher_subjects
        ]
        
        # Форма для нового предмета
        new_subject_form = TeacherSubjectEditForm(prefix='new_subject')
        subject_forms.append(new_subject_form)
    
    context = {
        'user_form': user_form,
        'profile_form': profile_form,
        'teacher': teacher_profile,
        'subject_forms': subject_forms,
        'teacher_subjects': teacher_subjects,
        # Передаём completeness для синхронизации с widget'ом на teacher_profile.html
        # и подсветки missing-секций
        'completeness': teacher_profile.get_completeness(),
    }
    return render(request, 'logic/teacher_profile_edit.html', context)


@login_required
def delete_teacher_subject(request, subject_id):
    """Удаление предмета учителя"""
    try:
        teacher_profile = request.user.teacher_profile
        teacher_subject = TeacherSubject.objects.get(id=subject_id, teacher=teacher_profile)
        teacher_subject.delete()
        messages.success(request, _('Предмет успешно удален'))
    except (TeacherProfile.DoesNotExist, TeacherSubject.DoesNotExist):
        messages.error(request, _('Предмет не найден'))
    
    return redirect('teacher_profile_edit')


@login_required
def student_profile_edit(request):
    """Редактирование профиля ученика"""
    try:
        student_profile = request.user.student_profile
    except StudentProfile.DoesNotExist:
        student_profile = StudentProfile.objects.create(user=request.user)
    
    if request.method == 'POST':
        user_form = UserProfileEditForm(request.POST, request.FILES, instance=request.user)
        profile_form = StudentProfileEditForm(request.POST, instance=student_profile)
        
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            
            messages.success(request, _('Профиль успешно обновлен!'))
            return redirect('profile')
        else:
            messages.error(request, _('Пожалуйста, исправьте ошибки в форме'))
    else:
        user_form = UserProfileEditForm(instance=request.user)
        profile_form = StudentProfileEditForm(instance=student_profile)
    
    context = {
        'user_form': user_form,
        'profile_form': profile_form,
        'student': student_profile
    }
    return render(request, 'logic/student_profile_edit.html', context)




@login_required
def toggle_profile_status(request):
    """AJAX-функция для быстрого переключения статуса активности профиля"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': _('Неверный запрос')})

    try:
        if request.user.user_type == 'teacher':
            profile = request.user.teacher_profile
        else:
            profile = request.user.student_profile

        profile.is_active = not profile.is_active
        profile.save()

        status_text = _('активен') if profile.is_active else _('деактивирован')
        messages.success(request, _('Ваш профиль %(status)s в поиске') % {'status': status_text})

        return JsonResponse({
            'success': True,
            'is_active': profile.is_active,
            'message': _('Профиль %(status)s') % {'status': status_text}
        })
    except (TeacherProfile.DoesNotExist, StudentProfile.DoesNotExist):
        return JsonResponse({'success': False, 'error': _('Профиль не найден')})


@login_required
def toggle_favorite_teacher(request, teacher_id):
    """Студент добавляет/удаляет учителя в избранное"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': _('Неверный метод')})

    if request.user.user_type != 'student':
        return JsonResponse({'success': False, 'error': _('Доступ запрещен')})

    teacher = get_object_or_404(TeacherProfile, id=teacher_id, is_active=True)

    fav, created = Favorite.objects.get_or_create(student=request.user, teacher=teacher)
    if not created:
        fav.delete()
        return JsonResponse({'success': True, 'favorited': False})

    # Новый заинтересованный ученик — уведомляем учителя (in-app + дубль на
    # email/Telegram через сигнал push_notification_realtime). Срабатывает только
    # на переходе «нет избранного → есть» (created=True), поэтому повторные
    # тоглы не плодят дубли. Если ученик ранее сказал «не интересно» (opt-out),
    # он скрыт из раздела «Потенциальные ученики» и из индикатора — уведомление
    # о нём было бы фантомным, поэтому пропускаем.
    from .leads import is_opted_out
    if not is_opted_out(teacher, request.user):
        _notify_teacher_new_interest(teacher, request.user)

    return JsonResponse({'success': True, 'favorited': True})


def _notify_teacher_new_interest(teacher, student_user):
    """Уведомление учителю о новом заинтересованном ученике (никогда не роняет
    основной поток — добавление в избранное важнее уведомления)."""
    try:
        student_name = student_user.get_full_name() or student_user.username
        try:
            action_url = reverse('potential_students')
        except Exception:
            action_url = ''
        Notification.objects.create(
            title='Новый заинтересованный ученик',
            # short_text — CharField(300); имя (first+last до ~300) могло бы
            # переполнить колонку, обрезаем как делает _notify в tasks.py.
            short_text=f'{student_name} добавил(а) вас в избранное.'[:300],
            full_text=(
                f'Ученик {student_name} добавил(а) вас в избранное. '
                f'Откройте раздел «Потенциальные ученики», чтобы написать первым.'
            ),
            target='specific_user',
            target_user=teacher.user,
            is_active=True,
            priority=6,
            category=Notification.Category.GENERAL,
            action_url=action_url,
        )
    except Exception:
        logger.warning(
            'new-interest notify failed teacher=%s student=%s',
            getattr(teacher, 'pk', None), getattr(student_user, 'pk', None),
            exc_info=True,
        )


@login_required
def toggle_favorite_student(request, student_id):
    """Учитель добавляет/удаляет ученика в избранное"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': _('Неверный метод')})

    if request.user.user_type != 'teacher' or not hasattr(request.user, 'teacher_profile'):
        return JsonResponse({'success': False, 'error': _('Доступ запрещен')})

    try:
        teacher_profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        return JsonResponse({'success': False, 'error': _('Профиль учителя не найден')})
    
    student = get_object_or_404(StudentProfile, id=student_id, is_active=True)

    fav, created = FavoriteStudent.objects.get_or_create(teacher=teacher_profile, student=student)
    if not created:
        fav.delete()
        return JsonResponse({'success': True, 'favorited': False})
    return JsonResponse({'success': True, 'favorited': True})


@login_required
def lead_opt_out(request, teacher_id):
    """Ученик говорит учителю «не интересно».

    Создаёт LeadOptOut: учитель теряет право писать первым и пропадает из
    раздела «Потенциальные ученики». Повторный вызов снимает отказ (toggle).
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': _('Неверный метод')})

    if request.user.user_type != 'student':
        return JsonResponse({'success': False, 'error': _('Доступ запрещен')})

    teacher = get_object_or_404(TeacherProfile, id=teacher_id)

    opt, created = LeadOptOut.objects.get_or_create(
        student=request.user, teacher=teacher
    )
    if not created:
        opt.delete()
        return JsonResponse({'success': True, 'opted_out': False})
    return JsonResponse({'success': True, 'opted_out': True})


@login_required
def my_favorite_teachers(request):
    """Список избранных учителей для ученика"""
    if request.user.user_type != 'student':
        messages.error(request, _('Доступ запрещен'))
        return redirect('home')

    favorites = Favorite.objects.select_related('teacher__user', 'teacher__city').filter(student=request.user)
    teachers = [f.teacher for f in favorites]
    return render(request, 'logic/favorites_teachers.html', {'teachers': teachers})


@login_required
def my_favorite_students(request):
    """Список избранных учеников для учителя"""
    if request.user.user_type != 'teacher' or not hasattr(request.user, 'teacher_profile'):
        messages.error(request, _('Доступ запрещен'))
        return redirect('home')

    favorites = FavoriteStudent.objects.select_related('student__user', 'student__city').filter(teacher=request.user.teacher_profile)
    students = [f.student for f in favorites]
    return render(request, 'logic/favorites_students.html', {'students': students})


@login_required
def potential_students(request):
    """Раздел «Потенциальные ученики» (лиды) для учителя.

    Показывает горячих (🔥 пробный урок) и тёплых (⭐ избранное) лидов.
    Фильтр ?status=hot|warm. Для каждого лида определяется состояние диалога:
    можно ли написать первым / ждём ответа / переписка уже идёт.
    """
    from .leads import (
        get_teacher_leads, count_teacher_leads,
        LEAD_HOT, LEAD_WARM, LEAD_STATUS_LABELS,
        teacher_can_send_in_conversation,
    )

    if request.user.user_type != 'teacher' or not hasattr(request.user, 'teacher_profile'):
        messages.error(request, _('Доступ запрещен'))
        return redirect('home')

    teacher_profile = request.user.teacher_profile

    all_leads = get_teacher_leads(teacher_profile)
    counts = count_teacher_leads(teacher_profile, leads=all_leads)

    # Открытие раздела = «просмотрено»: двигаем watermark, чтобы индикатор новых
    # лидов на кнопке погас. Пишем только при наличии новых (избегаем лишней
    # записи на каждый показ страницы) и сбрасываем кэш счётчиков ('new' живёт
    # в той же записи, что и total).
    if counts['new'] > 0:
        from django.utils import timezone
        from django.core.cache import cache
        teacher_profile.leads_seen_at = timezone.now()
        teacher_profile.save(update_fields=['leads_seen_at'])
        cache.delete(f'teacher_lead_counts_{teacher_profile.pk}')

    status_filter = request.GET.get('status')
    if status_filter in (LEAD_HOT, LEAD_WARM):
        visible = [l for l in all_leads if l['status'] == status_filter]
    else:
        status_filter = None
        visible = all_leads

    # Состояние переписки по каждому лиду одним запросом (без N+1).
    student_ids = [l['student_user'].id for l in visible]
    conv_by_student = {
        c.student_id: c
        for c in Conversation.objects.filter(
            teacher=teacher_profile, student_id__in=student_ids
        )
    }

    items = []
    for l in visible:
        student_user = l['student_user']
        conv = conv_by_student.get(student_user.id)
        if conv is None:
            chat_state = 'can_write'        # можно отправить первое сообщение
        else:
            allowed, _r = teacher_can_send_in_conversation(conv)
            chat_state = 'open' if allowed else 'awaiting_reply'
        items.append({
            **l,
            'status_label': LEAD_STATUS_LABELS.get(l['status'], ''),
            'conversation': conv,
            'chat_state': chat_state,
        })

    return render(request, 'logic/potential_students.html', {
        'items': items,
        'counts': counts,
        'status_filter': status_filter,
        'LEAD_HOT': LEAD_HOT,
        'LEAD_WARM': LEAD_WARM,
    })


@login_required
def student_suggestions(request):
    """Smart-matching: подобранные учителя для студента (Phase 8 magic moment)."""
    if request.user.user_type != 'student':
        messages.error(request, _('Доступ запрещен'))
        return redirect('home')

    try:
        student = request.user.student_profile
    except StudentProfile.DoesNotExist:
        messages.warning(request, _('Заполните профиль ученика'))
        return redirect('student_profile_edit')

    desired_subjects = list(student.desired_subjects.all())
    matches = TeacherProfile.get_smart_matches(student, limit=5)

    # Считаем сколько критериев заполнено в профиле — для подсказки
    profile_fields_filled = sum(1 for v in [
        desired_subjects,
        student.budget_max or student.budget_min,
        student.city_id,
        student.learning_format and student.learning_format != 'both',
    ] if v)

    return render(request, 'logic/student_suggestions.html', {
        'student': student,
        'matches': matches,
        'desired_subjects': desired_subjects,
        'profile_fields_filled': profile_fields_filled,
        'has_desired': bool(desired_subjects),
    })


# =============================================================================
# СИСТЕМА СООБЩЕНИЙ МЕЖДУ УЧИТЕЛЯМИ И УЧЕНИКАМИ
# =============================================================================

@login_required
def conversations_list(request):
    """
    Список всех переписок пользователя
    Для учителя: переписки с учениками
    Для ученика: переписки с учителями
    """
    from django.db.models import Prefetch

    user = request.user

    from django.db.models import OuterRef, Subquery

    # Общая аннотация непрочитанных сообщений
    unread_annotation = Count(
        Case(
            When(Q(messages__is_read=False) & ~Q(messages__sender=user), then=1),
            output_field=IntegerField()
        )
    )
    # Последнее сообщение — Subquery top-1 + один in_bulk ниже. Раньше
    # prefetch тянул в память ПОЛНУЮ историю каждой переписки ради первого
    # элемента: 30 чатов × 2000 сообщений = 60k строк на рендер страницы
    # (аудит 2026-06-10 H14). Индекс (conversation, -created_at) уже есть.
    last_msg_id_sq = Subquery(
        Message.objects.filter(conversation=OuterRef('pk'))
        .order_by('-created_at').values('id')[:1]
    )

    if user.user_type == 'teacher':
        try:
            teacher_profile = user.teacher_profile
        except TeacherProfile.DoesNotExist:
            messages.warning(request, _('Завершите регистрацию учителя'))
            return redirect('teacher_register')

        conversations = Conversation.objects.filter(
            teacher=teacher_profile,
            is_active=True,
            messages__isnull=False,
        ).select_related(
            'student', 'subject'
        ).annotate(
            unread_count=unread_annotation,
            last_msg_id=last_msg_id_sq,
        ).distinct().order_by('-updated_at')
    else:
        try:
            user.student_profile
        except StudentProfile.DoesNotExist:
            messages.warning(request, _('Заполните профиль ученика'))
            return redirect('student_profile_edit')

        conversations = Conversation.objects.filter(
            student=user,
            is_active=True,
            messages__isnull=False,
        ).select_related(
            'teacher__user', 'teacher__city', 'subject'
        ).annotate(
            unread_count=unread_annotation,
            last_msg_id=last_msg_id_sq,
        ).distinct().order_by('-updated_at')

    conversations = list(conversations)
    last_by_id = Message.objects.select_related('sender').in_bulk(
        [c.last_msg_id for c in conversations if c.last_msg_id]
    )
    conversations_with_info = []
    for conv in conversations:
        conversations_with_info.append({
            'conversation': conv,
            'last_message': last_by_id.get(conv.last_msg_id),
            'unread_count': conv.unread_count
        })

    return render(request, 'logic/conversations_list.html', {
        'conversations': conversations_with_info,
        'user_type': user.user_type
    })


@login_required
def conversation_detail(request, conversation_id):
    """
    Детальная страница переписки с сообщениями
    """
    user = request.user
    
    try:
        # Получаем переписку с проверкой доступа
        if user.user_type == 'teacher':
            conversation = get_object_or_404(
                Conversation.objects.select_related(
                    'teacher__user',
                    'student',
                    'subject'
                ),
                id=conversation_id,
                teacher=user.teacher_profile,
                is_active=True
            )
            other_user = conversation.student
        else:
            conversation = get_object_or_404(
                Conversation.objects.select_related(
                    'teacher__user',
                    'student',
                    'subject'
                ),
                id=conversation_id,
                student=user,
                is_active=True
            )
            other_user = conversation.teacher.user
        
        # Получаем сообщения переписки
        messages_list = conversation.messages.select_related('sender').order_by('created_at')
        
        # Отмечаем сообщения как прочитанные (которые не от текущего пользователя)
        marked = conversation.messages.filter(
            is_read=False
        ).exclude(
            sender=user
        ).update(is_read=True, read_at=timezone.now())

        # Сбрасываем кэш badge если что-то было отмечено
        if marked > 0:
            from .context_processors import invalidate_message_cache
            invalidate_message_cache(user.pk)

        # Форма для отправки нового сообщения
        if request.method == 'POST':
            form = MessageForm(request.POST)
            if form.is_valid():
                message = form.save(commit=False)
                message.conversation = conversation
                message.sender = user
                # Анти-обход: маскируем контакты до порога доверия (v2 Шаг 7).
                from .contact_filter import apply_contact_policy
                message.content, _masked = apply_contact_policy(conversation, message.content)
                message.save()

                # Обновляем время последнего обновления переписки
                conversation.save()  # updated_at обновится автоматически
                
                messages.success(request, _('Сообщение отправлено!'))
                return redirect('conversation_detail', conversation_id=conversation_id)
            else:
                messages.error(request, _('Ошибка при отправке сообщения'))
        else:
            form = MessageForm()
        
        return render(request, 'logic/conversation_detail.html', {
            'conversation': conversation,
            'messages': messages_list,
            'other_user': other_user,
            'form': form,
            'user_type': user.user_type
        })
        
    except Exception as e:
        logger.error(f"Ошибка в conversation_detail: {e}", exc_info=True)
        messages.error(request, _('Переписка не найдена или доступ запрещен'))
        return redirect('conversations_list')


@login_required(login_url='login')
def start_conversation(request, user_id):
    """
    Начать новую переписку с пользователем
    Ученик может начать переписку с учителем
    Учитель может начать переписку с учеником
    """
    current_user = request.user
    target_user = get_object_or_404(User, id=user_id)
    
    # Проверяем, что пользователи не пытаются писать сами себе
    if current_user == target_user:
        messages.error(request, _('Вы не можете писать себе'))
        return redirect('home')
    
    # Определяем, кто учитель, а кто ученик
    if current_user.user_type == 'teacher':
        try:
            teacher_profile = current_user.teacher_profile
        except TeacherProfile.DoesNotExist:
            # «Сирота»-учитель без профиля (брошенная регистрация) — не 500.
            messages.error(request, _('Завершите регистрацию учителя, чтобы писать сообщения'))
            return redirect('home')
        student = target_user

        # Проверяем, что целевой пользователь - ученик
        if target_user.user_type != 'student':
            messages.error(request, _('Вы можете писать только ученикам'))
            return redirect('home')

        # ГЕЙТИНГ: учитель пишет первым только лидам (избранное / пробный урок).
        # Если ученик уже отвечал в существующем чате — это обычная переписка,
        # ограничение снимается.
        from .leads import teacher_can_open_conversation
        existing = Conversation.objects.filter(
            teacher=teacher_profile, student=student
        ).first()
        if not teacher_can_open_conversation(teacher_profile, student, existing):
            messages.error(
                request,
                _('Написать можно только ученикам, которые добавили вас в избранное '
                  'или забронировали пробный урок.')
            )
            return redirect('student_detail', id=student.id)
    else:
        # Текущий пользователь - ученик
        if target_user.user_type != 'teacher':
            messages.error(request, _('Вы можете писать только учителям'))
            return redirect('home')
        
        try:
            teacher_profile = target_user.teacher_profile
        except TeacherProfile.DoesNotExist:
            messages.error(request, _('Профиль учителя не найден'))
            return redirect('home')

        student = current_user
    
    # Проверяем, существует ли уже переписка
    conversation, created = Conversation.objects.get_or_create(
        teacher=teacher_profile,
        student=student,
        defaults={'is_active': True}
    )
    
    if not created and not conversation.is_active:
        # Если переписка была деактивирована, активируем её
        conversation.is_active = True
        conversation.save()
    
    # Редирект на страницу переписки
    return redirect('conversation_detail', conversation_id=conversation.id)


@login_required
def send_message_ajax(request, conversation_id):
    """
    AJAX endpoint для отправки сообщения
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': _('Только POST запросы')})
    
    user = request.user
    
    try:
        conversation = _get_user_conversation(user, conversation_id)

        # Rate limit — ЕДИНЫЙ с WebSocket-путём (общий счётчик по таблице
        # Message). Без него спамер обходил WS-лимит, отправляя через AJAX.
        from .consumers import message_rate_limited
        if message_rate_limited(user):
            return JsonResponse({
                'success': False,
                'error': _('Слишком много сообщений. Подождите немного.'),
            }, status=429)

        # АНТИСПАМ: учитель вправе отправить только одно первое сообщение,
        # пока ученик не ответил.
        if user == conversation.teacher.user:
            from .leads import teacher_can_send_in_conversation
            allowed, _reason = teacher_can_send_in_conversation(conversation)
            if not allowed:
                return JsonResponse({
                    'success': False,
                    'error': _('Вы уже отправили первое сообщение. '
                               'Дождитесь ответа ученика, чтобы продолжить переписку.')
                }, status=403)

        # Проверяем форму
        form = MessageForm(request.POST)
        if form.is_valid():
            message = form.save(commit=False)
            message.conversation = conversation
            message.sender = user
            # Анти-обход: маскируем контакты до порога доверия (v2 Шаг 7).
            from .contact_filter import apply_contact_policy
            message.content, _masked = apply_contact_policy(conversation, message.content)
            message.save()

            # Обновляем время последнего обновления переписки
            conversation.save()

            # Ретрансляция в real-time: отправляем сообщение в ту же группу,
            # что и ChatConsumer, чтобы второй участник получил его без перезагрузки,
            # если AJAX-путь сработал вместо WebSocket.
            try:
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                channel_layer = get_channel_layer()
                if channel_layer is not None:
                    message_data = {
                        'id': str(message.id),
                        'message': message.content,
                        'sender_id': message.sender.id,
                        'sender_name': user.get_full_name() or user.username,
                        'created_at': message.created_at.isoformat(),
                        'is_read': message.is_read,
                    }
                    async_to_sync(channel_layer.group_send)(
                        f'chat_{conversation.id}',
                        {'type': 'chat_message', 'message_data': message_data},
                    )
            except Exception as ws_err:
                logger.warning(f"WS-ретрансляция в send_message_ajax не удалась: {ws_err}")

            return JsonResponse({
                'success': True,
                'message': {
                    'id': message.id,
                    'content': message.content,
                    'sender': user.get_full_name() or user.username,
                    'sender_id': user.id,
                    'created_at': message.created_at.strftime('%d.%m.%Y %H:%M')
                }
            })
        else:
            return JsonResponse({
                'success': False,
                'error': form.errors
            })
            
    except Exception as e:
        logger.error(f"Ошибка в send_message_ajax: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': _('Произошла ошибка при отправке сообщения')
        })


@login_required
def mark_messages_read(request, conversation_id):
    """
    Отметить сообщения как прочитанные (AJAX)
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': _('Только POST запросы')})
    
    user = request.user
    
    try:
        conversation = _get_user_conversation(user, conversation_id, require_active=False)

        # Отмечаем все непрочитанные сообщения (не от текущего пользователя) как прочитанные
        updated = conversation.messages.filter(
            is_read=False
        ).exclude(
            sender=user
        ).update(is_read=True, read_at=timezone.now())

        # Сбрасываем кэш badge
        if updated > 0:
            from .context_processors import invalidate_message_cache
            invalidate_message_cache(user.pk)

        return JsonResponse({
            'success': True,
            'updated_count': updated
        })
        
    except Exception as e:
        logger.error(f"Ошибка в mark_messages_read: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': _('Произошла ошибка')
        })


@login_required
def delete_conversation(request, conversation_id):
    """
    Удалить (деактивировать) переписку
    """
    if request.method != 'POST':
        messages.error(request, _('Неверный метод запроса'))
        return redirect('conversations_list')
    
    user = request.user
    
    try:
        conversation = _get_user_conversation(user, conversation_id, require_active=False)

        # Деактивируем переписку вместо удаления
        conversation.is_active = False
        conversation.save()
        
        messages.success(request, _('Переписка удалена'))
        return redirect('conversations_list')
        
    except Exception as e:
        logger.error(f"Ошибка в delete_conversation: {e}", exc_info=True)
        messages.error(request, _('Ошибка при удалении переписки'))
        return redirect('conversations_list')


# =============================================================================
# API ДЛЯ ПОИСКА И ВЫБОРА ПРЕДМЕТОВ
# =============================================================================

def _maybe_log_subject_search(request, query: str, results_count: int) -> None:
    """
    Пишет SubjectSearchLog с дедупликацией и сэмплированием.

    - Дедуп: один и тот же запрос от одного пользователя/IP логируется
      не чаще раза в SEARCH_LOG_DEDUP_TTL секунд (защита от автокомплита,
      который дёргает endpoint на каждое нажатие клавиши).
    - Сэмплинг: оставляем только SEARCH_LOG_SAMPLE_RATE % запросов
      (для общего снижения пишущей нагрузки на основную БД).

    Стоит за boolean-флагом — выключается без правки кода.
    """
    if not getattr(settings, 'SEARCH_LOG_ENABLED', True):
        return

    try:
        normalized = normalize_query(query)
        if not normalized:
            return

        actor = (
            f'u:{request.user.pk}' if request.user.is_authenticated
            else f'ip:{get_client_ip(request)}'
        )
        dedup_key = f'search_log_dedup:{actor}:{normalized}'
        dedup_ttl = getattr(settings, 'SEARCH_LOG_DEDUP_TTL', 300)  # 5 минут
        if cache.get(dedup_key):
            return
        cache.set(dedup_key, 1, dedup_ttl)

        sample_rate = getattr(settings, 'SEARCH_LOG_SAMPLE_RATE', 1.0)
        if sample_rate < 1.0 and random.random() > sample_rate:
            return

        SubjectSearchLog.objects.create(
            query=normalized[:200],
            user=request.user if request.user.is_authenticated else None,
            ip_address=get_client_ip(request),
            found_results_count=results_count,
        )
    except Exception as e:
        logger.warning(f"Failed to log subject search: {e}")


def subjects_autocomplete(request):
    """
    API для автокомплита предметов с умным поиском
    Возвращает JSON с предметами, отсортированными по релевантности
    """
    query = request.GET.get('q', '').strip()

    if not query or len(query) < 2:
        return JsonResponse({'results': []})

    # Ограничиваем длину запроса для безопасности
    query = query[:100]

    try:
        subjects = Subject.objects.filter(is_active=True).filter(
            build_subject_search_q(query)
        ).select_related('category').annotate(
            relevance=build_subject_relevance_annotation(query)
        ).order_by('-relevance', '-is_popular', 'name')[:30]

        results = []
        for subject in subjects:
            teachers_count = subject.get_teachers_count()
            results.append({
                'id': subject.id,
                'name': subject.get_display_name(),
                'description': subject.description[:100] if subject.description else '',
                'category': subject.category.name if subject.category else 'Без категории',
                'category_color': subject.category.color if subject.category else '#999999',
                'icon': subject.icon or 'fas fa-book',
                'is_popular': subject.is_popular,
                'teachers_count': teachers_count
            })

        _maybe_log_subject_search(request, query, len(results))

        return JsonResponse({'results': results})

    except Exception as e:
        logger.error(f"Error in subjects_autocomplete: {e}", exc_info=True)
        return JsonResponse({'error': _('Ошибка поиска предметов')}, status=500)


def subjects_popular(request):
    """
    API для получения популярных предметов
    """
    popular_subjects = Subject.objects.filter(
        is_active=True,
        is_popular=True
    ).select_related('category').order_by('name')[:20]

    results = []
    for subject in popular_subjects:
        teachers_count = subject.get_teachers_count()
        results.append({
            'id': subject.id,
            'name': subject.name,
            'category': subject.category.name if subject.category else 'Без категории',
            'category_color': subject.category.color if subject.category else '#999999',
            'icon': subject.icon or 'fas fa-book',
            'teachers_count': teachers_count
        })

    return JsonResponse({'results': results})


def subjects_categories(request):
    """
    API для получения всех категорий с количеством предметов
    """
    categories = SubjectCategory.objects.filter(
        is_active=True
    ).annotate(
        subjects_count=Count('subjects', filter=Q(subjects__is_active=True))
    ).filter(subjects_count__gt=0).order_by('order', 'name')

    results = []
    for category in categories:
        results.append({
            'id': category.id,
            'name': category.name,
            'icon': category.icon or 'fas fa-folder',
            'color': category.color,
            'subjects_count': category.subjects_count
        })

    return JsonResponse({'results': results})


def subjects_by_category(request, category_id):
    """
    API для получения предметов определенной категории
    """
    try:
        category = SubjectCategory.objects.get(id=category_id, is_active=True)
    except SubjectCategory.DoesNotExist:
        return JsonResponse({'error': _('Категория не найдена')}, status=404)

    subjects = Subject.objects.filter(
        category=category,
        is_active=True
    ).order_by('-is_popular', 'name')

    results = []
    for subject in subjects:
        teachers_count = subject.get_teachers_count()
        results.append({
            'id': subject.id,
            'name': subject.name,
            'description': subject.description[:100] if subject.description else '',
            'icon': subject.icon or 'fas fa-book',
            'is_popular': subject.is_popular,
            'teachers_count': teachers_count
        })

    return JsonResponse({
        'category': {
            'id': category.id,
            'name': category.name,
            'color': category.color
        },
        'results': results
    })


# ============================================
# TELEGRAM MANAGEMENT VIEWS
# ============================================

@staff_member_required
def telegram_management(request):
    """
    Страница управления Telegram пользователями для админа
    """
    # Получаем всех Telegram пользователей с профилями
    telegram_users = TelegramUser.objects.select_related(
        'user', 
        'user__teacher_profile', 
        'user__student_profile'
    ).order_by('-created_at')
    
    # Статистика
    total_users = telegram_users.count()
    active_users = telegram_users.filter(started_bot=True).count()
    notifications_enabled = telegram_users.filter(notifications_enabled=True, started_bot=True).count()
    linked_users = telegram_users.filter(user__isnull=False).count()
    
    # Новые пользователи за неделю
    week_ago = timezone.now() - timedelta(days=7)
    new_users_week = telegram_users.filter(created_at__gte=week_ago).count()
    
    # Связанные учителя и ученики
    linked_teachers = telegram_users.filter(user__user_type='teacher').count()
    linked_students = telegram_users.filter(user__user_type='student').count()
    
    # Расчет процентов
    activation_rate = round((active_users / total_users * 100) if total_users > 0 else 0, 1)
    notification_rate = round((notifications_enabled / active_users * 100) if active_users > 0 else 0, 1)
    link_rate = round((linked_users / active_users * 100) if active_users > 0 else 0, 1)
    
    stats = {
        'total_users': total_users,
        'active_users': active_users,
        'notifications_enabled': notifications_enabled,
        'linked_users': linked_users,
        'new_users_week': new_users_week,
        'linked_teachers': linked_teachers,
        'linked_students': linked_students,
        'activation_rate': activation_rate,
        'notification_rate': notification_rate,
        'link_rate': link_rate,
    }
    
    context = {
        'stats': stats,
        'telegram_users': telegram_users[:50],  # Первые 50 для отображения
    }
    
    return render(request, 'admin/telegram_management.html', context)


@staff_member_required
@require_POST
def send_broadcast_message(request):
    """
    Отправка массового сообщения через Telegram бота
    """
    try:
        message_text = request.POST.get('message', '').strip()
        recipients = request.POST.get('recipients', 'all')
        
        if not message_text:
            messages.error(request, _('Сообщение не может быть пустым'))
            return redirect('telegram_management')

        # Ограничиваем длину сообщения
        if len(message_text) > 4000:
            messages.error(request, _('Сообщение слишком длинное (максимум 4000 символов)'))
            return redirect('telegram_management')
        
        # Определяем получателей
        if recipients == 'all':
            users = TelegramUser.objects.filter(notifications_enabled=True, started_bot=True)
        elif recipients == 'linked':
            users = TelegramUser.objects.filter(user__isnull=False, notifications_enabled=True, started_bot=True)
        elif recipients == 'teachers':
            users = TelegramUser.objects.filter(user__user_type='teacher', notifications_enabled=True, started_bot=True)
        elif recipients == 'students':
            users = TelegramUser.objects.filter(user__user_type='student', notifications_enabled=True, started_bot=True)
        else:
            users = TelegramUser.objects.filter(notifications_enabled=True, started_bot=True)
        
        users_count = users.count()
        
        if users_count == 0:
            messages.warning(request, _('Нет пользователей для отправки сообщения'))
            return redirect('telegram_management')
        
        # Отправляем сообщения (используем AdminTelegramService)
        try:
            from .admin_telegram_service import admin_telegram_service

            formatted_message = f"📢 *Сообщение от администрации UstozHub*\n\n{message_text}"

            # Отправляем через admin сервис
            stats = admin_telegram_service.send_to_selected_users(
                telegram_users=list(users),
                message=formatted_message,
                parse_mode='Markdown'
            )
            success_count = stats['success']
            error_count = stats['failed']
        except Exception as e:
            success_count, error_count = 0, users_count
            messages.error(request, _('Ошибка сервиса отправки: %(err)s') % {'err': str(e)})

        if success_count > 0:
            messages.success(request, _('Сообщение успешно отправлено %(count)s пользователям') % {'count': success_count})

        if error_count > 0:
            messages.warning(request, _('Не удалось отправить %(count)s пользователям (возможно, заблокировали бота или удалили чат)') % {'count': error_count})

    except Exception as e:
        messages.error(request, _('Ошибка при отправке сообщений: %(err)s') % {'err': str(e)})
    
    return redirect('telegram_management')


@staff_member_required  
@require_POST
def send_individual_message(request):
    """
    Отправка персонального сообщения пользователю
    """
    try:
        user_id = request.POST.get('user_id')
        message_text = request.POST.get('message', '').strip()
        
        if not user_id or not message_text:
            messages.error(request, _('Необходимо выбрать пользователя и ввести сообщение'))
            return redirect('telegram_management')
        
        telegram_user = get_object_or_404(TelegramUser, id=user_id)
        
        if not telegram_user.started_bot:
            messages.error(request, _('Пользователь не активировал бота'))
            return redirect('telegram_management')
        
        # Отправляем сообщение
        from .admin_telegram_service import admin_telegram_service
        
        formatted_message = f"💬 *Персональное сообщение от администрации*\n\n{message_text}"
        
        success = admin_telegram_service.send_message_sync(
            telegram_id=telegram_user.telegram_id,
            text=formatted_message,
            parse_mode='Markdown'
        )
        
        if success:
            messages.success(request, _('Сообщение отправлено пользователю %(name)s') % {'name': telegram_user.first_name})
        else:
            messages.error(request, _('Не удалось отправить сообщение пользователю %(name)s (возможно, заблокировал бота или удалил чат)') % {'name': telegram_user.first_name})

    except Exception as e:
        messages.error(request, _('Ошибка: %(err)s') % {'err': str(e)})
    
    return redirect('telegram_management')


@staff_member_required
@require_POST
def admin_toggle_telegram_notifications(request, user_id):
    """Переключение уведомлений Telegram-пользователя из админки"""
    tg_user = get_object_or_404(TelegramUser, id=user_id)
    try:
        tg_user.notifications_enabled = not tg_user.notifications_enabled
        tg_user.save(update_fields=['notifications_enabled'])
        return JsonResponse({
            'success': True,
            'notifications_enabled': tg_user.notifications_enabled,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})


@staff_member_required
def admin_conversation_detail(request, conversation_id):
    """Admin view: read full conversation + send messages."""
    conversation = get_object_or_404(
        Conversation.objects.select_related('teacher__user', 'student', 'subject'),
        id=conversation_id
    )
    messages_qs = conversation.messages.select_related('sender').order_by('created_at')

    if request.method == 'POST':
        content = request.POST.get('content', '').strip()
        send_as = request.POST.get('send_as', '')  # user id to send as
        if content:
            from django.contrib import messages as django_messages
            # Имперсонация разрешена ТОЛЬКО за участников этой беседы — иначе
            # staff мог бы слать сообщения от имени любого аккаунта (send_as=<любой id>).
            allowed_senders = {
                str(conversation.teacher.user_id): conversation.teacher.user,
                str(conversation.student_id): conversation.student,
            }
            if send_as:
                sender = allowed_senders.get(str(send_as))
                if sender is None:
                    django_messages.error(
                        request,
                        _('Можно писать только от имени участников беседы.'),
                    )
                    return redirect('admin_conversation_detail', conversation_id=conversation.id)
            else:
                sender = request.user
            Message.objects.create(
                conversation=conversation,
                sender=sender,
                content=content,
            )
            conversation.save(update_fields=['updated_at'])
            logger.warning(
                'admin_conversation_detail: staff=%s sent message as user=%s in conversation=%s',
                request.user.pk, sender.pk, conversation.id,
            )
            django_messages.success(request, _('Сообщение отправлено от имени %(name)s') % {'name': sender.get_full_name() or sender.username})
            return redirect('admin_conversation_detail', conversation_id=conversation.id)

    context = {
        'conversation': conversation,
        'messages_list': messages_qs,
        'teacher_user': conversation.teacher.user,
        'student_user': conversation.student,
    }
    return render(request, 'admin/admin_conversation_detail.html', context)


@staff_member_required
def export_telegram_users(request):
    """Экспорт списка Telegram пользователей в CSV"""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="telegram_users_{datetime.now().strftime("%Y%m%d_%H%M")}.csv"'
    
    # Добавляем BOM для корректного отображения в Excel
    response.write('\ufeff')
    
    writer = csv.writer(response)
    writer.writerow([
        'ID',
        'Telegram ID', 
        'Имя',
        'Фамилия',
        'Username',
        'Привязанный аккаунт',
        'Тип пользователя',
        'Активен',
        'Уведомления',
        'Дата регистрации',
        'Последняя активность'
    ])
    
    for user in TelegramUser.objects.select_related('user').all():
        writer.writerow([
            user.id,
            user.telegram_id,
            user.first_name or '',
            user.last_name or '',
            user.telegram_username or '',
            user.user.username if user.user else 'Не привязан',
            user.user.user_type if user.user else '',
            'Да' if user.started_bot else 'Нет',
            'Да' if user.notifications_enabled else 'Нет',
            user.created_at.strftime('%d.%m.%Y %H:%M'),
            user.last_interaction.strftime('%d.%m.%Y %H:%M')
        ])
    
    return response




# ============================================
# СИСТЕМА УВЕДОМЛЕНИЙ
# ============================================


@login_required
def notifications_list(request):
    """Список уведомлений для текущего пользователя"""
    from django.db.models import Exists, OuterRef

    # Annotate is_read в один запрос вместо N+1
    read_subquery = NotificationRead.objects.filter(
        user=request.user,
        notification_id=OuterRef('id')
    )
    all_notifications = Notification.get_user_notifications(
        request.user,
        include_read=True
    ).annotate(
        is_read=Exists(read_subquery)
    )

    paginator = Paginator(all_notifications, 15)
    page = request.GET.get('page', 1)

    try:
        notifications_page = paginator.page(page)
    except PageNotAnInteger:
        notifications_page = paginator.page(1)
    except EmptyPage:
        notifications_page = paginator.page(paginator.num_pages)

    unread_count = Notification.get_unread_count(request.user)

    context = {
        'notifications': notifications_page,
        'unread_count': unread_count,
    }

    return render(request, 'notifications/list.html', context)


@login_required
def notification_detail(request, notification_id):
    """
    Детальный просмотр уведомления
    Помечает уведомление как прочитанное при просмотре
    """
    notification = get_object_or_404(Notification, id=notification_id)
    
    # Проверяем, имеет ли пользователь доступ к уведомлению
    if not notification.is_visible_for_user(request.user):
        messages.error(request, _('У вас нет доступа к этому уведомлению.'))
        return redirect('notifications_list')
    
    notification.mark_as_read(request.user)

    # Если уведомление связано с бронированием — даём учителю-владельцу
    # возможность подтвердить/отклонить прямо здесь.
    booking = notification.booking
    booking_panel = None
    if booking:
        tp = getattr(request.user, 'teacher_profile', None)
        is_owner_teacher = bool(tp and booking.slot.teacher_id == tp.pk)
        booking_panel = {
            'booking': booking,
            'is_owner_teacher': is_owner_teacher,
            'can_act': is_owner_teacher and booking.status == 'pending',
        }

    context = {
        'notification': notification,
        'is_read': True,
        'booking_panel': booking_panel,
    }

    return render(request, 'notifications/detail.html', context)


@login_required
@require_POST
def mark_notification_read(request, notification_id):
    """
    AJAX endpoint для пометки уведомления как прочитанного
    """
    try:
        notification = get_object_or_404(Notification, id=notification_id)
        
        # Проверяем доступ
        if not notification.is_visible_for_user(request.user):
            return JsonResponse({
                'success': False,
                'error': _('Нет доступа')
            }, status=403)
        
        # Помечаем как прочитанное
        notification.mark_as_read(request.user)
        
        # Получаем обновленное количество непрочитанных
        unread_count = Notification.get_unread_count(request.user)
        
        return JsonResponse({
            'success': True,
            'unread_count': unread_count
        })
    
    except Exception as e:
        logger.error(f"Error marking notification as read: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
@require_POST
def mark_all_notifications_read(request):
    """AJAX endpoint для пометки всех уведомлений как прочитанных"""
    try:
        unread_notifications = Notification.get_user_notifications(
            request.user,
            include_read=False
        )
        unread_ids = list(unread_notifications.values_list('id', flat=True))

        # Bulk create — один INSERT вместо N
        existing_read_ids = set(
            NotificationRead.objects.filter(
                user=request.user, notification_id__in=unread_ids
            ).values_list('notification_id', flat=True)
        )
        new_reads = [
            NotificationRead(user=request.user, notification_id=nid)
            for nid in unread_ids if nid not in existing_read_ids
        ]
        if new_reads:
            NotificationRead.objects.bulk_create(new_reads, ignore_conflicts=True)

        # Инвалидируем кэш
        from .context_processors import invalidate_notification_cache
        invalidate_notification_cache(request.user.pk)

        return JsonResponse({
            'success': True,
            'marked_count': len(unread_ids),
            'unread_count': 0
        })

    except Exception as e:
        logger.error(f"Error marking all notifications as read: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def notifications_dropdown(request):
    """AJAX endpoint для dropdown — последние 5 уведомлений"""
    try:
        from django.db.models import Exists, OuterRef

        read_subquery = NotificationRead.objects.filter(
            user=request.user,
            notification_id=OuterRef('id')
        )
        all_qs = Notification.get_user_notifications(
            request.user,
            include_read=True
        ).annotate(
            is_read=Exists(read_subquery)
        )
        notifications = all_qs[:5]

        notifications_data = [
            {
                'id': n.id,
                'title': n.title,
                'short_text': n.short_text,
                'is_read': n.is_read,
                'created_at': n.created_at.strftime('%d.%m.%Y %H:%M'),
                'url': reverse('notification_detail', args=[n.id])
            }
            for n in notifications
        ]

        unread_count = Notification.get_unread_count(request.user)

        return JsonResponse({
            'success': True,
            'notifications': notifications_data,
            'unread_count': unread_count,
            'has_more': all_qs.count() > 5
        })

    except Exception as e:
        logger.error(f"Error fetching notifications dropdown: {e}", exc_info=True)
        return JsonResponse({
            'success': False,
            'error': str(e)
        }, status=500)


@login_required
def badge_counts(request):
    """
    Lightweight API endpoint для получения актуальных badge-счётчиков.
    Вызывается фронтендом по таймеру для обновления badge без перезагрузки.
    """
    from .context_processors import (
        unread_messages_count, unread_notifications_count
    )
    msgs = unread_messages_count(request)
    notifs = unread_notifications_count(request)
    return JsonResponse({
        'unread_messages': msgs['unread_messages_count'],
        'unread_notifications': notifs['unread_notifications_count'],
    })


# =============================================================================
# GOOGLE OAUTH2: Onboarding после Google Login
# =============================================================================

def _has_any_profile(user):
    """Проверяет, есть ли у пользователя хотя бы один профиль."""
    try:
        _ = user.teacher_profile.pk
        return True
    except Exception:
        pass
    try:
        _ = user.student_profile.pk
        return True
    except Exception:
        pass
    return False


@login_required
def google_student_onboarding(request):
    """
    Минимальная форма onboarding для студента после Google login.
    Поля: имя, интересующие предметы.
    Создаёт StudentProfile для уже существующего User.
    """
    user = request.user

    if _has_any_profile(user):
        return redirect('dashboard')

    if request.method == 'POST':
        form = GoogleStudentOnboardingForm(request.POST)
        if form.is_valid():
            # Обновляем имя пользователя
            user.first_name = form.cleaned_data['first_name']
            user.last_name = form.cleaned_data.get('last_name', '')
            user.user_type = 'student'
            user.save(update_fields=['first_name', 'last_name', 'user_type'])

            # Создаём профиль студента
            student_profile = StudentProfile.objects.create(
                user=user,
                is_active=True,
            )

            # Устанавливаем интересы
            interests = form.cleaned_data['interests']
            student_profile.interests.set(interests)
            student_profile.desired_subjects.set(interests)

            messages.success(
                request,
                _('Добро пожаловать! Мы подберём для вас подходящих учителей.')
            )

            # Редирект на home с поиском по первому предмету
            first_subject = interests.first()
            if first_subject:
                return redirect(f'{reverse("home")}?search={first_subject.name}')
            return redirect('dashboard')
    else:
        form = GoogleStudentOnboardingForm(initial={
            'first_name': user.first_name,
            'last_name': user.last_name,
        })

    return render(request, 'logic/google_student_onboarding.html', {'form': form})


@login_required
@require_POST
def google_complete_student(request):
    """POST-shortcut: выбор роли student → редирект на onboarding форму."""
    if _has_any_profile(request.user):
        return redirect('dashboard')
    return redirect('google_student_onboarding')


@login_required
@require_POST
def google_complete_teacher(request):
    """
    Подготавливает Google-пользователя к регистрации как учитель.
    Сохраняет данные Google в сессию и перенаправляет на wizard.
    """
    user = request.user

    if _has_any_profile(user):
        messages.info(request, _('У вас уже есть профиль'))
        return redirect('dashboard')

    # Сохраняем данные Google-пользователя в сессию для предзаполнения wizard
    google_data = {
        'google_first_name': user.first_name,
        'google_last_name': user.last_name,
        'google_email': user.email,
        'google_user_id': user.pk,
        'is_google_teacher': True,
    }

    # Выход и удаление заглушки — wizard создаст полноценного учителя
    logout(request)
    # Сессия пересоздаётся после logout, сохраняем данные в новую
    for key, value in google_data.items():
        request.session[key] = value

    try:
        # У пользователя автоматически создаётся Wallet (OneToOne, on_delete=PROTECT)
        # через post_save-сигнал — он блокирует удаление. Сначала снимаем его,
        # иначе stub остаётся, и wizard падает на коллизии уникального email.
        from billing.models import Wallet
        Wallet.objects.filter(user=user).delete()
        user.delete()
        logger.info(f"Google stub user deleted before teacher wizard: {google_data['google_email']}")
    except Exception as e:
        logger.error(f"Error deleting Google stub user: {e}")

    return redirect('teacher_register')


# ============================================
# DAILY REMINDER TEMPLATES (admin-dashboard)
# ============================================

@staff_member_required
def daily_reminders_list(request):
    """
    Список всех шаблонов ежедневной авто-рассылки (утро/вечер × ru/uz/en).
    """
    templates = DailyReminderTemplate.objects.order_by(
        'period', 'language', '-is_active', '-updated_at',
    )

    # Группируем для удобного вывода: { (period, lang): [templates...] }
    grouped = {}
    for tpl in templates:
        grouped.setdefault((tpl.period, tpl.language), []).append(tpl)

    # Счётчики активных шаблонов по каждой паре — чтобы было видно пустые слоты
    summary = []
    for period_code, period_label in DailyReminderTemplate.PERIOD_CHOICES:
        for lang_code, lang_label in DailyReminderTemplate.LANGUAGE_CHOICES:
            items = grouped.get((period_code, lang_code), [])
            summary.append({
                'period': period_code,
                'period_label': period_label,
                'language': lang_code,
                'language_label': lang_label,
                'items': items,
                'active_count': sum(1 for i in items if i.is_active),
                'total_count': len(items),
            })

    context = {
        'summary': summary,
        'total_templates': templates.count(),
        'total_active': templates.filter(is_active=True).count(),
    }
    return render(request, 'admin/daily_reminders.html', context)


@staff_member_required
def daily_reminder_edit(request, template_id=None):
    """
    Создание / редактирование одного шаблона.
    template_id=None → создание, иначе — правка существующего.
    """
    instance = None
    if template_id:
        instance = get_object_or_404(DailyReminderTemplate, id=template_id)

    if request.method == 'POST':
        period = request.POST.get('period', '').strip()
        language = request.POST.get('language', '').strip()
        text = request.POST.get('text', '').strip()
        note = request.POST.get('note', '').strip()
        is_active = request.POST.get('is_active') == 'on'

        valid_periods = {p for p, _ in DailyReminderTemplate.PERIOD_CHOICES}
        valid_langs = {l for l, _ in DailyReminderTemplate.LANGUAGE_CHOICES}

        if period not in valid_periods:
            messages.error(request, _('Выберите корректный период (утро/вечер).'))
        elif language not in valid_langs:
            messages.error(request, _('Выберите корректный язык.'))
        elif not text:
            messages.error(request, _('Текст сообщения не может быть пустым.'))
        elif len(text) > 4000:
            messages.error(request, _('Текст слишком длинный (максимум 4000 символов).'))
        else:
            if instance is None:
                instance = DailyReminderTemplate()
            instance.period = period
            instance.language = language
            instance.text = text
            instance.note = note[:200]
            instance.is_active = is_active
            instance.save()
            messages.success(
                request,
                _('Шаблон обновлён.') if template_id else _('Шаблон создан.'),
            )
            return redirect('daily_reminders_list')

    context = {
        'instance': instance,
        'period_choices': DailyReminderTemplate.PERIOD_CHOICES,
        'language_choices': DailyReminderTemplate.LANGUAGE_CHOICES,
        'prefill_period': request.GET.get('period', ''),
        'prefill_language': request.GET.get('language', ''),
    }
    return render(request, 'admin/daily_reminder_edit.html', context)


@staff_member_required
@require_POST
def daily_reminder_delete(request, template_id):
    """Удалить шаблон."""
    tpl = get_object_or_404(DailyReminderTemplate, id=template_id)
    tpl.delete()
    messages.success(request, _('Шаблон удалён.'))
    return redirect('daily_reminders_list')


@staff_member_required
@require_POST
def daily_reminder_toggle(request, template_id):
    """Переключить статус активности одним кликом."""
    tpl = get_object_or_404(DailyReminderTemplate, id=template_id)
    tpl.is_active = not tpl.is_active
    tpl.save(update_fields=['is_active', 'updated_at'])
    messages.success(
        request,
        _('Шаблон %(state)s.') % {'state': _('включён') if tpl.is_active else _('выключен')},
    )
    return redirect('daily_reminders_list')


@staff_member_required
@require_POST
def daily_reminder_test(request, template_id):
    """
    Отправить данный шаблон текущему администратору в Telegram как тест.
    """
    tpl = get_object_or_404(DailyReminderTemplate, id=template_id)

    tg = TelegramUser.objects.filter(
        user=request.user, started_bot=True,
    ).first()

    if not tg:
        messages.warning(
            request,
            _('Ваш аккаунт не привязан к Telegram-боту — привяжите его, '
              'чтобы получать тестовые сообщения.'),
        )
        return redirect('daily_reminders_list')

    from .admin_telegram_service import admin_telegram_service
    test_text = f"🧪 *Тест рассылки*\n\n{tpl.text}"
    ok = admin_telegram_service.send_message_simple(
        telegram_id=tg.telegram_id,
        text=test_text,
        parse_mode='Markdown',
    )
    if ok:
        messages.success(request, _('Тестовое сообщение отправлено вам в Telegram.'))
    else:
        messages.error(request, _('Не удалось отправить тестовое сообщение.'))
    return redirect('daily_reminders_list')


