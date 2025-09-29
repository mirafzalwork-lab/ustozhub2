from django.shortcuts import render
from django.db.models import Q, Min, Max
from .models import TeacherProfile, Subject, City

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

# Главная страница и регистрация

# class HomeView(TemplateView):
#     """Главная страница"""
#     template_name = 'teacherhub/home.html'
    
#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         context.update({
#             'featured_teachers': TeacherProfile.objects.filter(
#                 is_featured=True, 
#                 is_active=True
#             ).select_related('user', 'city').prefetch_related(
#                 'subjects', 'reviews'
#             )[:6],
#             'subjects': Subject.objects.filter(is_active=True)[:12],
#             'total_teachers': TeacherProfile.objects.filter(is_active=True).count(),
#             'total_students': User.objects.filter(user_type='student').count(),
#             'average_rating': TeacherProfile.objects.filter(
#                 is_active=True
#             ).aggregate(avg_rating=Avg('rating'))['avg_rating'] or 0,
#         })
#         return context

# def register_view(request):
#     """Регистрация пользователя"""
#     if request.method == 'POST':
#         form = CustomUserCreationForm(request.POST)
#         if form.is_valid():
#             with transaction.atomic():
#                 user = form.save()
                
#                 # Создаем соответствующий профиль
#                 if user.user_type == 'teacher':
#                     TeacherProfile.objects.create(
#                         user=user,
#                         bio="Расскажите о себе и своем опыте преподавания...",
#                         education_level='bachelor',
#                         experience_years=0
#                     )
#                 else:
#                     StudentProfile.objects.create(user=user)
                
#                 # Авторизуем пользователя
#                 username = form.cleaned_data.get('username')
#                 raw_password = form.cleaned_data.get('password1')
#                 user = authenticate(username=username, password=raw_password)
#                 login(request, user)
                
#                 messages.success(request, 'Регистрация прошла успешно!')
                
#                 # Перенаправляем на страницу заполнения профиля
#                 if user.user_type == 'teacher':
#                     return redirect('teacher_profile_edit')
#                 else:
#                     return redirect('student_profile_edit')
                    
#     else:
#         form = CustomUserCreationForm()
    
#     return render(request, 'registration/register.html', {'form': form})

# # Профили пользователей

# @login_required
# def profile_view(request):
#     """Просмотр собственного профиля"""
#     if request.user.user_type == 'teacher':
#         return redirect('teacher_profile_view')
#     else:
#         return redirect('student_profile_view')

# @login_required
# def teacher_profile_view(request):
#     """Просмотр профиля учителя"""
#     try:
#         teacher_profile = request.user.teacher_profile
#     except TeacherProfile.DoesNotExist:
#         messages.warning(request, 'Пожалуйста, заполните ваш профиль учителя.')
#         return redirect('teacher_profile_edit')
    
#     context = {
#         'teacher': teacher_profile,
#         'reviews': teacher_profile.reviews.select_related('student').order_by('-created_at')[:5],
#         'subjects': teacher_profile.teachersubject_set.select_related('subject').all(),
#         'recent_messages': Message.objects.filter(
#             conversation__teacher=teacher_profile
#         ).select_related('sender', 'conversation').order_by('-created_at')[:5]
#     }
#     return render(request, 'teacherhub/teacher_profile.html', context)

# @login_required
# def teacher_profile_edit(request):
#     """Редактирование профиля учителя"""
#     try:
#         teacher_profile = request.user.teacher_profile
#     except TeacherProfile.DoesNotExist:
#         teacher_profile = TeacherProfile.objects.create(
#             user=request.user,
#             bio="Расскажите о себе и своем опыте преподавания...",
#             education_level='bachelor',
#             experience_years=0
#         )
    
#     if request.method == 'POST':
#         user_form = UserProfileForm(request.POST, request.FILES, instance=request.user)
#         teacher_form = TeacherProfileForm(request.POST, instance=teacher_profile)
#         subject_formset = TeacherSubjectFormSet(
#             request.POST, 
#             instance=teacher_profile,
#             queryset=teacher_profile.teachersubject_set.all()
#         )
        
