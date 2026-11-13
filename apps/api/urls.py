"""REST API v1 routes (mounted at /api/v1/)."""

from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("organizations", views.OrganizationViewSet, basename="organization")
router.register("calendar", views.CalendarViewSet, basename="calendar")
router.register("venues", views.VenueViewSet)
router.register("rooms", views.RoomViewSet)
router.register("floorplans", views.FloorplanViewSet)
router.register("series", views.EventSeriesViewSet)
router.register("events", views.EventViewSet)
router.register("event-rooms", views.EventRoomViewSet)
router.register("rfps", views.VenueRFPViewSet)
router.register("concessions", views.ConcessionViewSet)
router.register("tracks", views.TrackViewSet)
router.register("time-slots", views.TimeSlotViewSet)
router.register("sessions", views.SessionViewSet)
router.register("session-speakers", views.SessionSpeakerViewSet)
router.register("logistics", views.LogisticsItemViewSet)
router.register("speakers", views.SpeakerViewSet)
router.register("my-pitches", views.MyPitchViewSet, basename="my-pitch")
router.register("pitch-reviews", views.PitchTargetViewSet)
router.register("companies", views.CompanyViewSet)
router.register("sponsorships", views.SponsorshipViewSet)
router.register("exhibitors", views.ExhibitorBookingViewSet)
router.register("publish-runs", views.PublishRunViewSet, basename="publish-run")
router.register("reports", views.ReportViewSet, basename="report")
router.register("ask", views.AskViewSet, basename="ask")
router.register("public/events", views.PublicEventViewSet, basename="public-event")

urlpatterns = router.urls
