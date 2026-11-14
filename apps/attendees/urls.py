from django.urls import path

from . import views

app_name = "attendees"
urlpatterns = [
    path("<str:token>/", views.home, name="home"),
    path("<str:token>/calendar.ics", views.calendar, name="calendar"),
    path("<str:token>/join/<int:public_id>/", views.join, name="join"),
]
