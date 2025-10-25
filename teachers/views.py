from django.shortcuts import render
from django.db.models import Q, Min, Max
from .models import TeacherProfile, Subject, City, StudentProfile, ProfileView
from django.shortcuts import get_object_or_404
from django.db.models import Q, Min, Max, Avg
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from .forms import (
    TeacherRegistrationForm, 
    TeacherSubjectsForm, 
    CertificateUploadForm
)
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from .forms import LoginForm, StudentRegistrationForm
from .models import TeacherProfile, TeacherSubject, Certificate


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
        print(f"Error recording profile view: {e}")


def home(request):
    """
    Главная страница с учителями
    БЕЗ ИЗМЕНЕНИЙ - оставлена оригинальная логика
    """
    # Базовый queryset
    teachers = TeacherProfile.objects.filter(is_active=True).select_related(
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
    search_query = request.GET.get('search')
    
    # Применяем фильтры
    if subject_id:
        teachers = teachers.filter(subjects__id=subject_id)
    
    if city_id:
        teachers = teachers.filter(city_id=city_id)
    
    if teaching_format:
        teachers = teachers.filter(teaching_format=teaching_format)
    
    if min_rating:
        teachers = teachers.filter(rating__gte=float(min_rating))
    
    if min_experience:
        teachers = teachers.filter(experience_years__gte=int(min_experience))
    
    # Фильтр по цене (через связанную модель TeacherSubject)
    if min_price:
        teachers = teachers.filter(teachersubject__hourly_rate__gte=float(min_price))
    
    if max_price:
        teachers = teachers.filter(teachersubject__hourly_rate__lte=float(max_price))
    
    # Поиск по имени или биографии
    if search_query:
        teachers = teachers.filter(
            Q(user__first_name__icontains=search_query) |
            Q(user__last_name__icontains=search_query) |
            Q(bio__icontains=search_query)
        )
    
    # Убираем дубликаты и сортируем
    teachers = teachers.distinct().order_by('-rating', '-created_at')
    
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
    
    # Данные для фильтров
    all_subjects = Subject.objects.filter(is_active=True).order_by('name')
    all_cities = City.objects.filter(is_active=True).order_by('name')
    
    # Получаем диапазон цен для слайдера
    price_range = TeacherProfile.objects.filter(is_active=True).aggregate(
        min_price=Min('teachersubject__hourly_rate'),
        max_price=Max('teachersubject__hourly_rate')
    )
    
    context = {
        'teachers': teachers_page,  # Изменено: теперь используем объект Page
        'total_teachers': paginator.count,  # Общее количество учителей
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
    }
    
    return render(request, 'logic/home.html', context)


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
        students = students.filter(budget_max__gte=float(min_budget))
    
    if max_budget:
        students = students.filter(budget_min__lte=float(max_budget))
    
    # Поиск по имени или описанию
    if search_query:
        students = students.filter(
            Q(user__first_name__icontains=search_query) |
            Q(user__last_name__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(bio__icontains=search_query)
        )
    
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
    
    # Данные для фильтров
    all_subjects = Subject.objects.filter(is_active=True).order_by('name')
    all_cities = City.objects.filter(is_active=True).order_by('name')
    
    # Получаем диапазон бюджета для слайдера
    budget_range = StudentProfile.objects.filter(
        is_active=True,
        budget_max__isnull=False
    ).aggregate(
        min_budget=Min('budget_min'),
        max_budget=Max('budget_max')
    )
    
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
    
    # ✅ НОВОЕ: Записываем просмотр профиля
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
        is_active=True
    ).exclude(id=teacher.id).distinct()[:3]
    
    context = {
        'teacher': teacher,
        'reviews': reviews,
        'rating_stats': rating_stats,
        'rating_distribution': rating_distribution,
        'is_favorite': is_favorite,
        'similar_teachers': similar_teachers,
    }
    
    return render(request, 'logic/teacher_detail.html', context)


def student_detail(request, id):
    """
    Детальная страница ученика с подсчетом просмотров
    """
    student = get_object_or_404(
        StudentProfile.objects.select_related('user', 'city')
        .prefetch_related(
            'desired_subjects',
            'interests'
        ),
        id=id,
        is_active=True
    )
    
    # ✅ НОВОЕ: Записываем просмотр профиля
    record_profile_view(request, student, 'student')
    
    # Получаем все желаемые предметы
    desired_subjects = student.desired_subjects.all()
    
    # Получаем дополнительные интересы
    other_interests = student.interests.exclude(
        id__in=desired_subjects.values_list('id', flat=True)
    )
    
    # Похожие ученики по предметам
    similar_students = StudentProfile.objects.filter(
        desired_subjects__in=desired_subjects,
        is_active=True
    ).exclude(id=student.id).distinct()[:3]
    
    # Получаем учителей, которые преподают нужные предметы
    suggested_teachers = TeacherProfile.objects.filter(
        subjects__in=desired_subjects,
        is_active=True
    ).distinct().order_by('-rating')[:6]
    
    # Проверяем, добавлен ли ученик в избранное учителем
    is_favorited = False
    if request.user.is_authenticated and request.user.user_type == 'teacher':
        pass
    
    context = {
        'student': student,
        'desired_subjects': desired_subjects,
        'other_interests': other_interests,
        'similar_students': similar_students,
        'suggested_teachers': suggested_teachers,
        'is_favorited': is_favorited,
    }
    
    return render(request, 'logic/student_detail.html', context)


# Остальные функции БЕЗ ИЗМЕНЕНИЙ
def teacher_register_step1(request):
    """Шаг 1: Основная информация"""
    if request.method == 'POST':
        form = TeacherRegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save()
            request.session['teacher_registration_user_id'] = user.id
            messages.success(
                request, 
                'Основная информация сохранена! Теперь добавьте предметы и цены.'
            )
            return redirect('teacher_register_step2')
    else:
        form = TeacherRegistrationForm()
    
    context = {
        'form': form,
        'step': 1,
        'total_steps': 3
    }
    return render(request, 'logic/teacher_register_step1.html', context)
 


def teacher_register_step2(request):
    """Шаг 2: Предметы и цены"""
    user_id = request.session.get('teacher_registration_user_id')
    
    if not user_id:
        messages.error(request, 'Пожалуйста, начните регистрацию с первого шага')
        return redirect('teacher_register_step1')
    
    try:
        teacher_profile = TeacherProfile.objects.get(user_id=user_id)
    except TeacherProfile.DoesNotExist:
        messages.error(request, 'Профиль учителя не найден')
        return redirect('teacher_register_step1')
    
    if request.method == 'POST':
        form = TeacherSubjectsForm(request.POST, teacher=teacher_profile)
        
        if form.is_valid():
            # Удаляем старые предметы
            TeacherSubject.objects.filter(teacher=teacher_profile).delete()
            
            subjects_added = 0
            
            # Проходим по всем 5 возможным предметам
            for i in range(1, 6):
                subject = form.cleaned_data.get(f'subject_{i}')
                hourly_rate = form.cleaned_data.get(f'hourly_rate_{i}')
                
                # Сохраняем только если указаны И предмет И цена
                if subject and hourly_rate and hourly_rate > 0:
                    TeacherSubject.objects.create(
                        teacher=teacher_profile,
                        subject=subject,
                        hourly_rate=hourly_rate,
                        is_free_trial=form.cleaned_data.get(f'is_free_trial_{i}', False),
                        description=form.cleaned_data.get(f'description_{i}', '')
                    )
                    subjects_added += 1
            
            # Проверка что добавлен хотя бы один предмет
            if subjects_added == 0:
                messages.error(request, 'Необходимо добавить хотя бы один предмет с указанием цены')
                context = {
                    'form': form,
                    'step': 2,
                    'total_steps': 3
                }
                return render(request, 'logic/teacher_register_step2.html', context)
            
            messages.success(
                request, 
                f'Добавлено {subjects_added} предмет(ов). Теперь загрузите сертификаты.'
            )
            return redirect('teacher_register_step3')
        else:
            # Показываем ошибки формы
            messages.error(request, 'Пожалуйста, исправьте ошибки в форме')
            context = {
                'form': form,
                'step': 2,
                'total_steps': 3
            }
            return render(request, 'logic/teacher_register_step2.html', context)    
        
    else:
        form = TeacherSubjectsForm(teacher=teacher_profile)
        context = {
            'form': form,
            'step': 2,
            'total_steps': 3
        }
        return render(request, 'logic/teacher_register_step2.html', context)


def teacher_register_step3(request):
    """Шаг 3: Сертификаты (опционально)"""
    user_id = request.session.get('teacher_registration_user_id')
    
    if not user_id:
        messages.error(request, 'Пожалуйста, начните регистрацию с первого шага')
        return redirect('teacher_register_step1')
    
    try:
        teacher_profile = TeacherProfile.objects.get(user_id=user_id)
    except TeacherProfile.DoesNotExist:
        messages.error(request, 'Профиль учителя не найден')
        return redirect('teacher_register_step1')
    
    if request.method == 'POST':
        if 'skip' in request.POST:
            return redirect('teacher_register_complete')
        
        form = CertificateUploadForm(request.POST, request.FILES)
        
        if form.is_valid():
            certificate = form.save()
            teacher_profile.certificates.add(certificate)
            
            messages.success(request, 'Сертификат добавлен!')
            
            if 'add_more' in request.POST:
                return redirect('teacher_register_step3')
            else:
                return redirect('teacher_register_complete')
    else:
        form = CertificateUploadForm()
    
    certificates = teacher_profile.certificates.all()
    
    context = {
        'form': form,
        'certificates': certificates,
        'step': 3,
        'total_steps': 3
    }
    return render(request, 'logic/teacher_register_step3.html', context)


def teacher_register_complete(request):
    """Завершение регистрации"""
    user_id = request.session.get('teacher_registration_user_id')
    
    if not user_id:
        return redirect('teacher_register_step1')
    
    try:
        teacher_profile = TeacherProfile.objects.get(user_id=user_id)
    except TeacherProfile.DoesNotExist:
        messages.error(request, 'Профиль учителя не найден')
        return redirect('teacher_register_step1')
    
    if 'teacher_registration_user_id' in request.session:
        del request.session['teacher_registration_user_id']
    
    context = {
        'teacher': teacher_profile
    }
    return render(request, 'logic/teacher_register_complete.html', context)


def remove_certificate(request, certificate_id):
    """Удаление сертификата во время регистрации"""
    user_id = request.session.get('teacher_registration_user_id')
    
    if not user_id:
        messages.error(request, 'Сессия истекла')
        return redirect('teacher_register_step1')
    
    certificate = get_object_or_404(Certificate, id=certificate_id)
    certificate.delete()
    
    messages.success(request, 'Сертификат удален')
    return redirect('teacher_register_step3')


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
                try:
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    user_obj = User.objects.get(email=username)
                    user = authenticate(username=user_obj.username, password=password)
                except:
                    pass
            
            if user is not None:
                login(request, user)
                
                if not remember_me:
                    request.session.set_expiry(0)
                
                messages.success(request, f'Добро пожаловать, {user.get_full_name()}!')
                
                next_url = request.GET.get('next')
                if next_url:
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
        return redirect('home')
    
    return render(request, 'logic/register_choose.html')


def register_student(request):
    """Регистрация ученика"""
    if request.user.is_authenticated:
        return redirect('home')
    
    if request.method == 'POST':
        form = StudentRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            
            messages.success(
                request,
                'Регистрация прошла успешно! Добро пожаловать в UstozHub!'
            )
            return redirect('profile')
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
            return redirect('teacher_register_step1')
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


from .forms import (
    TeacherRegistrationForm, 
    TeacherSubjectsForm, 
    CertificateUploadForm,
    LoginForm, 
    StudentRegistrationForm,
    TeacherProfileEditForm,
    StudentProfileEditForm,
    UserProfileEditForm
)

@login_required
def teacher_profile_edit(request):
    """Редактирование профиля учителя"""
    try:
        teacher_profile = request.user.teacher_profile
    except TeacherProfile.DoesNotExist:
        messages.error(request, 'Профиль учителя не найден')
        return redirect('home')
    
    if request.method == 'POST':
        user_form = UserProfileEditForm(request.POST, request.FILES, instance=request.user)
        profile_form = TeacherProfileEditForm(request.POST, instance=teacher_profile)
        
        if user_form.is_valid() and profile_form.is_valid():
            user_form.save()
            profile_form.save()
            
            messages.success(request, 'Профиль успешно обновлен!')
            return redirect('profile')
        else:
            messages.error(request, 'Пожалуйста, исправьте ошибки в форме')
    else:
        user_form = UserProfileEditForm(instance=request.user)
        profile_form = TeacherProfileEditForm(instance=teacher_profile)
    
    context = {
        'user_form': user_form,
        'profile_form': profile_form,
        'teacher': teacher_profile
    }
    return render(request, 'logic/teacher_profile_edit.html', context)


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


from django.http import JsonResponse

@login_required
def toggle_profile_status(request):
    """
    AJAX-функция для быстрого переключения статуса активности профиля
    """
    if request.method == 'POST':
        if request.user.user_type == 'teacher':
            try:
                profile = request.user.teacher_profile
                profile.is_active = not profile.is_active
                profile.save()
                
                status_text = 'активен' if profile.is_active else 'деактивирован'
                messages.success(request, f'Ваш профиль {status_text} в поиске')
                
                return JsonResponse({
                    'success': True,
                    'is_active': profile.is_active,
                    'message': f'Профиль {status_text}'
                })
            except TeacherProfile.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Профиль не найден'})
        
        elif request.user.user_type == 'student':
            try:
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
            except StudentProfile.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Профиль не найден'})
    
    return JsonResponse({'success': False, 'error': 'Неверный запрос'})