#!/usr/bin/env python3
"""Local login tester for Netze BW Portal integration client."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ssl

from aiohttp import ClientSession, TCPConnector

from custom_components.netze_bw_portal.api import (
    NetzeBwPortalApiClient,
    NetzeBwPortalAuthError,
    NetzeBwPortalConnectionError,
)


async def _main() -> int:
    username = os.getenv("NETZE_BW_USERNAME")
    password = os.getenv("NETZE_BW_PASSWORD")

    if not username or not password:
        print("Missing NETZE_BW_USERNAME or NETZE_BW_PASSWORD", file=sys.stderr)
        return 2

    ssl_context: ssl.SSLContext | bool = True
    if os.getenv("NETZE_BW_INSECURE_SSL") == "1":
        ssl_context = False
        print("WARNING: SSL verification disabled for this run")
    elif ca_file := os.getenv("NETZE_BW_CA_FILE"):
        ctx = ssl.create_default_context(cafile=ca_file)
        ssl_context = ctx
        print(f"Using custom CA file: {ca_file}")

    connector = TCPConnector(ssl=ssl_context)

    async with ClientSession(connector=connector) as session:
        try:
            pre = await session.get("https://meine.netze-bw.de/bff/auth/user")
            print(f"preflight /bff/auth/user status={pre.status}")
        except Exception as err:
            print(f"preflight FAILED: {err!r}", file=sys.stderr)

        client = NetzeBwPortalApiClient(session=session, username=username, password=password)

        try:
            sub = await client.async_ensure_login()
            meters = await client.async_fetch_ims_meter_choices()
        except NetzeBwPortalAuthError as err:
            print(f"AUTH FAILED: {err}", file=sys.stderr)
            return 1
        except NetzeBwPortalConnectionError as err:
            print(f"CONNECTION FAILED: {err}", file=sys.stderr)
            traceback.print_exc()
            return 1

        print("LOGIN OK")
        print(f"account_sub: {sub}")
        print(f"meters_found: {len(meters)}")
        for meter_id, name in meters.items():
            print(f"- {meter_id}: {name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
