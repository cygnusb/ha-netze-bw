"""HTTP client for meine.netze-bw.de."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import logging
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from aiohttp import ClientSession

from .const import (
    AUTH_URL,
    BASE_URL,
    HISTORY_DAYS,
    MEASUREMENT_FILTER_DAY,
    METER_TYPE_IMS,
    VALUE_TYPE_CONSUMPTION,
    VALUE_TYPE_FEEDIN,
    VALUE_TYPE_FEEDIN_READING,
    VALUE_TYPE_READING,
)
from .models import (
    CoordinatorData,
    MeasurementPoint,
    MeasurementSeries,
    MeterDefinition,
    MeterDetails,
    MeterSnapshot,
)

_LOGGER = logging.getLogger(__name__)


class NetzeBwPortalError(Exception):
    """Base API exception."""


class NetzeBwPortalAuthError(NetzeBwPortalError):
    """Authentication failed."""


class NetzeBwPortalConnectionError(NetzeBwPortalError):
    """Communication failed."""


class _HiddenInputParser(HTMLParser):
    """Minimal parser to extract hidden form fields."""

    def __init__(self) -> None:
        super().__init__()
        self.form_action: str | None = None
        self.hidden_inputs: dict[str, str] = {}
        self._inside_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        if tag == "form":
            self._inside_form = True
            self.form_action = attr_map.get("action")
        if tag == "input" and self._inside_form and attr_map.get("type") == "hidden":
            name = attr_map.get("name")
            value = attr_map.get("value") or ""
            if name:
                self.hidden_inputs[name] = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._inside_form = False


class NetzeBwPortalApiClient:
    """API client for Netze BW portal."""

    def __init__(
        self,
        session: ClientSession,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._user_agent = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
        )

    def _bff_headers(self) -> dict[str, str]:
        """Return common headers required by the Duende BFF anti-forgery protection."""
        return {
            "x-csrf": "1",
            "User-Agent": self._user_agent,
        }

    async def async_ensure_login(self) -> str:
        """Ensure session is authenticated and return account sub."""
        account_sub = await self.async_get_account_sub()
        if account_sub:
            return account_sub
        await self.async_login()
        account_sub = await self.async_get_account_sub()
        if not account_sub:
            raise NetzeBwPortalAuthError("Login succeeded but no account subject available")
        return account_sub

    async def async_login(self) -> None:
        """Login with username/password through the Auth0 form flow."""
        _LOGGER.info("Starting login flow for %s", self._username)
        login_debug: dict[str, str | int] = {}
        try:
            login_entry = await self._session.get(
                f"{BASE_URL}/bff/auth/login?returnUrl=/signin?target=%2F",
                allow_redirects=True,
            )
        except Exception as err:
            raise NetzeBwPortalConnectionError("Could not open login entrypoint") from err
        _LOGGER.info("Login entrypoint reached: %s (status %s)", login_entry.url.host, login_entry.status)
        login_debug["login_entry_url"] = str(login_entry.url)
        login_debug["login_entry_status"] = login_entry.status

        if login_entry.url.host == "meine.netze-bw.de":
            _LOGGER.debug("Already authenticated via existing session")
            return

        if login_entry.url.host != "login.netze-bw.de":
            raise NetzeBwPortalAuthError(
                f"Unexpected login host: {login_entry.url.host or 'unknown'}"
            )

        login_page = await login_entry.text()
        parser = _HiddenInputParser()
        parser.feed(login_page)
        cookie_jar = self._session.cookie_jar.filter_cookies(login_entry.url)
        csrf_cookie = cookie_jar.get("_csrf")
        csrf_value = parser.hidden_inputs.get("_csrf") or (
            csrf_cookie.value if csrf_cookie is not None else ""
        )

        query = parse_qs(urlparse(str(login_entry.url)).query)

        payload = {
            "client_id": self._query_one(query, "client"),
            "redirect_uri": self._query_one(query, "redirect_uri"),
            "tenant": "netze-bw",
            "response_type": self._query_one(query, "response_type"),
            "scope": self._query_one(query, "scope"),
            "state": self._query_one(query, "state"),
            "nonce": self._query_one(query, "nonce"),
            "connection": "Username-Password-Authentication",
            "username": self._username,
            "password": self._password,
            "popup_options": {},
            "sso": True,
            "_intstate": "deprecated",
            "_csrf": csrf_value,
            "audience": self._query_one(query, "audience"),
            "code_challenge_method": self._query_one(query, "code_challenge_method"),
            "code_challenge": self._query_one(query, "code_challenge"),
            "protocol": self._query_one(query, "protocol"),
        }

        client_sku = self._query_one(query, "x-client-SKU", default=None)
        if client_sku is not None:
            payload["x-client-_sku"] = client_sku
        client_ver = self._query_one(query, "x-client-ver", default=None)
        if client_ver is not None:
            payload["x-client-ver"] = client_ver

        try:
            auth_response = await self._session.post(
                f"{AUTH_URL}/usernamepassword/login",
                json=payload,
                headers={
                    "Accept": "*/*",
                    "Auth0-Client": "eyJuYW1lIjoibG9jay5qcy11bHAiLCJ2ZXJzaW9uIjoiMTEuMTcuMyIsImVudiI6eyJhdXRoMC5qcy11bHAiOiI5LjExLjIifX0=",
                    "Origin": AUTH_URL,
                    "Referer": str(login_entry.url),
                    "User-Agent": self._user_agent,
                },
            )
        except Exception as err:
            raise NetzeBwPortalConnectionError("Could not submit credentials") from err
        _LOGGER.info("Credentials submitted, auth response status: %s", auth_response.status)
        login_debug["auth_response_status"] = auth_response.status
        login_debug["auth_response_url"] = str(auth_response.url)

        auth_body = await auth_response.text()
        form_parser = _HiddenInputParser()
        form_parser.feed(auth_body)
        if not form_parser.form_action or "wresult" not in form_parser.hidden_inputs:
            if auth_response.status >= 400:
                raise NetzeBwPortalAuthError(
                    f"Auth request failed with status {auth_response.status}"
                )
            snippet = auth_body[:250].replace("\n", " ")
            raise NetzeBwPortalAuthError(
                "Authentication challenge not supported (MFA/Captcha?) "
                f"or invalid response body: {snippet}"
            )

        callback_url = urljoin(str(auth_response.url), form_parser.form_action)
        callback_resp = await self._session.post(
            callback_url,
            data=form_parser.hidden_inputs,
            allow_redirects=False,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": AUTH_URL,
                "Referer": str(auth_response.url),
                "User-Agent": self._user_agent,
            },
        )
        _LOGGER.debug("Callback response status: %s", callback_resp.status)
        login_debug["callback_status"] = callback_resp.status
        login_debug["callback_url"] = str(callback_resp.url)

        location = callback_resp.headers.get("Location")
        if callback_resp.status not in (301, 302) or not location:
            raise NetzeBwPortalAuthError("Missing authorization resume redirect")

        resume_url = urljoin(str(callback_resp.url), location)
        resume_resp = await self._session.get(
            resume_url,
            allow_redirects=True,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": str(callback_resp.url),
                "User-Agent": self._user_agent,
            },
        )
        _LOGGER.info("Auth resume complete: %s (status %s)", resume_resp.url.host, resume_resp.status)
        login_debug["resume_status"] = resume_resp.status
        login_debug["resume_url"] = str(resume_resp.url)
        for response in [*resume_resp.history, resume_resp]:
            if response.cookies:
                self._session.cookie_jar.update_cookies(response.cookies, response.url)

        bff_cookies = self._session.cookie_jar.filter_cookies(BASE_URL)
        login_debug["bff_cookie_names"] = ",".join(sorted(bff_cookies.keys()))

        # Finalize login on app root and verify with brief retries.
        await self._session.get(
            f"{BASE_URL}/",
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": str(resume_resp.url),
                "User-Agent": self._user_agent,
            },
        )

        last_err: NetzeBwPortalAuthError | None = None
        for attempt in range(4):
            try:
                await self.async_get_account_sub(raise_on_unauth=True)
                _LOGGER.info("Login successful on attempt %d", attempt)
                return
            except NetzeBwPortalAuthError as err:
                last_err = err
                _LOGGER.debug("Auth check attempt %d failed: %s", attempt, err)
                await asyncio.sleep(0.4)

        raise NetzeBwPortalAuthError(
            f"Not authenticated after login flow: {login_debug}"
        ) from last_err

    async def async_get_account_sub(self, raise_on_unauth: bool = False) -> str | None:
        """Return account subject from /bff/auth/user."""
        try:
            resp = await self._session.get(
                f"{BASE_URL}/bff/auth/user",
                headers=self._bff_headers(),
            )
        except Exception as err:
            raise NetzeBwPortalConnectionError(
                f"Could not read auth user endpoint: {err!r}"
            ) from err

        if resp.status == 401:
            if raise_on_unauth:
                raise NetzeBwPortalAuthError("Not authenticated")
            return None

        if resp.status != 200:
            raise NetzeBwPortalConnectionError(f"Unexpected auth user status {resp.status}")

        claims = await resp.json(content_type=None)
        for claim in claims:
            if claim.get("type") == "sub":
                return claim.get("value")
        return None

    async def async_fetch_data(self, selected_meter_ids: set[str] | None = None) -> CoordinatorData:
        """Fetch all meter snapshots for selected IMS meters."""
        account_sub = await self.async_ensure_login()
        installations = await self._get_json(f"{BASE_URL}/bff/api/kuposervice/v1/portal/installations")

        meter_defs = self._extract_ims_meter_definitions(installations)
        if selected_meter_ids is not None:
            meter_defs = [meter for meter in meter_defs if meter.id in selected_meter_ids]

        snapshots: dict[str, MeterSnapshot] = {}
        errors: dict[str, str] = {}

        async def _load_meter(meter: MeterDefinition) -> None:
            try:
                snapshots[meter.id] = await self._fetch_meter_snapshot(meter)
            except NetzeBwPortalError as err:
                errors[meter.id] = str(err)

        await asyncio.gather(*(_load_meter(meter) for meter in meter_defs))

        return CoordinatorData(account_sub=account_sub, meters=snapshots, errors=errors)

    async def async_fetch_ims_meter_choices(self) -> dict[str, str]:
        """Return active IMS meters for options flow."""
        await self.async_ensure_login()
        installations = await self._get_json(f"{BASE_URL}/bff/api/kuposervice/v1/portal/installations")
        meter_defs = self._extract_ims_meter_definitions(installations)
        return {meter.id: meter.friendly_name for meter in meter_defs}

    async def _fetch_meter_snapshot(self, meter: MeterDefinition) -> MeterSnapshot:
        details_json = await self._get_json(f"{BASE_URL}/bff/api/imsservice/v1/meters/byId/{meter.id}")

        details = MeterDetails(
            serial_no=details_json.get("serialNo"),
            metering_code=details_json.get("meteringCode"),
            smgw_id=details_json.get("smgwId"),
            division=details_json.get("division"),
        )

        value_type_daily, value_type_total, last_endpoint = self._value_types_for_meter(meter)

        last_data = await self._get_json(f"{BASE_URL}/bff/api/imsservice/v1/meters/{meter.id}/{last_endpoint}")
        daily_value = self._to_float(last_data.get("value"))
        last_date = self._parse_datetime(last_data.get("date"))

        total_measurement = await self.async_fetch_measurement_series(
            meter_id=meter.id,
            value_type=value_type_total,
            interval=MEASUREMENT_FILTER_DAY,
            start=datetime.now(tz=timezone.utc) - timedelta(days=HISTORY_DAYS),
            end=datetime.now(tz=timezone.utc),
        )
        total_values = [point.value for point in total_measurement.points if point.value is not None]
        total_reading = total_values[-1] if total_values else None

        daily_measurement = await self.async_fetch_measurement_series(
            meter_id=meter.id,
            value_type=value_type_daily,
            interval=MEASUREMENT_FILTER_DAY,
            start=datetime.now(tz=timezone.utc) - timedelta(days=HISTORY_DAYS),
            end=datetime.now(tz=timezone.utc),
        )
        daily_values = [point.value for point in daily_measurement.points if point.value is not None]
        sum_30d = sum(daily_values[-30:]) if daily_values else None
        sum_7d = sum(daily_values[-7:]) if daily_values else None

        return MeterSnapshot(
            meter=meter,
            details=details,
            daily_value=daily_value,
            total_reading=total_reading,
            last_date=last_date,
            sum_7d=sum_7d,
            sum_30d=sum_30d,
            unit=daily_measurement.unit or total_measurement.unit,
        )

    async def async_fetch_measurement_series(
        self,
        meter_id: str,
        value_type: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> MeasurementSeries:
        """Fetch a generic measurement series."""
        payload = await self._get_json(
            f"{BASE_URL}/bff/api/imsservice/v1/meters/{meter_id}/measurements",
            params={
                "valueType": value_type,
                "startDate": self._isoformat(start),
                "endDate": self._isoformat(end),
                "filter": interval,
            },
        )
        return self._measurement_series_from_payload(
            meter_id=meter_id,
            value_type=value_type,
            interval=interval,
            payload=payload,
        )

    @classmethod
    def _measurement_series_from_payload(
        cls,
        *,
        meter_id: str,
        value_type: str,
        interval: str,
        payload: dict[str, Any],
    ) -> MeasurementSeries:
        """Parse the measurements payload into internal models."""
        measurements = payload.get("measurements", [])
        points: list[MeasurementPoint] = []
        unit: str | None = None
        for measurement in measurements:
            if unit is None:
                unit = measurement.get("unit")
            points.append(
                MeasurementPoint(
                    start_datetime=cls._parse_datetime(
                        measurement.get("startDatetime") or measurement.get("date")
                    )
                    or cls._parse_datetime(measurement.get("endDatetime") or measurement.get("date"))
                    or datetime.now(tz=timezone.utc),
                    end_datetime=cls._parse_datetime(
                        measurement.get("endDatetime") or measurement.get("date")
                    ),
                    value=cls._to_float(measurement.get("value")),
                    unit=measurement.get("unit"),
                    status=measurement.get("status"),
                )
            )

        return MeasurementSeries(
            meter_id=meter_id,
            value_type=value_type,
            interval=interval,
            points=points,
            unit=unit,
            min_measurement_start_datetime=cls._parse_datetime(
                payload.get("minMeasurementStartDateTime")
            ),
            max_measurement_end_datetime=cls._parse_datetime(
                payload.get("maxMeasurementEndDateTime")
            ),
        )

    @staticmethod
    def _value_types_for_meter(meter: MeterDefinition) -> tuple[str, str, str]:
        if VALUE_TYPE_FEEDIN in meter.value_types:
            return VALUE_TYPE_FEEDIN, VALUE_TYPE_FEEDIN_READING, "lastfeedin"
        return VALUE_TYPE_CONSUMPTION, VALUE_TYPE_READING, "lastconsumption"

    @staticmethod
    def _extract_ims_meter_definitions(installations: dict[str, Any]) -> list[MeterDefinition]:
        results: list[MeterDefinition] = []

        for item in installations.get("installations", []):
            meter_type = item.get("type")
            state = item.get("state")
            active = state == "Active" or (isinstance(state, dict) and state.get("code") == "Active")
            if meter_type != METER_TYPE_IMS or not active:
                continue

            meter_id = item.get("id")
            if not isinstance(meter_id, str):
                continue

            value_types = [value for value in item.get("valueTypes", []) if isinstance(value, str)]
            if not value_types:
                continue

            friendly_name = item.get("friendlyName") or meter_id
            results.append(
                MeterDefinition(
                    id=meter_id,
                    friendly_name=friendly_name,
                    meter_id=item.get("meterId"),
                    value_types=value_types,
                    meter_type=meter_type,
                    state=state if isinstance(state, str) else state.get("code") if isinstance(state, dict) else None,
                )
            )

        return results

    async def _get_json(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        response = await self._session.get(url, params=params, headers=self._bff_headers())
        if response.status == 401:
            raise NetzeBwPortalAuthError("Unauthorized")
        if response.status >= 400:
            raise NetzeBwPortalConnectionError(f"HTTP error {response.status} for {url}")
        return await response.json(content_type=None)

    @staticmethod
    def _query_one(query: dict[str, list[str]], key: str, default: str | None = "") -> str | None:
        values = query.get(key)
        if not values:
            return default
        return values[0]

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if value is None:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _isoformat(dt_value: datetime) -> str:
        return dt_value.isoformat(timespec="milliseconds").replace("+00:00", "Z")
