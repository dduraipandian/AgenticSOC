"""Tests for the Alert model itself: what it accepts, rejects and guarantees.

The EDR mapping (raw detection -> Alert) is tested in test_edr_events.py. Here we only need *a* valid
Alert to start from, so we let the EDR normaliser build one and then change single fields.
"""

import datetime
import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from domain.alerts import Alert, FileAccess, Network
from pipeline.events.edr import EDR

FIXTURE_S6 = Path(__file__).parent / "fixtures" / "s6.json"
ALERT_ID = UUID("019a8b2e-7c41-7d3a-9f10-2b6c8e4d5a01")


@pytest.fixture
def s6_detection():
    return json.loads(FIXTURE_S6.read_text())


def make_edr_alert(raw, **overrides) -> Alert:
    """A valid Alert from the EDR normaliser, with some fields replaced."""
    fields = EDR(ALERT_ID, raw).alert().model_dump()
    fields.update(overrides)
    return Alert(**fields)


# --- valid alerts -------------------------------------------------------------------------------

def test_builds_alert_from_s6_detection(s6_detection):
    alert = make_edr_alert(s6_detection)

    assert alert.source == "sentrix-edr"
    assert alert.vendor_event_id == "det-1"
    assert alert.category == "sensitive_file_read"
    assert alert.technique == "T1003.008"
    assert alert.entities.host == "build-01"
    assert alert.entities.user == "svc-maint"
    assert alert.file.path == "/etc/shadow"
    assert alert.file.mode == "read"
    assert alert.severity == 4
    assert alert.process.parent_cmd == "/bin/bash /opt/maint/nightly.sh"
    assert "/usr/sbin/cron" in [a.cmd for a in alert.process.ancestors]
    assert alert.schema_version == "1"


def test_alert_time_is_converted_to_utc(s6_detection):
    india = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    alert = make_edr_alert(s6_detection, alert_time=datetime.datetime(2026, 11, 12, 13, 0, tzinfo=india))

    assert alert.alert_time == datetime.datetime(2026, 11, 12, 7, 30, tzinfo=datetime.UTC)
    assert alert.alert_time.utcoffset() == datetime.timedelta(0)


def test_technique_and_sections_are_optional(s6_detection):
    # Not every alert has a technique, a process, a file or a network connection.
    alert = make_edr_alert(s6_detection, technique=None, process=None, file=None, network=None)

    assert alert.technique is None
    assert alert.process is None


def test_network_section(s6_detection):
    alert = make_edr_alert(s6_detection, network=Network(dst_ip="203.0.113.50", dst_port=8443))

    assert str(alert.network.dst_ip) == "203.0.113.50"
    assert alert.network.dst_port == 8443


# --- invalid alerts -----------------------------------------------------------------------------


@pytest.mark.parametrize("severity", [0, 6, 75])
def test_rejects_severity_outside_1_to_5(s6_detection, severity):
    with pytest.raises(ValidationError):
        make_edr_alert(s6_detection, severity=severity)


def test_rejects_time_without_timezone(s6_detection):
    with pytest.raises(ValidationError):
        make_edr_alert(s6_detection, alert_time=datetime.datetime(2026, 11, 12, 7, 30))


def test_rejects_unknown_source(s6_detection):
    with pytest.raises(ValidationError):
        make_edr_alert(s6_detection, source="crowdstrike")


def test_rejects_unknown_field(s6_detection):
    with pytest.raises(ValidationError):
        make_edr_alert(s6_detection, priority="high")


def test_rejects_missing_required_field(s6_detection):
    alert = make_edr_alert(s6_detection)
    fields = alert.model_dump()
    del fields["vendor_event_id"]

    with pytest.raises(ValidationError):
        Alert(**fields)


def test_rejects_unknown_file_mode():
    with pytest.raises(ValidationError):
        FileAccess(path="/etc/shadow", mode="execute")


def test_rejects_invalid_ip_and_port():
    with pytest.raises(ValidationError):
        Network(dst_ip="not-an-ip", dst_port=443)
    with pytest.raises(ValidationError):
        Network(dst_ip="203.0.113.50", dst_port=70000)


# --- behaviour ----------------------------------------------------------------------------------

def test_alert_cannot_be_changed_after_creation(s6_detection):
    alert = make_edr_alert(s6_detection)

    with pytest.raises(ValidationError):
        alert.severity = 5


def test_round_trips_through_json(s6_detection):
    alert = make_edr_alert(s6_detection)

    assert Alert.model_validate_json(alert.model_dump_json()) == alert