#         if user_form.is_valid() and teacher_form.is_valid() and subject_formset.is_valid():
#             with transaction.atomic():
#                 user_form.save()
#                 teacher_form.save()
#                 subject_formset.save()
                
#             messages.success(request, 'Профиль успешно обновлен!')
#             return redirect('teacher_profile_view')
#     else:
#         user_form = UserProfileForm(instance=request.user)
#         teacher_form = TeacherProfileForm(instance=teacher_profile)
#         subject_formset = TeacherSubjectFormSet(
#             instance=teacher_profile,
#             queryset=teacher_profile.teachersubject_set.all()
#         )
    
#     context = {
#         'user_form': user_form,
#         'teacher_form': teacher_form,
#         'subject_formset': subject_formset,
#         'teacher': teacher_profile,
#     }
#     return render(request, 'teacherhub/teacher_profile_edit.html', context)

# @login_required
# def student_profile_view(request):
#     """Просмотр профиля ученика"""
#     try:
#         student_profile = request.user.student_profile
#     except StudentProfile.DoesNotExist:
#         messages.warning(request, 'Пожалуйста, заполните ваш профиль.')
#         return redirect('student_profile_edit')
    
#     context = {
#         'student': student_profile,
#         'favorites': Favorite.objects.filter(
#             student=request.user
#         ).select_related('teacher__user').order_by('-created_at')[:5],
#         'recent_conversations': Conversation.objects.filter(
#             student=request.user
#         ).select_related('teacher__user').order_by('-updated_at')[:5],
#         'given_reviews': Review.objects.filter(
#             student=request.user
#         ).select_related('teacher__user').order_by('-created_at')[:5]
#     }
#     return render(request, 'teacherhub/student_profile.html', context)

# @login_required
# def student_profile_edit(request):
#     """Редактирование профиля ученика"""
#     try:
#         student_profile = request.user.student_profile
#     except StudentProfile.DoesNotExist:
#         student_profile = StudentProfile.objects.create(user=request.user)
    
#     if request.method == 'POST':
#         user_form = UserProfileForm(request.POST, request.FILES, instance=request.user)
#         student_form = StudentProfileForm(request.POST, instance=student_profile)
        
#         if user_form.is_valid() and student_form.is_valid():
#             with transaction.atomic():
#                 user_form.save()
#                 student_form.save()
                
#             messages.success(request, 'Профиль успешно обновлен!')
#             return redirect('student_profile_view')
#     else:
#         user_form = UserProfileForm(instance=request.user)
#         student_form = StudentProfileForm(instance=student_profile)
    
#     context = {
#         'user_form': user_form,
#         'student_form': student_form,
#         'student': student_profile,
#     }
#     return render(request, 'teacherhub/student_profile_edit.html', context)

# # Поиск и каталог учителей

# class TeacherListView(ListView):
#     """Каталог учителей с поиском и фильтрами"""
#     model = TeacherProfile
#     template_name = 'teacherhub/teacher_list.html'
#     context_object_name = 'teachers'
#     paginate_by = 12
    
#     def get_queryset(self):
#         queryset = TeacherProfile.objects.filter(is_active=True).select_related(
#             'user', 'city'
#         ).prefetch_related('subjects', 'teachersubject_set__subject')
        
#         form = TeacherSearchForm(self.request.GET)
#         if form.is_valid():
#             # Поиск по запросу
#             query = form.cleaned_data.get('query')
#             if query:
#                 queryset = queryset.filter(
#                     Q(user__first_name__icontains=query) |
#                     Q(user__last_name__icontains=query) |
#                     Q(bio__icontains=query) |
#                     Q(subjects__name__icontains=query)
#                 ).distinct()
            
#             # Фильтр по предмету
#             subject = form.cleaned_data.get('subject')
#             if subject:
#                 queryset = queryset.filter(subjects=subject)
            
#             # Фильтр по городу
#             city = form.cleaned_data.get('city')
#             if city:
#                 queryset = queryset.filter(city=city)
            
#             # Фильтр по формату обучения
#             teaching_format = form.cleaned_data.get('teaching_format')
#             if teaching_format:
#                 queryset = queryset.filter(teaching_format=teaching_format)
            
