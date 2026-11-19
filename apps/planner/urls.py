from django.urls import path

from . import event_views, pass_views, pitch_views, room_views, views

app_name = "planner"
urlpatterns = [
    path("", views.event_list, name="event-list"),
    path("new/", event_views.event_new, name="event-new"),
    path("sessions/<int:pk>/panel/", views.session_panel, name="session-panel"),
    path("sessions/<int:pk>/place/", views.session_place, name="session-place"),
    path("sessions/<int:pk>/speakers/add/", views.session_speaker_add, name="session-speaker-add"),
    path(
        "sessions/<int:pk>/speakers/<int:row_pk>/remove/", views.session_speaker_remove, name="session-speaker-remove"
    ),
    path("sessions/<int:pk>/speakers/search/", views.speaker_search, name="speaker-search"),
    path("<slug:org_slug>/<slug:event_slug>/grid/", views.grid, name="grid"),
    path("<slug:org_slug>/<slug:event_slug>/pitches/", pitch_views.pitches, name="pitches"),
    path("<slug:org_slug>/<slug:event_slug>/pitches/bulk/", pitch_views.pitch_bulk, name="pitch-bulk"),
    path("<slug:org_slug>/<slug:event_slug>/pitches/finalize/", pitch_views.pitch_finalize, name="pitch-finalize"),
    path("<slug:org_slug>/<slug:event_slug>/pitches/new/", pitch_views.pitch_new, name="pitch-new"),
    path(
        "<slug:org_slug>/<slug:event_slug>/pitches/speaker-search/",
        pitch_views.pitch_speaker_search,
        name="pitch-speaker-search",
    ),
    path("pitches/<int:pk>/vote/", pitch_views.pitch_vote, name="pitch-vote"),
    path("pitches/<int:pk>/panel/", pitch_views.pitch_panel, name="pitch-panel"),
    path("<slug:org_slug>/<slug:event_slug>/sessions/new/", views.session_new, name="session-new"),
    path("<slug:org_slug>/<slug:event_slug>/rooms/", room_views.rooms, name="rooms"),
    path("<slug:org_slug>/<slug:event_slug>/rooms/new/", room_views.room_new, name="room-new"),
    path("<slug:org_slug>/<slug:event_slug>/rooms/fill/", room_views.fill, name="rooms-fill"),
    path("<slug:org_slug>/<slug:event_slug>/rooms/unfill/", room_views.unfill, name="rooms-unfill"),
    path("<slug:org_slug>/<slug:event_slug>/rooms/<int:pk>/", room_views.room_edit, name="room-edit"),
    path("<slug:org_slug>/<slug:event_slug>/rooms/<int:pk>/move/", room_views.room_move, name="room-move"),
    path("<slug:org_slug>/<slug:event_slug>/rooms/<int:pk>/remove/", room_views.room_remove, name="room-remove"),
    path("<slug:org_slug>/<slug:event_slug>/links/", pass_views.passes, name="passes"),
    path("<slug:org_slug>/<slug:event_slug>/links/issue/", pass_views.passes_issue, name="passes-issue"),
    path("<slug:org_slug>/<slug:event_slug>/links/links.csv", pass_views.passes_csv, name="passes-csv"),
    path("<slug:org_slug>/<slug:event_slug>/links/<int:pk>/", pass_views.pass_action, name="pass-action"),
    path("<slug:org_slug>/<slug:event_slug>/run-of-show/", pass_views.run_of_show, name="run-of-show"),
    path("<slug:org_slug>/<slug:event_slug>/run-of-show/zoom/", pass_views.zoom_action, name="zoom-action"),
    path("<slug:org_slug>/<slug:event_slug>/slots/", views.slots, name="slots"),
    path("<slug:org_slug>/<slug:event_slug>/slots/copy/", views.slots_copy, name="slots-copy"),
    path("<slug:org_slug>/<slug:event_slug>/slots/<int:slot_pk>/", views.slot_edit, name="slot-edit"),
    path("<slug:org_slug>/<slug:event_slug>/slots/<int:slot_pk>/delete/", views.slot_delete, name="slot-delete"),
]
