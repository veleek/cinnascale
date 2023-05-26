import asyncio
import wifi
# import ipaddress
import ssl
import socketpool
import adafruit_requests

# URLs to fetch from
TEXT_URL = "http://wifitest.adafruit.com/testwifi/index.html"
JSON_QUOTES_URL = "https://www.adafruit.com/api/quotes.php"
JSON_STARS_URL = "https://api.github.com/repos/adafruit/circuitpython"

# Get wifi details and more from a secrets.py file
try:
    from secrets import secrets
except ImportError:
    print("WiFi secrets are kept in secrets.py, please add them there!")
    raise

pool: socketpool.SocketPool = None
requests: adafruit_requests.Session = None


async def update_sensor_async(value: int):
    print("update_sensor_async...")

    # URLs to fetch from
    SENSOR_URL = "http://192.168.1.11:8123/api/states/sensor.cinnascale"

    sensor_data = {
        "state": value,
        "attributes": {
            "unit_of_measurement": "g",
            "friendly_name": "CinnaScale",
            "icon": "mdi:scale",
            "device_class": "weight",
            "state_class": "measuring"
        }
    }

    headers = {
        "Authorization": "Bearer " + secrets["token"],
        "Content-Type": "application/json",
    }

    print("posting data...")

    global requests
    response = requests.post(SENSOR_URL, json=sensor_data, headers=headers)

    print("request complete...")

    print("-" * 40)
    print(response.status_code)
    print(response.text)
    print("-" * 40)

    print("Sensor updated!")


async def init_network():
    print("My MAC addr:", [hex(i) for i in wifi.radio.mac_address])

    print("Available WiFi networks:")
    for network in wifi.radio.start_scanning_networks():
        print(
            "\t%s\t\tRSSI: %d\tChannel: %d"
            % (str(network.ssid, "utf-8"), network.rssi, network.channel)
        )
    wifi.radio.stop_scanning_networks()

    print("Connecting to %s" % secrets["ssid"])
    wifi.radio.connect(secrets["ssid"], secrets["password"])
    print("Connected to {} with ip {}!".format(
        secrets["ssid"],
        wifi.radio.ipv4_address))
    await asyncio.sleep(1)

    global pool, requests

    pool = socketpool.SocketPool(wifi.radio)
    requests = adafruit_requests.Session(pool, ssl.create_default_context())
