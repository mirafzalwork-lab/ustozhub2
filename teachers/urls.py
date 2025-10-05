from django.urls import path
from .views import home, detail, teacher_register_step1, teacher_register_step2, teacher_register_step3, teacher_register_complete, profile_view, profile_edit, login_view, logout_view, register_choose, register_student

urlpatterns = [
    path('', home, name='home'),

    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('register/choose/', register_choose, name='register_choose'),
    path('register/student/', register_student, name='register_student'),
    # path('register/teacher/', register_teacher, name='register_teacher'),

    # path('register/', views.register_view, name='register')

    path('register/', teacher_register_step1, name='teacher_register_step1'),
    path('register/step2/', teacher_register_step2, name='teacher_register_step2'),
    path('register/step3/', teacher_register_step3, name='teacher_register_step3'),
    path('register/complete/', teacher_register_complete, name='teacher_register_complete'),
    path('teacher/<int:id>/', detail, name='teacher_detail'),
    path('profile/', profile_view, name='profile'),
    path('profile/edit/', profile_edit, name='profile_edit'),
]
