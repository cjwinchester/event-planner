from django.urls import path

from . import views

app_name = "planner"
urlpatterns = [
    path("", views.event_list, name="event-list"),
    path("sessions/<int:pk>/panel/", views.session_panel, name="session-panel"),
    path("sessions/<int:pk>/place/", views.session_place, name="session-place"),
    path("sessions/<int:pk>/speakers/add/", views.session_speaker_add, name="session-speaker-add"),
    path(
        "sessions/<int:pk>/speakers/<int:row_pk>/remove/", views.session_speaker_remove, name="session-speaker-remove"
    ),
    path("sessions/<int:pk>/speakers/search/", views.speaker_search, name="speaker-search"),
    path("<slug:org_slug>/<slug:event_slug>/grid/", views.grid, name="grid"),
    path("<slug:org_slug>/<slug:event_slug>/sessions/new/", views.session_new, name="session-new"),
    path("<slug:org_slug>/<slug:event_slug>/slots/", views.slots, name="slots"),
    path("<slug:org_slug>/<slug:event_slug>/slots/copy/", views.slots_copy, name="slots-copy"),
    path("<slug:org_slug>/<slug:event_slug>/slots/<int:slot_pk>/", views.slot_edit, name="slot-edit"),
    path("<slug:org_slug>/<slug:event_slug>/slots/<int:slot_pk>/delete/", views.slot_delete, name="slot-delete"),
]
