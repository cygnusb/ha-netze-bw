"""Unit tests for netze_bw_portal API helpers."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, call

from custom_components.netze_bw_portal.api import (
    NetzeBwPortalApiClient,
    NetzeBwPortalAuthError,
    NetzeBwPortalConnectionError,
    _HiddenInputParser,
)
from custom_components.netze_bw_portal.const import (
    MEASUREMENT_FILTER_DAY,
    VALUE_TYPE_CONSUMPTION,
    VALUE_TYPE_FEEDIN,
    VALUE_TYPE_FEEDIN_READING,
    VALUE_TYPE_READING,
)


class _FakeResponse:
    """Minimal aiohttp-like response for API unit tests."""

    def __init__(self, status: int, payload: dict | list | None = None) -> None:
        self.status = status
        self._payload = payload if payload is not None else {}

    async def json(self, content_type: str | None = None) -> dict | list:
        return self._payload


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


def test_get_json_retries_once_after_reauth() -> None:
    """A single 401 should trigger reauth and one successful retry."""
    session = AsyncMock()
    session.get = AsyncMock(
        side_effect=[
            _FakeResponse(401),
            _FakeResponse(200, {"ok": True}),
        ]
    )
    client = NetzeBwPortalApiClient(session=session)  # type: ignore[arg-type]

    async def _login() -> None:
        client._login_generation += 1

    client.async_login = AsyncMock(side_effect=_login)

    result = asyncio.run(client._get_json("https://example.test/resource"))

    assert result == {"ok": True}
    client.async_login.assert_awaited_once()
    assert session.get.await_count == 2


def test_get_json_raises_auth_error_after_second_401() -> None:
    """A repeated 401 after reauth should surface as an auth failure."""
    session = AsyncMock()
    session.get = AsyncMock(
        side_effect=[
            _FakeResponse(401),
            _FakeResponse(401),
        ]
    )
    client = NetzeBwPortalApiClient(session=session)  # type: ignore[arg-type]

    async def _login() -> None:
        client._login_generation += 1

    client.async_login = AsyncMock(side_effect=_login)

    try:
        asyncio.run(client._get_json("https://example.test/resource"))
    except NetzeBwPortalAuthError as err:
        assert str(err) == "Unauthorized"
    else:
        raise AssertionError("Expected NetzeBwPortalAuthError")

    client.async_login.assert_awaited_once()
    assert session.get.await_count == 2


def test_reauthenticate_retries_temporary_errors_with_backoff(monkeypatch) -> None:
    """Temporary reauth failures should be retried with exponential backoff."""
    sleep_mock = AsyncMock()
    monkeypatch.setattr("custom_components.netze_bw_portal.api.asyncio.sleep", sleep_mock)

    client = NetzeBwPortalApiClient(session=AsyncMock())  # type: ignore[arg-type]
    login_outcomes = iter(
        [
            NetzeBwPortalConnectionError("maintenance"),
            NetzeBwPortalConnectionError("still down"),
            None,
        ]
    )

    async def _login() -> None:
        outcome = next(login_outcomes)
        if outcome is not None:
            raise outcome
        client._login_generation += 1

    client.async_login = AsyncMock(side_effect=_login)

    asyncio.run(client._async_reauthenticate(0))

    assert client.async_login.await_count == 3
    assert sleep_mock.await_args_list == [call(0.5), call(1.0)]
    assert client._login_generation == 1


def test_parallel_401_requests_share_one_reauth() -> None:
    """Concurrent 401 responses should not trigger multiple logins."""
    session = AsyncMock()

    client = NetzeBwPortalApiClient(session=session)  # type: ignore[arg-type]
    request_count = 0

    async def _login_once() -> None:
        client._login_generation += 1
        await asyncio.sleep(0)

    client.async_login = AsyncMock(side_effect=_login_once)

    async def _run() -> list[dict | list]:
        first_wave_ready = asyncio.Event()

        async def _get(*args, **kwargs) -> _FakeResponse:
            nonlocal request_count
            request_count += 1
            if request_count <= 2:
                if request_count == 2:
                    first_wave_ready.set()
                await first_wave_ready.wait()
                return _FakeResponse(401)
            if request_count == 3:
                return _FakeResponse(200, {"request": 1})
            return _FakeResponse(200, {"request": 2})

        session.get = AsyncMock(side_effect=_get)

        return await asyncio.gather(
            client._get_json("https://example.test/one"),
            client._get_json("https://example.test/two"),
        )

    results = asyncio.run(_run())

    assert results == [{"request": 1}, {"request": 2}]
    client.async_login.assert_awaited_once()
