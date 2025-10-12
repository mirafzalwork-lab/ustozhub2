from django.urls import path
from .views import (
    home, detail, student_detail, teacher_register_step1, teacher_register_step2, 
    teacher_register_step3, teacher_register_complete, profile_view, profile_edit, 
    login_view, students_list, logout_view, register_choose, register_student,
    teacher_profile_edit, student_profile_edit, toggle_profile_status
)

urlpatterns = [
    path('', home, name='home'),

    path('login/', login_view, name='login'),
    path('logout/', logout_view, name='logout'),
    path('register/choose/', register_choose, name='register_choose'),
    path('register/student/', register_student, name='register_student'),
    path('students/', students_list, name='students_list'),
    path('student/<int:id>/', student_detail, name='student_detail'),

    path('register/', teacher_register_step1, name='teacher_register_step1'),
    path('register/step2/', teacher_register_step2, name='teacher_register_step2'),
    path('register/step3/', teacher_register_step3, name='teacher_register_step3'),
    path('register/complete/', teacher_register_complete, name='teacher_register_complete'),
    path('teacher/<int:id>/', detail, name='teacher_detail'),
    path('profile/', profile_view, name='profile'),
    path('profile/edit/', profile_edit, name='profile_edit'),
    path('profile/edit/teacher/', teacher_profile_edit, name='teacher_profile_edit'),
    path('profile/edit/student/', student_profile_edit, name='student_profile_edit'),
    path('profile/toggle-status/', toggle_profile_status, name='toggle_profile_status'),
]