#             # Фильтр по цене
#             price_range = form.cleaned_data.get('price_range')
#             if price_range:
#                 if price_range == '500000+':
#                     queryset = queryset.filter(
#                         teachersubject__hourly_rate__gte=500000
#                     )
#                 elif '-' in price_range:
#                     min_price, max_price = map(int, price_range.split('-'))
#                     queryset = queryset.filter(
#                         teachersubject__hourly_rate__gte=min_price,
#                         teachersubject__hourly_rate__lte=max_price
#                     )
            
#             # Фильтр по бесплатному пробному занятию
#             if form.cleaned_data.get('has_free_trial'):
#                 queryset = queryset.filter(teachersubject__is_free_trial=True)
            
#             # Фильтр по рейтингу
#             min_rating = form.cleaned_data.get('min_rating')
#             if min_rating:
#                 queryset = queryset.filter(rating__gte=min_rating)
            
#             # Сортировка
#             sort_by = form.cleaned_data.get('sort_by', '-rating')
#             if sort_by in ['hourly_rate', '-hourly_rate']:
#                 queryset = queryset.annotate(
#                     min_price=Min('teachersubject__hourly_rate')
#                 ).order_by('min_price' if sort_by == 'hourly_rate' else '-min_price')
#             else:
#                 queryset = queryset.order_by(sort_by)
        
#         return queryset.distinct()
    
#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         context['form'] = TeacherSearchForm(self.request.GET)
#         context['subjects'] = Subject.objects.filter(is_active=True)
#         context['cities'] = City.objects.filter(is_active=True)
#         return context

# class TeacherDetailView(DetailView):
#     """Детальная страница учителя"""
#     model = TeacherProfile
#     template_name = 'teacherhub/teacher_detail.html'
#     context_object_name = 'teacher'
    
#     def get_queryset(self):
#         return TeacherProfile.objects.filter(is_active=True).select_related(
#             'user', 'city'
#         ).prefetch_related(
#             'subjects', 'teachersubject_set__subject', 'certificates',
#             'reviews__student'
#         )
    
#     def get_context_data(self, **kwargs):
#         context = super().get_context_data(**kwargs)
#         teacher = self.get_object()
        
#         context.update({
#             'subjects': teacher.teachersubject_set.select_related('subject').all(),
#             'reviews': teacher.reviews.select_related('student', 'subject').order_by('-created_at'),
#             'certificates': teacher.certificates.all(),
#             'is_favorite': False,
#             'can_contact': True,
#             'contact_form': ContactTeacherForm(),
#         })
        
#         if self.request.user.is_authenticated:
#             if self.request.user.user_type == 'student':
#                 context['is_favorite'] = Favorite.objects.filter(
#                     student=self.request.user,
#                     teacher=teacher
#                 ).exists()
                
#                 # Проверяем, есть ли уже переписка
#                 context['existing_conversation'] = Conversation.objects.filter(
#                     student=self.request.user,
#                     teacher=teacher
#                 ).first()
#             else:
#                 context['can_contact'] = False
        
#         return context

# # Сообщения и переписки

# @login_required
# def conversations_list(request):
#     """Список переписок"""
#     if request.user.user_type == 'teacher':
#         conversations = Conversation.objects.filter(
#             teacher__user=request.user
#         ).select_related('student', 'subject').order_by('-updated_at')
#     else:
#         conversations = Conversation.objects.filter(
#             student=request.user
#         ).select_related('teacher__user', 'subject').order_by('-updated_at')
    
#     # Пагинация
#     paginator = Paginator(conversations, 10)
#     page_number = request.GET.get('page')
#     conversations = paginator.get_page(page_number)
    
#     context = {
#         'conversations': conversations,
#         'user_type': request.user.user_type,
#     }
#     return render(request, 'teacherhub/conversations_list.html', context)

# @login_required
# def conversation_detail(request, conversation_id):
#     """Детальная страница переписки"""
#     conversation = get_object_or_404(Conversation, id=conversation_id)
    
#     # Проверяем права доступа
#     if request.user.user_type == 'teacher':
#         if conversation.teacher.user != request.user:
#             raise Http404
#     else:
#         if conversation.student != request.user:
#             raise Http404
    
