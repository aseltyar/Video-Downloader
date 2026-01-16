from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('progress/<str:progress_id>/', views.get_progress, name='progress'),
    path('download/<str:progress_id>/', views.download_file, name='download_file'),
]