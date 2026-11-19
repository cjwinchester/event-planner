from django.urls import path

from . import company_views, views

app_name = "portal"
urlpatterns = [
    path("", views.portal_home, name="home"),
    path("profile/", views.profile, name="profile"),
    path("sharing/", views.sharing_view, name="sharing"),
    path("sharing/<int:org_id>/revoke/", views.sharing_revoke, name="sharing-revoke"),
    path("sessions/", views.sessions, name="sessions"),
    path("sessions/<int:pk>/", views.session_detail, name="session"),
    path("sessions/<int:pk>/withdraw/", views.session_withdraw, name="session-withdraw"),
    path("events/<int:event_id>/info/", views.event_info, name="event-info"),
    path("calls/", views.calls, name="calls"),
    path("pitches/", views.pitches, name="pitches"),
    path("pitches/new/", views.pitch_new, name="pitch-new"),
    path("pitches/<int:pk>/", views.pitch_detail, name="pitch"),
    path("pitches/<int:pk>/edit/", views.pitch_edit, name="pitch-edit"),
    path("companies/<slug:slug>/", company_views.company, name="company"),
    path("companies/<slug:slug>/edit/", company_views.company_edit, name="company-edit"),
    path("companies/<slug:slug>/contacts/", company_views.company_contacts, name="company-contacts"),
    path(
        "companies/<slug:slug>/contacts/<int:pk>/",
        company_views.company_contact_update,
        name="company-contact-update",
    ),
]
