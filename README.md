# CinnaScale

A network connected kitchen scale that uses a QT Py ESP32-S3 to measure the cat's food bowl and report the results to
[Home Assistant] to notify me when the bowl is empty.

## Hardware

- [QT Py ESP32-S3] - Any ESP32 board (or other microcontroller) should work, but
  this one is small and has all the built in features I need including a battery charger and USB-C port and a STEMMA
  quick connector which is connected to...
- [AdaFruit NAU7802] - A 24-bit ADC with a STEMMA connector which provides a very
  simple API for measuring the force from...
- Any kitchen scale that has a single four wire strain gauge. I used [this random one that I found for
  free][Pelouze Dymo SP5].

## Getting Started

- The microcontroller runs [CircuitPython]. To update the code, you just edit the files on the device. It will
  automatically restart whenever it detects any changes.

- Connect to the device:

  - USB - if the device is connected to a PC, it will mount a USB device and the files can be edited directly.
  - Web - you can access the CircuitPython web UI directly by navigating to the IP address of the device. From here, you
    can edit files as you normally would.

  The Serial Monitor can be used to see the debug output. Mu (the CircuitPython editor) will automatically detect the
  connected device if connected via USB. Through the Web editor, you can create a remote serial connection as well.

- Install the library dependencies using [circup]

  ```bash
  circup install -r ./requirements.txt
  ```

- Create a `secrets.py` with your WiFi credentials, Home Assistant URL and [Long Lived Access Token]

  ```python
  secrets = {
    "ssid": "<WiFi SSID>",
    "password": "<WiFi Password>",
    "homeassistant_url": "<Home Assistant URL>",
    "token": "<Long Lived Access Token>"
  }
  ```

- Copy everything from this folder over to the remote device.

## Notes

- The scale that I'm using has 3 buttons on it.
  - Unit (g/oz) is connected to the MOSI pin on the board. Pressing it will force a manual report of the current weight
    on the scale.
  - Tare is connected to the MISO pin. Pressing it will sleep for 5 seconds giving you time to get the device into a
    "zero weight" state (in my case this is putting the empty cat bowl on top). It will then read the scale's weight
    (an average of 20 samples) and store the result in the microcontroller's non-volatile memory (NVM) which allows it
    to persist across restarts, so if there's a power outage it won't reset the scale's state.
  - Off is connected to the SCK pin. Pressing it manually restarts the micro controller.

[Home Assistant]: https://www.home-assistant.io/
[QT Py ESP32-S3]: https://www.adafruit.com/product/5426
[AdaFruit NAU7802]: https://www.adafruit.com/product/4538
[Pelouze Dymo SP5]: https://www.newegg.com/dymo-by-pelouze-sp5/p/N82E16848077011
[CircuitPython]: https://circuitpython.org/
[circup]: https://github.com/adafruit/circup
[Long Lived Access Token]: https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token
