from datetime import UTC, date, datetime, timedelta

import pytest
from django.contrib.gis.geos import Point

from apps.events.models import Event, Registration, RegistrationSnapshot
from apps.reporting.geo import MIN_CELL_COUNT, attendee_dispersion
from apps.reporting.pace import cumulative_at, registration_curve, series_pace


def _reg(event, days_before, **kw):
    when = datetime.combine(event.start_date - timedelta(days=days_before), datetime.min.time(), tzinfo=UTC)
    return Registration.objects.create(event=event, registered_at=when.replace(hour=15), **kw)


@pytest.mark.django_db
def test_curve_from_registrations_excludes_cancellations(world):
    _reg(world.event, 30)
    _reg(world.event, 30)
    _reg(world.event, 10)
    _reg(world.event, 5, canceled_at=datetime.now(UTC))
    assert registration_curve(world.event) == [(30, 2), (10, 3)]
    assert cumulative_at(registration_curve(world.event), 20) == 2


@pytest.mark.django_db
def test_series_pace_uses_snapshots_for_history(world):
    prior = Event.objects.create(
        organization=world.org,
        series=world.series,
        name="NICAR 2026",
        slug="nicar-2026",
        start_date=date(2026, 3, 5),
        end_date=date(2026, 3, 8),
        timezone="America/New_York",
    )
    RegistrationSnapshot.objects.create(event=prior, recorded_on=date(2026, 2, 3), total_registrations=500)
    RegistrationSnapshot.objects.create(event=prior, recorded_on=date(2026, 3, 4), total_registrations=1100)
    _reg(world.event, 40)
    data = series_pace(world.series)
    assert [s["label"] for s in data["series"]] == ["2026", "2027"]
    assert data["today"]["rows"][1]["final"] == 1100


@pytest.mark.django_db
def test_attendee_dispersion_suppresses_small_cells(world):
    for _ in range(MIN_CELL_COUNT):
        _reg(world.event, 10, location=Point(-104.99, 39.74, srid=4326))
    _reg(world.event, 10, location=Point(-122.4, 37.7, srid=4326))  # lone attendee: hidden
    features = attendee_dispersion(Event.objects.filter(pk=world.event.pk))["features"]
    assert len(features) == 1 and features[0]["properties"]["count"] == MIN_CELL_COUNT


@pytest.mark.django_db
def test_report_api_scoped(world):
    from rest_framework.test import APIClient

    c = APIClient()
    c.force_authenticate(world.outsider)
    assert c.get(f"/api/v1/reports/series-pace/?series={world.series.pk}").status_code == 404
    c.force_authenticate(world.staff_user)
    assert c.get(f"/api/v1/reports/series-pace/?series={world.series.pk}").status_code == 200
