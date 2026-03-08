"""Unit tests for netze_bw_portal API helpers."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.netze_bw_portal.api import (
    NetzeBwPortalApiClient,
    _HiddenInputParser,
)
from custom_components.netze_bw_portal.const import (
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
