# Netze BW Portal Home Assistant Integration

Custom integration for `https://meine.netze-bw.de`.

## Features

- Login with username/password (no MFA support in v1)
- Discovers active IMS meters
- Sensor set per meter:
  - Daily energy
  - Total reading
  - 7 day sum
  - 30 day sum
  - Last measurement timestamp
  - Diagnostic meter metadata
- Options flow for meter selection and polling interval

## Installation (HACS)

1. Add this repository as custom repository in HACS (type: Integration).
2. Install **Netze BW Portal**.
3. Restart Home Assistant.
4. Add integration in UI.

## Notes

- If your Netze BW account requires MFA or additional interactive steps, login will fail in v1.
- Polling defaults to 15 minutes.
