"""Unit tests for netze_bw_portal API helpers."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.netze_bw_portal.api import (
    NetzeBwPortalApiClient,
    _HiddenInputParser,
)
from custom_components.netze_bw_portal.const import (
    MEASUREMENT_FILTER_DAY,
    VALUE_TYPE_CONSUMPTION,
    VALUE_TYPE_FEEDIN,
    VALUE_TYPE_FEEDIN_READING,
    VALUE_TYPE_READING,
)


def test_hidden_input_parser_extracts_form_and_inputs() -> None:
    """Test parsing hidden callback form."""
    html = """
    <html><body>
      <form method=\"post\" action=\"https://login.netze-bw.de/login/callback\">
        <input type=\"hidden\" name=\"wa\" value=\"wsignin1.0\" />
        <input type=\"hidden\" name=\"wresult\" value=\"token\" />
      </form>
    </body></html>
    """

    parser = _HiddenInputParser()
    parser.feed(html)

    assert parser.form_action == "https://login.netze-bw.de/login/callback"
    assert parser.hidden_inputs == {"wa": "wsignin1.0", "wresult": "token"}


def test_extract_ims_meter_definitions_filters_and_maps() -> None:
    """Test IMS filtering logic from installations payload."""
    fixture = Path("tests/fixtures/netze_bw_portal/installations.json")
    data = json.loads(fixture.read_text())

    meters = NetzeBwPortalApiClient._extract_ims_meter_definitions(data)

    assert len(meters) == 2
    assert meters[0].id == "43D711FCC2A8570691DF880DAA5F98EE"
    assert meters[0].friendly_name == "Verbrauch"
    assert meters[0].value_types == [VALUE_TYPE_CONSUMPTION]
    assert meters[1].id == "67EB3DDAF71666674560F299E759F9E8"
    assert meters[1].value_types == [VALUE_TYPE_FEEDIN]


def test_value_type_mapping_for_consumption_and_feedin() -> None:
    """Test endpoint mapping from meter value type."""
    fixture = Path("tests/fixtures/netze_bw_portal/installations.json")
    data = json.loads(fixture.read_text())
    meters = NetzeBwPortalApiClient._extract_ims_meter_definitions(data)

    consumption = meters[0]
    feedin = meters[1]

    assert NetzeBwPortalApiClient._value_types_for_meter(consumption) == (
        VALUE_TYPE_CONSUMPTION,
        VALUE_TYPE_READING,
        "lastconsumption",
    )
    assert NetzeBwPortalApiClient._value_types_for_meter(feedin) == (
        VALUE_TYPE_FEEDIN,
        VALUE_TYPE_FEEDIN_READING,
        "lastfeedin",
    )


def test_parse_measurement_series_maps_har_fields() -> None:
    """Measurement series should retain bounds and interval timestamps."""
    client = NetzeBwPortalApiClient(session=None)  # type: ignore[arg-type]

    payload = {
        "minMeasurementStartDateTime": "2026-02-01T00:00:00Z",
        "maxMeasurementEndDateTime": "2026-03-01T00:00:00Z",
        "measurements": [
            {
                "startDatetime": "2026-02-27T00:00:00Z",
                "endDatetime": "2026-02-28T00:00:00Z",
                "value": "4.25",
                "unit": "kWh",
                "status": "VALID",
            },
            {
                "startDatetime": "2026-02-28T00:00:00Z",
                "endDatetime": "2026-03-01T00:00:00Z",
                "value": "5.75",
                "unit": "kWh",
                "status": "VALID",
            },
        ],
    }

    series = NetzeBwPortalApiClient._measurement_series_from_payload(
        meter_id="meter-1",
        value_type=VALUE_TYPE_CONSUMPTION,
        interval=MEASUREMENT_FILTER_DAY,
        payload=payload,
    )

    assert series.meter_id == "meter-1"
    assert series.value_type == VALUE_TYPE_CONSUMPTION
    assert series.interval == MEASUREMENT_FILTER_DAY
    assert series.unit == "kWh"
    assert len(series.points) == 2
    assert series.points[0].value == 4.25
    assert series.points[0].status == "VALID"
    assert series.points[0].start_datetime.isoformat() == "2026-02-27T00:00:00+00:00"
    assert series.points[0].end_datetime.isoformat() == "2026-02-28T00:00:00+00:00"
    assert series.min_measurement_start_datetime.isoformat() == "2026-02-01T00:00:00+00:00"
    assert series.max_measurement_end_datetime.isoformat() == "2026-03-01T00:00:00+00:00"
