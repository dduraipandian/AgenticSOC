"""Tests for the EDR normaliser: raw sentrix-edr detection -> Alert."""

import copy
import json
from pathlib import Path

import pytest

from domain.alerts import Entities, FileAccess
from domain.errors import InvalidEDRSeverity
from pipeline.events.edr import EDR

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def s6_detection():
    return json.loads((FIXTURES / "s6.json").read_text())


@pytest.fixture
def beacon_detection(s6_detection):
    raw = copy.deepcopy(s6_detection)
    raw.update(id="det-2", name="connection_to_known_bad_ip", technique="T1071", severity=85,
               net={"ip": "203.0.113.50", "port": 8443}, file=None)
    return raw


def test_s6_detection_maps_to_alert(s6_detection):
    alert = EDR(s6_detection).alert()

    assert alert.source == "sentrix-edr"
    assert alert.vendor_event_id == "det-1"
    assert alert.category == "sensitive_file_read"
    assert alert.technique == "T1003.008"
    assert alert.severity == 4  # EDR 75
    assert alert.entities == Entities(host="build-01", user="svc-maint", ip=None)
    assert alert.file == FileAccess(path="/etc/shadow", mode="read")
    assert alert.network is None
    assert alert.process.cmd == "awk -F: {print $1, $5} /etc/shadow"
    assert alert.process.parent_cmd == "/bin/bash /opt/maint/nightly.sh"
    # Ancestors are nearest first, so the first one is the parent itself.
    assert alert.process.ancestors[0].cmd == "/bin/bash /opt/maint/nightly.sh"
    assert alert.process.ancestors[1].cmd == "/bin/sh -c /bin/bash /opt/maint/nightly.sh"
    assert "/usr/sbin/cron" in [a.cmd for a in alert.process.ancestors]
    assert alert.raw == s6_detection


@pytest.mark.parametrize("edr_severity, expected", [
    (0, 1), (19, 1), (20, 2), (39, 2), (40, 3), (60, 4), (75, 4), (80, 5), (100, 5),
])
def test_edr_severity_maps_to_1_to_5(s6_detection, edr_severity, expected):
    s6_detection["severity"] = edr_severity

    assert EDR(s6_detection).alert().severity == expected



@pytest.mark.parametrize("edr_severity", [-1, 101])
def test_rejects_edr_severity_outside_0_to_100(s6_detection, edr_severity):
    s6_detection["severity"] = edr_severity
    with pytest.raises(InvalidEDRSeverity):
        EDR(s6_detection).alert()
