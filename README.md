# Geely Galaxy Home Assistant Integration

A Home Assistant custom integration for Geely Galaxy vehicles, supporting:

- Vehicle status monitoring (battery, mileage, tire pressure, etc.)
- Remote control (lock/unlock, windows, climate)
- Home charger management
- Daily sign-in for points

## Supported Vehicles

- Geely Galaxy L7 (吉利银河 L7)
- Geely Galaxy (geely2 platform)

## Installation

### Via HACS (Recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed
2. Go to HACS → Integrations → Explore & Download Repositories
3. Search for "Geely Galaxy" and install
4. Restart Home Assistant

### Manual Installation

1. Copy `custom_components/geely_galaxy/` to your Home Assistant's `custom_components/` folder
2. Restart Home Assistant

## Configuration

Go to **Settings → Devices & Services → Add Integration** and search for "Geely Galaxy".

### Login Methods

1. **SMS Verification (Recommended)** - Enter phone number, complete verification
2. **Token Login** - Enter refresh_token and device_sn from packet capture
3. **Password Login** - Enter SM4-encrypted password

## Entities

### Sensors
- Vehicle status (battery, mileage, tire pressure, etc.)
- Sign-in status
- Charger status and records

### Buttons
- Daily sign-in
- Vehicle control (windows, climate, etc.)

### Switches
- Vehicle controls

## Troubleshooting

For issues, please check:
1. Home Assistant log (`Settings → System → Logs`)
2. [Issues](https://github.com/lyj5812/geely-galaxy/issues)

## Development

This integration is based on reverse engineering of the Geely Galaxy app API.

## License

MIT License