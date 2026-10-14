# cocoro-air

https://cocoroplusapp.jp.sharp/air

## Supported features

- [ ] Air Cleaner
    - [x] Temperature Sensor
    - [x] Humidity Sensor
    - [x] Humidifier Mode
    - [ ] Filter Remaining Sensor
    - [ ] ~Power~
    - [ ] ~Fan Speed~
    - [ ] ~Air Quality Sensor~

Note: You can use [echonetlite](https://github.com/scottyphillips/echonetlite_homeassistant) for Power, Air quality and Fan speed control. Thus this plugin won't support it unless someone contribute to the project.

## Installation

### Install via HACS Custom repositories

https://hacs.xyz/docs/faq/custom_repositories

## Configuration

1. Go to https://cocoroplusapp.jp.sharp/air and sign in/up
2. Get `device_id` and model name from devtools
3. Go to integrations and search for "Cocoro Air"
4. Input your email, password, device_id and model name

## Model supported
Model that I have tested:
- KILS50

## Authentication and recovery

Version 1.0.1 supports Sharp's Auth0 login flow. Each integration entry uses
its own persistent HTTP cookie session. Expired sessions are renewed and the
failed request is retried once. Polling errors mark entities unavailable rather
than displaying old measurements as current readings.

If authentication requests account confirmation or acceptance of updated terms,
sign in to [COCORO AIR](https://cocoroplusapp.jp.sharp/air) in a browser and complete
that step yourself. Then reload the integration or follow its reauthentication
prompt. The integration never accepts account terms automatically.

Existing temperature, humidity, water tank and humidity mode entity IDs are
preserved. The water tank remains an `on`/`off` enum sensor for compatibility with
existing dashboards. The humidity mode remains a switch.

After replacing the integration files manually, restart Home Assistant to load
the new Python modules.

## Development checks

Install `httpx`, then run:

```sh
python -m unittest discover -s tests -v
python -m compileall -q custom_components
```
