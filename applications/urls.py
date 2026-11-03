from django.urls import path

from . import views

app_name = "applications"

urlpatterns = [
    path("new/", views.edit_application, name="new"),
    path("<int:pk>/", views.detail, name="detail"),
    path("<int:pk>/edit/", views.edit_application, name="edit"),
    path("<int:pk>/delete/", views.delete, name="delete"),
    path("<int:pk>/withdraw/", views.withdraw, name="withdraw"),
]
