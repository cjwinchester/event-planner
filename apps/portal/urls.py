from django.urls import path

from . import views

app_name = "portal"
urlpatterns = [
    path("", views.portal_home, name="home"),
    path("profile/", views.profile, name="profile"),
    path("sharing/", views.sharing_view, name="sharing"),
    path("sharing/<int:org_id>/revoke/", views.sharing_revoke, name="sharing-revoke"),
]