#     # Отмечаем сообщения как прочитанные
#     Message.objects.filter(
#         conversation=conversation,
#         is_read=False
#     ).exclude(sender=request.user).update(is_read=True)
    
#     messages_list = conversation.messages.select_related('sender').order_by('created_at')
    
#     if request.method == 'POST':
#         form = MessageForm(request.POST)
#         if form.is_valid():
#             message = form.save(commit=False)
#             message.conversation = conversation
#             message.sender = request.user
#             message.save()
            
#             # Обновляем время последнего сообщения в переписке
#             conversation.updated_at = message.created_at
#             conversation.save()
            
#             messages.success(request, 'Сообщение отправлено!')
#             return redirect('conversation_detail', conversation_id=conversation.id)
#     else:
#         form = MessageForm()
    
#     context = {
#         'conversation': conversation,
#         'messages': messages_list,
#         'form': form,
#     }
#     return render(request, 'teacherhub/conversation_detail.html', context)

# @login_required
# def contact_teacher(request, teacher_id):
#     """Связаться с учителем"""
#     if request.user.user_type != 'student':
#         messages.error(request, 'Только ученики могут связываться с учителями.')
#         return redirect('teacher_list')
    
#     teacher = get_object_or_404(TeacherProfile, id=teacher_id, is_active=True)
    
#     # Проверяем, есть ли уже переписка
#     conversation, created = Conversation.objects.get_or_create(
#         teacher=teacher,
#         student=request.user,
#         defaults={'is_active': True}
#     )
    
#     if request.method == 'POST':
#         form = ContactTeacherForm(request.POST)
#         if form.is_valid():
#             # Создаем первое сообщение
#             message_content = f"""
# Предмет: {form.cleaned_data['subject']}
# Формат: {form.cleaned_data.get('preferred_format', 'Не указан')}
# Время: {form.cleaned_data.get('available_time', 'Не указано')}

# {form.cleaned_data['message']}
#             """.strip()
            
#             Message.objects.create(
#                 conversation=conversation,
#                 sender=request.user,
#                 content=message_content
#             )
            
#             # Обновляем предмет переписки
#             conversation.subject = form.cleaned_data['subject']
#             conversation.save()
            
#             messages.success(request, 'Сообщение отправлено учителю!')
#             return redirect('conversation_detail', conversation_id=conversation.id)
#     else:
#         form = ContactTeacherForm()
    
#     context = {
#         'teacher': teacher,
#         'form': form,
#     }
#     return render(request, 'teacherhub/contact_teacher.html', context)

# # Избранное

# @login_required
# def toggle_favorite(request, teacher_id):
#     """Добавить/удалить учителя из избранного"""
#     if request.user.user_type != 'student':
#         return JsonResponse({'error': 'Только ученики могут добавлять в избранное'}, status=403)
    
#     teacher = get_object_or_404(TeacherProfile, id=teacher_id)
#     favorite, created = Favorite.objects.get_or_create(
#         student=request.user,
#         teacher=teacher
#     )
    
#     if not created:
#         favorite.delete()
#         is_favorite = False
#     else:
#         is_favorite = True
    
#     return JsonResponse({
#         'is_favorite': is_favorite,
#         'message': 'Добавлено в избранное' if is_favorite else 'Удалено из избранного'
#     })

# @login_required
# def favorites_list(request):
#     """Список избранных учителей"""
#     if request.user.user_type != 'student':
#         messages.error(request, 'Доступно только ученикам.')
#         return redirect('home')
    
#     favorites = Favorite.objects.filter(
#         student=request.user
#     ).select_related('teacher__user', 'teacher__city').order_by('-created_at')
    
#     paginator = Paginator(favorites, 12)
#     page_number = request.GET.get('page')
#     favorites = paginator.get_page(page_number)
    
#     return render(request, 'teacherhub/favorites_list.html', {'favorites': favorites})

# # Отзывы

# @login_required
# def write_review(request, teacher_id):
#     """Написать отзыв учителю"""
#     if request.user.user_type != 'student':
#         messages.error(request, 'Только ученики могут оставлять отзывы.')
#         return redirect('teacher_detail', pk=teacher_id)
    
#     teacher = get_object_or_404(TeacherProfile, id=teacher_id)
    
