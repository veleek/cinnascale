# CinnaScale

A network connected kitchen scale that uses a QT Py ESP32-S3 to measure the cat's food bowl and report the results to
[Home Assistant] to notify me when the bowl is empty.

## Hardware

* [QT Py ESP32-S3] - Any ESP32 board (or other microcontroller) should work, but
  this one is small and has all the built in features I need including a battery charger and USB-C port and a STEMMA
  quick connector which is connected to...
* [AdaFruit NAU7802] - A 24-bit ADC with a STEMMA connector which provides a very
  simple API for measuring the force from...
* Any kitchen scale that has a single four wire strain gauge.  I used [this random one that I found for
  free][Pelouze Dymo SP5].

## Getting Started

* Install the library dependencies using [circup]

  ```bash
  circup install -r ./requirements.txt
  ```

* Create a `secrets.py` with your WiFi credentials, Home Assistant URL and [Long Lived Access Token]

  ```jsonc
  secrets = {
    "ssid": "<WiFi SSID>",
    "password": "<WiFi Password>",
    "homeassistant_url": "<Home Assistant URL>",
    "token": "<Long Lived Access Token>"
  }
  ```

[Home Assistant]: https://www.home-assistant.io/
[QT Py ESP32-S3]: https://www.adafruit.com/product/5426
[AdaFruit NAU7802]: https://www.adafruit.com/product/4538
[Pelouze Dymo SP5]: https://www.newegg.com/dymo-by-pelouze-sp5/p/N82E16848077011
[circup]: https://github.com/adafruit/circup
[Long Lived Access Token]: https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token