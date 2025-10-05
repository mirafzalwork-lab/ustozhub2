from django.shortcuts import render
from django.db.models import Q, Min, Max
from .models import TeacherProfile, Subject, City
from django.shortcuts import get_object_or_404
from django.db.models import Q, Min, Max, Avg
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from .forms import (
    TeacherRegistrationForm, 
    TeacherSubjectsForm, 
    CertificateUploadForm
)
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from .forms import LoginForm, StudentRegistrationForm
from .models import TeacherProfile, TeacherSubject, Certificate

def home(request):
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
    
    # Данные для фильтров
    all_subjects = Subject.objects.filter(is_active=True).order_by('name')
    all_cities = City.objects.filter(is_active=True).order_by('name')
    
    # Получаем диапазон цен для слайдера
    price_range = TeacherProfile.objects.filter(is_active=True).aggregate(
        min_price=Min('teachersubject__hourly_rate'),
        max_price=Max('teachersubject__hourly_rate')
    )
    
    context = {
        'teachers': teachers,
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






def detail(request, id):
    """Детальная страница учителя"""
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
    
    # Получаем отзывы с детальной информацией
    reviews = teacher.reviews.select_related('student', 'subject').order_by('-created_at')
    
    # Статистика по рейтингам
    rating_stats = reviews.aggregate(
        avg_knowledge=Avg('knowledge_rating'),
        avg_communication=Avg('communication_rating'),
        avg_punctuality=Avg('punctuality_rating')
    )
    
    # Распределение оценок (для графика)
    rating_distribution = {
        5: reviews.filter(rating=5).count(),
        4: reviews.filter(rating=4).count(),
        3: reviews.filter(rating=3).count(),
        2: reviews.filter(rating=2).count(),
        1: reviews.filter(rating=1).count(),
    }
    
    # Проверяем, добавлен ли учитель в избранное текущим пользователем
    is_favorite = False
    if request.user.is_authenticated:
        is_favorite = teacher.favorited_by.filter(student=request.user).exists()
    
    # Похожие учителя (по предметам)
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



def teacher_register_step1(request):
    """Шаг 1: Основная информация"""
    if request.method == 'POST':
        form = TeacherRegistrationForm(request.POST, request.FILES)
        if form.is_valid():
            user = form.save()
            
            # Сохраняем ID пользователя в сессии для следующих шагов
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
            # Удаляем старые предметы (если есть)
            TeacherSubject.objects.filter(teacher=teacher_profile).delete()
            
            # Добавляем новые предметы
            for i in range(1, 6):
                subject = form.cleaned_data.get(f'subject_{i}')
                
                if subject:
                    TeacherSubject.objects.create(
                        teacher=teacher_profile,
                        subject=subject,
                        hourly_rate=form.cleaned_data[f'hourly_rate_{i}'],
                        is_free_trial=form.cleaned_data.get(f'is_free_trial_{i}', False),
                        description=form.cleaned_data.get(f'description_{i}', '')
                    )
            
            messages.success(request, 'Предметы добавлены! Переходим к сертификатам.')
            return redirect('teacher_register_step3')
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
            # Пропустить этот шаг
            return redirect('teacher_register_complete')
        
        form = CertificateUploadForm(request.POST, request.FILES)
        
        if form.is_valid():
            certificate = form.save()
            teacher_profile.certificates.add(certificate)
            
            messages.success(request, 'Сертификат добавлен!')
            
            # Если нажата кнопка "Добавить еще"
            if 'add_more' in request.POST:
                return redirect('teacher_register_step3')
            else:
                return redirect('teacher_register_complete')
    else:
        form = CertificateUploadForm()
    
    # Получаем уже добавленные сертификаты
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
    
    # Очищаем сессию
    if 'teacher_registration_user_id' in request.session:
        del request.session['teacher_registration_user_id']
    
    context = {
        'teacher': teacher_profile
    }
    return render(request, 'logic/teacher_register_complete.html', context)


# Дополнительная функция для удаления сертификата
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
            
            # Попытка входа по username или email
            user = authenticate(username=username, password=password)
            
            if user is None:
                # Пробуем найти пользователя по email
                try:
                    user_obj = User.objects.get(email=username)
                    user = authenticate(username=user_obj.username, password=password)
                except User.DoesNotExist:
                    pass
            
            if user is not None:
                login(request, user)
                
                # Настройка сессии
                if not remember_me:
                    request.session.set_expiry(0)
                
                messages.success(request, f'Добро пожаловать, {user.get_full_name()}!')
                
                # Перенаправление
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
            
            # Автоматический вход после регистрации
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


# ===== ПРОФИЛЬ =====
from .models import TeacherProfile, StudentProfile
@login_required
def profile_view(request):
    """Просмотр профиля"""
    if request.user.user_type == 'teacher':
        try:
            teacher_profile = request.user.teacher_profile
            return render(request, 'logic/teacher_profile.html', {
                'teacher': teacher_profile
            })
        except TeacherProfile.DoesNotExist:
            messages.warning(request, 'Завершите регистрацию учителя')
            return redirect('teacher_register_step1')
    else:
        try:
            student_profile = request.user.student_profile
            return render(request, 'logic/student_profile.html', {
                'student': student_profile
            })
        except StudentProfile.DoesNotExist:
            # Создаем профиль если не существует
            StudentProfile.objects.create(user=request.user)
            return redirect('profile')


@login_required
def profile_edit(request):
    """Редактирование профиля"""
    if request.user.user_type == 'teacher':
        messages.info(request, 'Редактирование профиля учителя будет доступно позже')
        return redirect('profile')
    else:
        messages.info(request, 'Редактирование профиля ученика будет доступно позже')
        return redirect('profile')