#     # Проверяем, что между учеником и учителем была переписка
#     if not Conversation.objects.filter(student=request.user, teacher=teacher).exists():
#         messages.error(request, 'Вы можете оставить отзыв только после общения с учителем.')
#         return redirect('teacher_detail', pk=teacher_id)
    
#     if request.method == 'POST':
#         form = ReviewForm(request.POST)
#         if form.is_valid():
#             # Проверяем, что отзыв еще не оставлен
#             existing_review = Review.objects.filter(
#                 teacher=teacher,
#                 student=request.user,
#                 subject=form.cleaned_data['subject']
#             ).first()
            
#             if existing_review:
#                 messages.error(request, 'Вы уже оставили отзыв для этого учителя по данному предмету.')
#                 return redirect('teacher_detail', pk=teacher_id)
            
#             with transaction.atomic():
#                 review = form.save(commit=False)
#                 review.teacher = teacher
#                 review.student = request.user
#                 review.save()
                
#                 # Обновляем рейтинг учителя
#                 avg_rating = teacher.reviews.aggregate(
#                     avg_rating=Avg('rating')
#                 )['avg_rating']
#                 teacher.rating = round(avg_rating, 2) if avg_rating else 0
#                 teacher.total_reviews = teacher.reviews.count()
#                 teacher.save()
            
#             messages.success(request, 'Отзыв успешно добавлен!')
#             return redirect('teacher_detail', pk=teacher_id)
#     else:
#         # Предлагаем предметы, по которым велась переписка
#         conversation_subjects = Conversation.objects.filter(
#             student=request.user,
#             teacher=teacher,
#             subject__isnull=False
#         ).values_list('subject', flat=True).distinct()
        
#         form = ReviewForm()
#         # Ограничиваем выбор предметов теми, по которым было общение
#         if conversation_subjects:
#             form.fields['subject'].queryset = Subject.objects.filter(
#                 id__in=conversation_subjects
#             )
    
#     context = {
#         'teacher': teacher,
#         'form': form,
#     }
#     return render(request, 'teacherhub/write_review.html', context)

# # API представления для AJAX запросов

# @csrf_exempt
# @login_required
# def api_send_message(request):
#     """API для отправки сообщений через AJAX"""
#     if request.method == 'POST':
#         try:
#             data = json.loads(request.body)
#             conversation_id = data.get('conversation_id')
#             content = data.get('content', '').strip()
            
#             if not content:
#                 return JsonResponse({'error': 'Сообщение не может быть пустым'}, status=400)
            
#             conversation = get_object_or_404(Conversation, id=conversation_id)
            
#             # Проверяем права доступа
#             if request.user.user_type == 'teacher':
#                 if conversation.teacher.user != request.user:
#                     return JsonResponse({'error': 'Доступ запрещен'}, status=403)
#             else:
#                 if conversation.student != request.user:
#                     return JsonResponse({'error': 'Доступ запрещен'}, status=403)
            
#             message = Message.objects.create(
#                 conversation=conversation,
#                 sender=request.user,
#                 content=content
#             )
            
#             conversation.updated_at = message.created_at
#             conversation.save()
            
#             return JsonResponse({
#                 'success': True,
#                 'message_id': message.id,
#                 'content': message.content,
#                 'sender_name': message.sender.get_full_name(),
#                 'created_at': message.created_at.strftime('%d.%m.%Y %H:%M')
#             })
            
#         except json.JSONDecodeError:
#             return JsonResponse({'error': 'Неверный формат данных'}, status=400)
#         except Exception as e:
#             return JsonResponse({'error': str(e)}, status=500)
    
#     return JsonResponse({'error': 'Метод не поддерживается'}, status=405)

# @login_required
# def api_get_messages(request, conversation_id):
#     """API для получения сообщений переписки"""
#     conversation = get_object_or_404(Conversation, id=conversation_id)
    
#     # Проверяем права доступа
#     if request.user.user_type == 'teacher':
#         if conversation.teacher.user != request.user:
#             return JsonResponse({'error': 'Доступ запрещен'}, status=403)
#     else:
#         if conversation.student != request.user:
#             return JsonResponse({'error': 'Доступ запрещен'}, status=403)
    
