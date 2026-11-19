from django.urls import path

from . import views

app_name = "hotel"
urlpatterns = [path("<str:token>/", views.home, name="home")]
