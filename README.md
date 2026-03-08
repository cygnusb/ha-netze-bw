# Netze BW Portal Integration for Home Assistant

[![GitHub Release][release-badge]][release-url]
[![GitHub Downloads (all assets, all releases)][downloads-badge]][release-url]
[![HACS Custom][hacs-badge]][hacs-url]
[![HA Version][ha-badge]][ha-url]
[![License][license-badge]][license-url]
[![GitHub commit activity][commits-badge]][commits-url]

Custom [Home Assistant](https://www.home-assistant.io/) integration for the [Netze BW Kundenportal](https://meine.netze-bw.de) (meine.netze-bw.de). Automatically discovers your smart meters (IMS) and creates sensors for energy consumption and feed-in data.

## Features

- Authenticates via username/password against the Netze BW Auth0 login
- Auto-discovers all active IMS (intelligent metering system) meters
- Creates sensors per meter:

| Sensor | Description | Unit |
|---|---|---|
| Daily energy | Last reported daily consumption or feed-in | kWh |
| Total reading | Current meter reading | kWh |
| 7 day sum | Sum of the last 7 days | kWh |
| 30 day sum | Sum of the last 30 days | kWh |
| Last measurement date | Timestamp of last measurement | - |
| Serial number | Meter serial number (diagnostic) | - |
| Metering code | Metering code (diagnostic) | - |
| SMGW ID | Smart meter gateway ID (diagnostic) | - |
| Value types | Available value types (diagnostic) | - |

- Options flow to select which meters to track and set the polling interval (5 - 120 min, default 15 min)
- Supports both consumption and feed-in (Einspeisung) meters

## Installation

### HACS (recommended)

1. Open HACS in your Home Assistant instance
2. Click the three dots in the top right corner and select **Custom repositories**
3. Add this repository URL with category **Integration**
4. Search for **Netze BW Portal** and click **Install**
5. Restart Home Assistant

### Manual

1. Copy the `custom_components/netze_bw_portal` folder into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings** > **Devices & Services**
2. Click **Add Integration** and search for **Netze BW Portal**
3. Enter your meine.netze-bw.de email and password
4. The integration will discover your meters and create sensors automatically

### Options

After setup, click **Configure** on the integration to:

- Select which meters to enable/disable
- Adjust the polling interval (default: 15 minutes)

## Debug Logging

To enable debug logging, add the following to your `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.netze_bw_portal: debug
```

## Known Limitations

- Accounts with MFA (multi-factor authentication) or additional interactive login steps are not supported
- The integration uses the shared Home Assistant HTTP session; other integrations' cookies do not interfere, but the session is not isolated
- Data availability depends on the Netze BW portal API; daily values may be delayed by up to 24 hours

## Requirements

- Home Assistant 2025.1.0 or newer
- A registered account at [meine.netze-bw.de](https://meine.netze-bw.de)
- At least one active IMS meter linked to your account

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

[release-badge]: https://img.shields.io/github/v/release/cygnusb/ha-netze-bw?include_prereleases
[release-url]: https://github.com/cygnusb/ha-netze-bw/releases
[downloads-badge]: https://img.shields.io/github/downloads/cygnusb/ha-netze-bw/total
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://hacs.xyz
[ha-badge]: https://img.shields.io/badge/HA-2025.1.0+-blue.svg
[ha-url]: https://www.home-assistant.io/
[license-badge]: https://img.shields.io/github/license/cygnusb/ha-netze-bw
[license-url]: https://github.com/cygnusb/ha-netze-bw/blob/main/LICENSE
[commits-badge]: https://img.shields.io/github/commit-activity/y/cygnusb/ha-netze-bw
[commits-url]: https://github.com/cygnusb/ha-netze-bw/commits/main