#     messages_data = []
#     for message in conversation.messages.select_related('sender').order_by('created_at'):
#         messages_data.append({
#             'id': message.id,
#             'content': message.content,
#             'sender_name': message.sender.get_full_name(),
#             'sender_id': message.sender.id,
#             'is_read': message.is_read,
#             'created_at': message.created_at.strftime('%d.%m.%Y %H:%M')
#         })
    
#     return JsonResponse({
#         'messages': messages_data,
#         'conversation_id': str(conversation.id)
#     })

# # Статистика и аналитика

# @login_required
# def teacher_dashboard(request):
#     """Панель управления учителя"""
#     if request.user.user_type != 'teacher':
#         messages.error(request, 'Доступно только учителям.')
#         return redirect('home')
    
#     try:
#         teacher = request.user.teacher_profile
#     except TeacherProfile.DoesNotExist:
#         messages.warning(request, 'Пожалуйста, заполните ваш профиль учителя.')
#         return redirect('teacher_profile_edit')
    
#     # Статистика
#     total_conversations = Conversation.objects.filter(teacher=teacher).count()
#     active_conversations = Conversation.objects.filter(
#         teacher=teacher, 
#         is_active=True
#     ).count()
#     total_reviews = teacher.reviews.count()
#     unread_messages = Message.objects.filter(
#         conversation__teacher=teacher,
#         is_read=False
#     ).exclude(sender=request.user).count()
    
#     # Последние активности
#     recent_messages = Message.objects.filter(
#         conversation__teacher=teacher
#     ).select_related('sender', 'conversation').order_by('-created_at')[:5]
    
#     recent_reviews = teacher.reviews.select_related(
#         'student', 'subject'
#     ).order_by('-created_at')[:5]
    
#     # Популярные предметы
#     popular_subjects = teacher.teachersubject_set.annotate(
#         conversation_count=Count('subject__conversation')
#     ).select_related('subject').order_by('-conversation_count')[:5]
    
#     context = {
#         'teacher': teacher,
#         'total_conversations': total_conversations,
#         'active_conversations': active_conversations,
#         'total_reviews': total_reviews,
#         'unread_messages': unread_messages,
#         'recent_messages': recent_messages,
#         'recent_reviews': recent_reviews,
#         'popular_subjects': popular_subjects,
#     }
#     return render(request, 'teacherhub/teacher_dashboard.html', context)

# @login_required
# def student_dashboard(request):
#     """Панель управления ученика"""
#     if request.user.user_type != 'student':
#         messages.error(request, 'Доступно только ученикам.')
#         return redirect('home')
    
#     try:
#         student = request.user.student_profile
#     except StudentProfile.DoesNotExist:
#         messages.warning(request, 'Пожалуйста, заполните ваш профиль.')
#         return redirect('student_profile_edit')
    
#     # Статистика
#     total_conversations = Conversation.objects.filter(student=request.user).count()
#     total_favorites = Favorite.objects.filter(student=request.user).count()
#     total_reviews = Review.objects.filter(student=request.user).count()
#     unread_messages = Message.objects.filter(
#         conversation__student=request.user,
#         is_read=False
#     ).exclude(sender=request.user).count()
    
#     # Последние активности
#     recent_conversations = Conversation.objects.filter(
#         student=request.user
#     ).select_related('teacher__user', 'subject').order_by('-updated_at')[:5]
    
#     recent_favorites = Favorite.objects.filter(
#         student=request.user
#     ).select_related('teacher__user').order_by('-created_at')[:5]
    
#     # Рекомендации учителей на основе интересов
#     recommended_teachers = []
#     if student.interests.exists():
#         recommended_teachers = TeacherProfile.objects.filter(
#             subjects__in=student.interests.all(),
#             is_active=True
#         ).exclude(
#             id__in=Favorite.objects.filter(
#                 student=request.user
#             ).values_list('teacher_id', flat=True)
#         ).distinct().order_by('-rating')[:5]
    
#     context = {
#         'student': student,
#         'total_conversations': total_conversations,
#         'total_favorites': total_favorites,
#         'total_reviews': total_reviews,
#         'unread_messages': unread_messages,
#         'recent_conversations': recent_conversations,
#         'recent_favorites': recent_favorites,
#         'recommended_teachers': recommended_teachers,
#     }
#     return render(request, 'teacherhub/student_dashboard.html', context)

