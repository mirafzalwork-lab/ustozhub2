from django.shortcuts import render, redirect, get_object_or_404
from django.db.models import F, Q, Min, Max, Avg, Count, Case, When, Value, IntegerField
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
from django.http import JsonResponse, HttpResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
import csv
import logging
from datetime import datetime, timedelta

# Глобальный logger для всего модуля
logger = logging.getLogger(__name__)

from .models import (
    TeacherProfile, StudentProfile, Subject, City, ProfileView,
    TeacherSubject, Certificate, User, Favorite, FavoriteStudent,
    Conversation, Message, Review, ViewCounter, TelegramUser,
    SubjectCategory, SubjectSearchLog, Notification, NotificationRead,
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
    
    # Обычные пользователи НЕ могут видеть контакты — общение только через платформу
    return False


def get_client_ip(request):
    """Получить IP адрес клиента"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
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
    
    # Создаем запись о просмотре
    try:
        view_data = {
            'profile_type': profile_type,
            'viewer_ip': ip_address,
            'viewer_user': viewer_user,
            'user_agent': user_agent[:500] if user_agent else '',  # Ограничиваем длину
        }
        
        if profile_type == 'teacher':
            view_data['teacher_profile'] = profile
        else:
            view_data['student_profile'] = profile
        
        ProfileView.objects.create(**view_data)
    except Exception as e:
        # Логируем ошибку, но не прерываем работу приложения
        logger.error(f"Error recording profile view: {e}", exc_info=True)


def _apply_sort(queryset, sort_by):
    """Применяет сортировку к queryset учителей. is_featured всегда первый ключ."""
    if sort_by == 'price_low':
        return queryset.annotate(
            min_price=Min('teachersubject__hourly_rate')
        ).order_by('-is_featured', 'min_price', '-ranking_score')
    elif sort_by == 'price_high':
        return queryset.annotate(
            min_price=Min('teachersubject__hourly_rate')
        ).order_by('-is_featured', '-min_price', '-ranking_score')
    elif sort_by == 'rating':
        return queryset.order_by('-is_featured', '-rating', '-total_reviews', '-ranking_score')
    elif sort_by == 'experience':
        return queryset.order_by('-is_featured', '-experience_years', '-rating')
    elif sort_by == 'newest':
        return queryset.order_by('-is_featured', '-created_at')
    else:  # recommended (default)
        return queryset.order_by('-is_featured', '-ranking_score', '-rating', '-created_at')


def home(request):
    """
    Главная страница с учителями
    """

    track_view(request, 'home')
    # Базовый queryset: только активные и одобренные учителя
    teachers = TeacherProfile.objects.filter(
        is_active=True, moderation_status='approved'
    ).select_related(
        'user', 'city'
    ).prefetch_related(
        'subjects', 'teachersubject_set__subject', 'reviews'
    )
    
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

            # При поиске: featured всегда первый, потом релевантность
            if sort_by == 'price_low':
                teachers = teachers.annotate(
                    min_price=Min('teachersubject__hourly_rate')
                ).order_by('-is_featured', '-relevance', 'min_price')
            elif sort_by == 'price_high':
                teachers = teachers.annotate(
                    min_price=Min('teachersubject__hourly_rate')
                ).order_by('-is_featured', '-relevance', '-min_price')
            elif sort_by == 'rating':
                teachers = teachers.order_by('-is_featured', '-relevance', '-rating', '-total_reviews')
            elif sort_by == 'experience':
                teachers = teachers.order_by('-is_featured', '-relevance', '-experience_years', '-rating')
            elif sort_by == 'newest':
                teachers = teachers.order_by('-is_featured', '-relevance', '-created_at')
            else:  # recommended (default)
                teachers = teachers.order_by('-is_featured', '-relevance', '-ranking_score', '-rating', '-total_reviews')
        else:
            teachers = teachers.distinct()
            teachers = _apply_sort(teachers, sort_by)
    else:
        teachers = teachers.distinct()
        teachers = _apply_sort(teachers, sort_by)
    
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

    context = {
        'teachers': teachers_page,
        'total_teachers': paginator.count,
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
        messages.error(request, 'У вас нет доступа к админ панели')
        return redirect('home')
    
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
    total_profile_views = ProfileView.objects.count()
    profile_views_today = ProfileView.objects.filter(viewed_at__gte=today_start).count()
    profile_views_week = ProfileView.objects.filter(viewed_at__gte=week_ago).count()
    profile_views_month = ProfileView.objects.filter(viewed_at__gte=month_ago).count()
    
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
        'conversation__student'
    ).order_by('-created_at')[:15]
    
    # ========== ПОСЛЕДНИЕ РЕГИСТРАЦИИ ==========
    recent_teachers = TeacherProfile.objects.select_related('user', 'city').order_by('-created_at')[:8]
    recent_students = StudentProfile.objects.select_related('user', 'city').order_by('-created_at')[:8]
    
    # ========== СТАТИСТИКА ПО СТРАНИЦАМ ==========
    page_stats = ViewCounter.objects.filter(month=current_month).values('page').annotate(
        view_count=Count('id')
    ).order_by('-view_count')[:10]
    
    # ========== ТОП ПРЕДМЕТОВ ==========
    top_subjects = Subject.objects.annotate(
        teacher_count=Count('teacherprofile'),
        student_interest_count=Count('interested_students')
    ).order_by('-teacher_count')[:10]
    
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
        'page_stats': page_stats,
        'top_subjects': top_subjects,
    
        # Вспомогательные данные
        'current_month': current_month.strftime('%B %Y'),
        'now': now,
    }
    
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

def students_list(request):
    """
    Страница со списком учеников, которые ищут учителей
    Аналогична home() но для учеников
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
    """Детальная страница учителя с подсчетом просмотров"""
    teacher = get_object_or_404(
        TeacherProfile.objects.select_related('user', 'city')
        .prefetch_related(
            'teachersubject_set__subject',
            'certificates',
            'reviews__student',
            'reviews__subject'
        ),
        id=id,
        is_active=True
    )

    record_profile_view(request, teacher, 'teacher')

    reviews = teacher.reviews.select_related('student', 'subject').order_by('-created_at')

    rating_stats = reviews.aggregate(
        avg_knowledge=Avg('knowledge_rating'),
        avg_communication=Avg('communication_rating'),
        avg_punctuality=Avg('punctuality_rating')
    )

    rating_distribution = {
        5: reviews.filter(rating=5).count(),
        4: reviews.filter(rating=4).count(),
        3: reviews.filter(rating=3).count(),
        2: reviews.filter(rating=2).count(),
        1: reviews.filter(rating=1).count(),
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

    context = {
        'teacher': teacher,
        'reviews': reviews,
        'rating_stats': rating_stats,
        'rating_distribution': rating_distribution,
        'is_favorite': is_favorite,
        'similar_teachers': similar_teachers,
        'can_view_contacts': can_view_contacts,
        'show_auth_prompt': show_auth_prompt,
    }

    return render(request, 'logic/teacher_detail.html', context)


def student_detail(request, id):
    """Детальная страница ученика с подсчетом просмотров"""
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
    """Вход в систему"""
    if request.user.is_authenticated:
        return redirect('home')
    
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            username = form.cleaned_data.get('username')
            password = form.cleaned_data.get('password')
            remember_me = form.cleaned_data.get('remember_me')
            
            user = authenticate(username=username, password=password)
            
            if user is None:
                # Fallback: try to authenticate by email
                try:
                    user_obj = User.objects.get(email=username)
                    user = authenticate(username=user_obj.username, password=password)
                except (User.DoesNotExist, User.MultipleObjectsReturned):
                    pass
            
            if user is not None:
                login(request, user)
                
                if not remember_me:
                    request.session.set_expiry(0)
                
                messages.success(request, f'Добро пожаловать, {user.get_full_name()}!')
                
                next_url = request.GET.get('next')
                if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                    return redirect(next_url)
                
                return redirect('profile')
            else:
                messages.error(request, 'Неверное имя пользователя или пароль')
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
        messages.success(request, 'Вы успешно вышли из системы')
        return redirect('home')
    
    return render(request, 'logic/logout_confirm.html')


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
            return redirect('home')

    return render(request, 'logic/register_choose.html')


def register_student(request):
    """Регистрация ученика"""
    if request.user.is_authenticated:
        # Разрешаем доступ Google-пользователям без профиля студента
        has_student = False
        try:
            _ = request.user.student_profile.pk
            has_student = True
        except Exception:
            pass
        if has_student:
            return redirect('home')
    
    if request.method == 'POST':
        form = StudentRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            
            messages.success(
                request,
                'Регистрация прошла успешно! Сейчас мы подберем для вас подходящих учителей.'
            )
            
            # Получаем желаемые предметы студента и перенаправляем на home с поиском
            try:
                student_profile = user.student_profile
                desired_subjects = student_profile.desired_subjects.all()
                
                # Если есть желаемые предметы, перенаправляем на home с поиском по первому предмету
                if desired_subjects.exists():
                    first_subject = desired_subjects.first()
                    return redirect(f'{reverse("home")}?search={first_subject.name}')
            except (StudentProfile.DoesNotExist, AttributeError):
                pass
            
            # Если нет предметов, просто перенаправляем на home
            return redirect('home')
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
            
            return render(request, 'logic/teacher_profile.html', {
                'teacher': teacher_profile,
                'views_stats': views_stats,  # ✅ НОВОЕ
            })
        except TeacherProfile.DoesNotExist:
            messages.warning(request, 'Завершите регистрацию учителя')
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
        messages.error(request, 'Профиль учителя не найден')
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
            
            messages.success(request, 'Профиль успешно обновлен!')
            return redirect('profile')
        else:
            # Выводим конкретные ошибки для отладки
            if not user_form.is_valid():
                for field, errors in user_form.errors.items():
                    messages.error(request, f'Ошибка в поле {field}: {", ".join(errors)}')
            if not profile_form.is_valid():
                for field, errors in profile_form.errors.items():
                    messages.error(request, f'Ошибка в поле {field}: {", ".join(errors)}')
            if not subject_forms_valid:
                messages.error(request, 'Проверьте правильность заполнения предметов')
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
        'teacher_subjects': teacher_subjects
    }
    return render(request, 'logic/teacher_profile_edit.html', context)


@login_required
def delete_teacher_subject(request, subject_id):
    """Удаление предмета учителя"""
    try:
        teacher_profile = request.user.teacher_profile
        teacher_subject = TeacherSubject.objects.get(id=subject_id, teacher=teacher_profile)
        teacher_subject.delete()
        messages.success(request, 'Предмет успешно удален')
    except (TeacherProfile.DoesNotExist, TeacherSubject.DoesNotExist):
        messages.error(request, 'Предмет не найден')
    
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
            
            messages.success(request, 'Профиль успешно обновлен!')
            return redirect('profile')
        else:
            messages.error(request, 'Пожалуйста, исправьте ошибки в форме')
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
        return JsonResponse({'success': False, 'error': 'Неверный запрос'})

    try:
        if request.user.user_type == 'teacher':
            profile = request.user.teacher_profile
        else:
            profile = request.user.student_profile

        profile.is_active = not profile.is_active
        profile.save()

        status_text = 'активен' if profile.is_active else 'деактивирован'
        messages.success(request, f'Ваш профиль {status_text} в поиске')

        return JsonResponse({
            'success': True,
            'is_active': profile.is_active,
            'message': f'Профиль {status_text}'
        })
    except (TeacherProfile.DoesNotExist, StudentProfile.DoesNotExist):
        return JsonResponse({'success': False, 'error': 'Профиль не найден'})


@login_required
def toggle_favorite_teacher(request, teacher_id):
    """Студент добавляет/удаляет учителя в избранное"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Неверный метод'})

    if request.user.user_type != 'student':
        return JsonResponse({'success': False, 'error': 'Доступ запрещен'})

    teacher = get_object_or_404(TeacherProfile, id=teacher_id, is_active=True)

    fav, created = Favorite.objects.get_or_create(student=request.user, teacher=teacher)
    if not created:
        fav.delete()
        return JsonResponse({'success': True, 'favorited': False})
    return JsonResponse({'success': True, 'favorited': True})


@login_required
def toggle_favorite_student(request, student_id):
    """Учитель добавляет/удаляет ученика в избранное"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Неверный метод'})

    if request.user.user_type != 'teacher' or not hasattr(request.user, 'teacher_profile'):
        return JsonResponse({'success': False, 'error': 'Доступ запрещен'})

    try:
        teacher_profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Профиль учителя не найден'})
    
    student = get_object_or_404(StudentProfile, id=student_id, is_active=True)

    fav, created = FavoriteStudent.objects.get_or_create(teacher=teacher_profile, student=student)
    if not created:
        fav.delete()
        return JsonResponse({'success': True, 'favorited': False})
    return JsonResponse({'success': True, 'favorited': True})


@login_required
def my_favorite_teachers(request):
    """Список избранных учителей для ученика"""
    if request.user.user_type != 'student':
        messages.error(request, 'Доступ запрещен')
        return redirect('home')

    favorites = Favorite.objects.select_related('teacher__user', 'teacher__city').filter(student=request.user)
    teachers = [f.teacher for f in favorites]
    return render(request, 'logic/favorites_teachers.html', {'teachers': teachers})


@login_required
def my_favorite_students(request):
    """Список избранных учеников для учителя"""
    if request.user.user_type != 'teacher' or not hasattr(request.user, 'teacher_profile'):
        messages.error(request, 'Доступ запрещен')
        return redirect('home')

    favorites = FavoriteStudent.objects.select_related('student__user', 'student__city').filter(teacher=request.user.teacher_profile)
    students = [f.student for f in favorites]
    return render(request, 'logic/favorites_students.html', {'students': students})


@login_required
def student_suggestions(request):
    """Страница с подходящими учителями для текущего ученика сразу после регистрации"""
    if request.user.user_type != 'student':
        messages.error(request, 'Доступ запрещен')
        return redirect('home')

    try:
        student = request.user.student_profile
    except StudentProfile.DoesNotExist:
        messages.warning(request, 'Заполните профиль ученика')
        return redirect('student_profile_edit')

    desired_subjects = student.desired_subjects.all()

    teachers = TeacherProfile.objects.filter(
        is_active=True,
        moderation_status='approved',
        subjects__in=desired_subjects
    ).select_related('user', 'city').prefetch_related('teachersubject_set__subject').distinct().order_by('-is_featured', '-rating', '-created_at')[:24]

    # fallback: если нет указанных предметов, показать топ-учителей
    if not teachers:
        teachers = TeacherProfile.objects.filter(
            is_active=True, moderation_status='approved'
        ).select_related('user', 'city').prefetch_related('teachersubject_set__subject').order_by('-is_featured', '-rating', '-created_at')[:12]

    return render(request, 'logic/student_suggestions.html', {
        'student': student,
        'teachers': teachers,
        'desired_subjects': desired_subjects,
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

    # Общая аннотация непрочитанных сообщений
    unread_annotation = Count(
        Case(
            When(Q(messages__is_read=False) & ~Q(messages__sender=user), then=1),
            output_field=IntegerField()
        )
    )
    # Prefetch для загрузки последнего сообщения без N+1
    messages_prefetch = Prefetch(
        'messages',
        queryset=Message.objects.select_related('sender').order_by('-created_at'),
        to_attr='prefetched_messages'
    )

    if user.user_type == 'teacher':
        try:
            teacher_profile = user.teacher_profile
        except TeacherProfile.DoesNotExist:
            messages.warning(request, 'Завершите регистрацию учителя')
            return redirect('teacher_register')

        conversations = Conversation.objects.filter(
            teacher=teacher_profile,
            is_active=True,
            messages__isnull=False,
        ).select_related(
            'student', 'subject'
        ).prefetch_related(messages_prefetch).annotate(
            unread_count=unread_annotation
        ).distinct().order_by('-updated_at')
    else:
        try:
            user.student_profile
        except StudentProfile.DoesNotExist:
            messages.warning(request, 'Заполните профиль ученика')
            return redirect('student_profile_edit')

        conversations = Conversation.objects.filter(
            student=user,
            is_active=True,
            messages__isnull=False,
        ).select_related(
            'teacher__user', 'teacher__city', 'subject'
        ).prefetch_related(messages_prefetch).annotate(
            unread_count=unread_annotation
        ).distinct().order_by('-updated_at')

    conversations_with_info = []
    for conv in conversations:
        last_message = conv.prefetched_messages[0] if conv.prefetched_messages else None
        conversations_with_info.append({
            'conversation': conv,
            'last_message': last_message,
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
                message.save()
                
                # Обновляем время последнего обновления переписки
                conversation.save()  # updated_at обновится автоматически
                
                messages.success(request, 'Сообщение отправлено!')
                return redirect('conversation_detail', conversation_id=conversation_id)
            else:
                messages.error(request, 'Ошибка при отправке сообщения')
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
        messages.error(request, 'Переписка не найдена или доступ запрещен')
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
        messages.error(request, 'Вы не можете писать себе')
        return redirect('home')
    
    # Определяем, кто учитель, а кто ученик
    if current_user.user_type == 'teacher':
        teacher_profile = current_user.teacher_profile
        student = target_user
        
        # Проверяем, что целевой пользователь - ученик
        if target_user.user_type != 'student':
            messages.error(request, 'Вы можете писать только ученикам')
            return redirect('home')
    else:
        # Текущий пользователь - ученик
        if target_user.user_type != 'teacher':
            messages.error(request, 'Вы можете писать только учителям')
            return redirect('home')
        
        try:
            teacher_profile = target_user.teacher_profile
        except TeacherProfile.DoesNotExist:
            messages.error(request, 'Профиль учителя не найден')
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
        return JsonResponse({'success': False, 'error': 'Только POST запросы'})
    
    user = request.user
    
    try:
        conversation = _get_user_conversation(user, conversation_id)

        # Проверяем форму
        form = MessageForm(request.POST)
        if form.is_valid():
            message = form.save(commit=False)
            message.conversation = conversation
            message.sender = user
            message.save()
            
            # Обновляем время последнего обновления переписки
            conversation.save()
            
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
            'error': 'Произошла ошибка при отправке сообщения'
        })


@login_required
def mark_messages_read(request, conversation_id):
    """
    Отметить сообщения как прочитанные (AJAX)
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Только POST запросы'})
    
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
            'error': 'Произошла ошибка'
        })


@login_required
def delete_conversation(request, conversation_id):
    """
    Удалить (деактивировать) переписку
    """
    if request.method != 'POST':
        messages.error(request, 'Неверный метод запроса')
        return redirect('conversations_list')
    
    user = request.user
    
    try:
        conversation = _get_user_conversation(user, conversation_id, require_active=False)

        # Деактивируем переписку вместо удаления
        conversation.is_active = False
        conversation.save()
        
        messages.success(request, 'Переписка удалена')
        return redirect('conversations_list')
        
    except Exception as e:
        logger.error(f"Ошибка в delete_conversation: {e}", exc_info=True)
        messages.error(request, 'Ошибка при удалении переписки')
        return redirect('conversations_list')


# =============================================================================
# API ДЛЯ ПОИСКА И ВЫБОРА ПРЕДМЕТОВ
# =============================================================================

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
                'name': subject.name,
                'description': subject.description[:100] if subject.description else '',
                'category': subject.category.name if subject.category else 'Без категории',
                'category_color': subject.category.color if subject.category else '#999999',
                'icon': subject.icon or 'fas fa-book',
                'is_popular': subject.is_popular,
                'teachers_count': teachers_count
            })

        # Логируем поиск для аналитики
        try:
            SubjectSearchLog.objects.create(
                query=query[:200],  # Ограничиваем длину
                user=request.user if request.user.is_authenticated else None,
                ip_address=get_client_ip(request),
                found_results_count=len(results)
            )
        except Exception as e:
            logger.warning(f"Failed to log subject search: {e}")

        return JsonResponse({'results': results})
    
    except Exception as e:
        logger.error(f"Error in subjects_autocomplete: {e}", exc_info=True)
        return JsonResponse({'error': 'Ошибка поиска предметов'}, status=500)


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
        return JsonResponse({'error': 'Категория не найдена'}, status=404)

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
            messages.error(request, 'Сообщение не может быть пустым')
            return redirect('telegram_management')
        
        # Ограничиваем длину сообщения
        if len(message_text) > 4000:
            messages.error(request, 'Сообщение слишком длинное (максимум 4000 символов)')
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
            messages.warning(request, 'Нет пользователей для отправки сообщения')
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
            messages.error(request, f'Ошибка сервиса отправки: {str(e)}')
        
        if success_count > 0:
            messages.success(request, f'Сообщение успешно отправлено {success_count} пользователям')
        
        if error_count > 0:
            messages.warning(request, f'Не удалось отправить {error_count} пользователям (возможно, заблокировали бота или удалили чат)')
            
    except Exception as e:
        messages.error(request, f'Ошибка при отправке сообщений: {str(e)}')
    
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
            messages.error(request, 'Необходимо выбрать пользователя и ввести сообщение')
            return redirect('telegram_management')
        
        telegram_user = get_object_or_404(TelegramUser, id=user_id)
        
        if not telegram_user.started_bot:
            messages.error(request, 'Пользователь не активировал бота')
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
            messages.success(request, f'Сообщение отправлено пользователю {telegram_user.first_name}')
        else:
            messages.error(request, f'Не удалось отправить сообщение пользователю {telegram_user.first_name} (возможно, заблокировал бота или удалил чат)')
            
    except Exception as e:
        messages.error(request, f'Ошибка: {str(e)}')
    
    return redirect('telegram_management')


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
        messages.error(request, 'У вас нет доступа к этому уведомлению.')
        return redirect('notifications_list')
    
    notification.mark_as_read(request.user)

    context = {
        'notification': notification,
        'is_read': True,
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
                'error': 'Нет доступа'
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
        return redirect('home')

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
                'Добро пожаловать! Мы подберём для вас подходящих учителей.'
            )

            # Редирект на home с поиском по первому предмету
            first_subject = interests.first()
            if first_subject:
                return redirect(f'{reverse("home")}?search={first_subject.name}')
            return redirect('home')
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
        return redirect('home')
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
        messages.info(request, 'У вас уже есть профиль')
        return redirect('home')

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
        user.delete()
        logger.info(f"Google stub user deleted before teacher wizard: {google_data['google_email']}")
    except Exception as e:
        logger.error(f"Error deleting Google stub user: {e}")

    return redirect('teacher_register')


