from django.urls import path

from . import views

app_name = "feeds"
urlpatterns = [
    path("<slug:org_slug>/<slug:event_slug>/bundle.json", views.bundle_json, name="bundle"),
    path("<slug:org_slug>/<slug:event_slug>/schedule.<str:fmt>", views.schedule_format, name="schedule"),
    path("<slug:org_slug>/<slug:event_slug>/tracks/<slug:track_slug>.ics", views.track_ics, name="track-ics"),
    path("<slug:org_slug>/<slug:event_slug>/sessions/<int:session_id>.ics", views.session_ics, name="session-ics"),
]
