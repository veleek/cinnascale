import binascii
import asyncio
import gc
import time
import wifi
import ssl
import socketpool
import adafruit_requests

# Get wifi details and more from a secrets.py file
try:
    from secrets import secrets
except ImportError:
    print("WiFi secrets are kept in secrets.py, please add them there!")
    raise

pool: socketpool.SocketPool = None
requests: adafruit_requests.Session = None

HASS_URL = secrets["homeassistant_url"]

# time.monotonic_ns() of the last successful post to Home Assistant.  Used by the watchdog in main.py to decide whether
# the device is still healthy.
last_success_ns: int = time.monotonic_ns()


class BaseCinnaSensor:
    def __init__(
        self, name: str, friendly_name: str, device_class: str, icon: str = None
    ):
        self.sensor_type = None
        self.name = name
        self.value = None
        self.attributes = {
            "friendly_name": friendly_name,
            "device_class": device_class,
            "icon": icon,
        }
        self.headers = {
            "Authorization": "Bearer " + secrets["token"],
            "Content-Type": "application/json",
        }

    @property
    def sensor_name(self):
        return self.sensor_type + "." + self.name

    def update(self, value: int, force: bool = False):
        if not force and self.value == value:
            print(f"[{self.sensor_name}] Value ({value}) unchanged, skipping update")
            return

        data = {"state": value, "attributes": self.attributes}

        update_message = "Force updating" if force else "Updating"
        print(f"[{self.sensor_name}] {update_message} to {value}... ", end="")

        url = f"{HASS_URL}/api/states/{self.sensor_name}"

        global requests, last_success_ns
        try:
            gc.collect()
            with requests.post(url, json=data, headers=self.headers, timeout=5) as response:
                status_code = response.status_code
        except Exception as e:
            # Never let a failed post take down the main loop.  If posts keep failing, the watchdog in main.py will
            # eventually hard reset the device.
            print(f"Update failed: {e!r}")
            return

        if status_code < 200 or status_code >= 300:
            print(f"Unexpected status code {status_code}")
        else:
            self.value = value
            last_success_ns = time.monotonic_ns()
            print(f"Success! Status: {status_code}")


class CinnaBinarySensor(BaseCinnaSensor):
    def __init__(
        self, name: str, friendly_name: str, device_class: str, icon: str = None
    ):
        super().__init__(name, friendly_name, device_class, icon)
        self.sensor_type = "binary_sensor"

    def update(self, value: bool, force: bool = False):
        return super().update("on" if value is True else "off", force)


class CinnaSensor(BaseCinnaSensor):
    def __init__(
        self,
        name: str,
        friendly_name: str,
        device_class: str,
        icon: str = None,
        state_class: str = None,
        unit_of_measurement: str = None,
    ):
        super().__init__(name, friendly_name, device_class, icon)
        self.sensor_type = "sensor"
        self.attributes["state_class"] = state_class
        self.attributes["unit_of_measurement"] = unit_of_measurement


class CinnaScaleDevice():
    SENSOR_NAME = "cinnascale"
    FRIENDLY_NAME = "CinnaScale"
    EMPTY_THRESHOLD = 10

    def __init__(self):
        self.weight_sensor = CinnaSensor(f"{self.SENSOR_NAME}", f"{self.FRIENDLY_NAME}", "weight", "mdi:scale", "measurement", "g")
        self.empty_sensor = CinnaBinarySensor(f"{self.SENSOR_NAME}_empty", f"{self.FRIENDLY_NAME} Empty", "battery")
        self.unstable_sensor = CinnaBinarySensor(f"{self.SENSOR_NAME}_unstable", f"{self.FRIENDLY_NAME} Unstable", "vibration")
        self.connection_strength_sensor = CinnaSensor(f"{self.SENSOR_NAME}_connection_strength", f"{self.FRIENDLY_NAME} Connection Strength", "signal_strength", "mdi:wifi")

    # Record the current WIFI signal strength.  We do this separately from the updating of any other sensors because we
    # want to record this whenever possible and avoid potential issues with the scale updated so that we have some data
    # point that indicates that we're still connected.
    def record_connection_strength(self):
        ap_info = wifi.radio.ap_info
        if ap_info is None:
            print("Not connected to WiFi, skipping connection strength update")
            return
        self.connection_strength_sensor.update(ap_info.rssi, True)

    def record_weight(self, success: bool, weight: int):
        if success:
            self.weight_sensor.update(weight)
            self.empty_sensor.update(weight < self.EMPTY_THRESHOLD)
            self.unstable_sensor.update(False)
        else:
            self.unstable_sensor.update(True)


async def init_network() -> bool:
    wifi.radio.hostname = "CinnaScale"

    if wifi.radio.connected:
        print("Already connected to WiFi {} (RSSI: {})".format(wifi.radio.ap_info.ssid, wifi.radio.ap_info.rssi))
    else:
        # old_mac = ['0xf4', '0x12', '0xfa', '0x8d', '0x9e', '0xdc']
        new_mac = "f4:12:fa:8d:e9:cc"
        wifi.radio.mac_address = binascii.unhexlify(new_mac.replace(":", ""))
        print("My MAC addr:", [hex(i) for i in wifi.radio.mac_address])

        show_network_strength(secrets["ssid"])
        await connect_to_network()

    global pool, requests

    # Only create the socket pool and session once.  Creating new ones on every reconnect orphans the sockets held by
    # the old session, and the ESP32 only has a handful of them.
    if requests is None:
        pool = socketpool.SocketPool(wifi.radio)
        requests = adafruit_requests.Session(pool, ssl.create_default_context())

    return wifi.radio.connected


async def connect_to_network(max_retries: int = 10) -> bool:
    """
    Connect to WiFi if we're not already connected, retrying with a backoff.  Safe to call at any time; returns
    immediately if we're already connected.
    """
    if wifi.radio.connected:
        return True

    ssid = secrets["ssid"]
    delay = 1

    for attempt in range(1, max_retries + 1):
        try:
            print("Connecting to {} (attempt {})... ".format(ssid, attempt), end="")
            wifi.radio.connect(ssid, secrets["password"], timeout=10)
            print("Connected with ip {}!".format(wifi.radio.ipv4_address))
            return True
        except Exception as e:
            print("Failed: {!r}".format(e))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    print("Failed to connect to WiFi after {} attempts".format(max_retries))
    return False


def show_network_strength(ssid: str):
    for network in wifi.radio.start_scanning_networks():
        if network.ssid == ssid:
            print("Found %s (RSSI: %d, Channel: %d)" % (network.ssid, network.rssi, network.channel))
            break
    wifi.radio.stop_scanning_networks()

