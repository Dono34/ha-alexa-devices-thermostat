# Alexa Devices with Amazon Smart Thermostat

A modified copy of Home Assistant's built-in **Alexa Devices** integration that adds support for the Amazon Smart Thermostat.

Status: early work in progress (0.2.0).

- Reads: room temperature, heat-to and cool-to set points, single set point, mode, fan mode, schedule on/off.
- Controls: mode (heat, cool, heat/cool, off), set points, fan mode (on, auto, circulate). Not yet confirmed against a real thermostat.

## What is in this repository

- `custom_components/alexa_devices` - the Home Assistant integration, based on Home Assistant Core 2026.11.2.
- `library` - a patched copy of [aioamazondevices](https://github.com/chemelli74/aioamazondevices) 17.2.7 that keeps thermostat endpoints and reads their set points, mode, fan mode and schedule state. It is renamed `aioamazondevices_thermostat` so it never clashes with the copy Home Assistant installs for the built-in integration.

While installed, this replaces the built-in Alexa Devices integration.

## Credits and license

Both parts are derived from Apache-2.0 licensed work: Home Assistant Core (the Home Assistant authors) and aioamazondevices (Simone Chemelli and contributors). This repository is distributed under the same license. See `LICENSE`.

This project is not affiliated with or endorsed by Amazon. It uses the same private interface as the Alexa app, which Amazon may change at any time.