# # Поиск по предметам

# def subjects_list(request):
#     """Список всех предметов"""
#     subjects = Subject.objects.filter(is_active=True).annotate(
#         teacher_count=Count('teacherprofile', distinct=True)
#     ).order_by('name')
    
#     context = {
#         'subjects': subjects,
#     }
#     return render(request, 'teacherhub/subjects_list.html', context)

# def subject_teachers(request, subject_id):
#     """Учителя по конкретному предмету"""
#     subject = get_object_or_404(Subject, id=subject_id, is_active=True)
    
#     teachers = TeacherProfile.objects.filter(
#         subjects=subject,
#         is_active=True
#     ).select_related('user', 'city').order_by('-rating')
    
#     # Применяем дополнительные фильтры из GET параметров
#     form = TeacherSearchForm(request.GET)
#     if form.is_valid():
#         # Фильтр по городу
#         city = form.cleaned_data.get('city')
#         if city:
#             teachers = teachers.filter(city=city)
        
#         # Фильтр по формату обучения
#         teaching_format = form.cleaned_data.get('teaching_format')
#         if teaching_format:
#             teachers = teachers.filter(teaching_format=teaching_format)
        
#         # Фильтр по цене
#         price_range = form.cleaned_data.get('price_range')
#         if price_range:
#             if price_range == '500000+':
#                 teachers = teachers.filter(
#                     teachersubject__subject=subject,
#                     teachersubject__hourly_rate__gte=500000
#                 )
#             elif '-' in price_range:
#                 min_price, max_price = map(int, price_range.split('-'))
#                 teachers = teachers.filter(
#                     teachersubject__subject=subject,
#                     teachersubject__hourly_rate__gte=min_price,
#                     teachersubject__hourly_rate__lte=max_price
#                 )
        
#         # Фильтр по бесплатному пробному занятию
#         if form.cleaned_data.get('has_free_trial'):
#             teachers = teachers.filter(
#                 teachersubject__subject=subject,
#                 teachersubject__is_free_trial=True
#             )
    
#     # Пагинация
#     paginator = Paginator(teachers, 12)
#     page_number = request.GET.get('page')
#     teachers = paginator.get_page(page_number)
    
#     context = {
#         'subject': subject,
#         'teachers': teachers,
#         'form': form,
#         'cities': City.objects.filter(is_active=True),
#     }
#     return render(request, 'teacherhub/subject_teachers.html', context)

# # Обработчики ошибок

# def handler404(request, exception):
#     """Обработчик 404 ошибки"""
#     return render(request, 'errors/404.html', status=404)

# def handler500(request):
#     """Обработчик 500 ошибки"""
#     return render(request, 'errors/500.html', status=500)

# # Дополнительные утилиты

# @login_required
# def delete_conversation(request, conversation_id):
#     """Удаление переписки"""
#     conversation = get_object_or_404(Conversation, id=conversation_id)
    
#     # Проверяем права доступа
#     if request.user.user_type == 'teacher':
#         if conversation.teacher.user != request.user:
#             raise Http404
#     else:
#         if conversation.student != request.user:
#             raise Http404
    
#     if request.method == 'POST':
#         conversation.delete()
#         messages.success(request, 'Переписка удалена.')
#         return redirect('conversations_list')
    
#     return render(request, 'teacherhub/confirm_delete_conversation.html', {
#         'conversation': conversation
#     })

# @login_required
# def mark_conversation_inactive(request, conversation_id):
#     """Архивирование переписки"""
#     conversation = get_object_or_404(Conversation, id=conversation_id)
    
#     # Проверяем права доступа
#     if request.user.user_type == 'teacher':
#         if conversation.teacher.user != request.user:
#             return JsonResponse({'error': 'Доступ запрещен'}, status=403)
#     else:
#         if conversation.student != request.user:
#             return JsonResponse({'error': 'Доступ запрещен'}, status=403)
    
#     conversation.is_active = False
#     conversation.save()
    
#     return JsonResponse({'success': True, 'message': 'Переписка архивирована'})