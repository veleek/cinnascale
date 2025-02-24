import binascii
import asyncio
import wifi
import mdns
import ssl
import socketpool
import traceback
import adafruit_requests
from adafruit_httpserver import Server

EHOSTUNREACH = 118

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

HASS_URL = secrets["homeassistant_url"]


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

        global requests
        try:
            response = requests.post(url, json=data, headers=self.headers, timeout=2)
        except adafruit_requests.OutOfRetries as oor:
            print("Update failed.  Out of retries.")
            traceback.print_exception(oor)
            return
            # raise CinnaScaleError("Failed to update {} value".format(self.sensor_name)) from e

        if response.status_code < 200 or response.status_code >= 300:
            print(f"Unexpected status code {response.status_code}")
        else:
            self.value = value
            print(f"Success! Status: {response.status_code}")


class CinnaBinarySensor(BaseCinnaSensor):
    def __init__(
        self, name: str, friendly_name: str, device_class: str, icon: str = None
    ):
        super().__init__(name, friendly_name, device_class, icon)
        self.sensor_type = "binary_sensor"

    def update(self, value: int):
        return super().update("on" if value is True else "off")


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
        self.connection_strength_sensor.update(wifi.radio.ap_info.rssi, True)

    def record_weight(self, success: bool, weight: int):
        if success:
            self.weight_sensor.update(weight)
            self.empty_sensor.update(weight < self.EMPTY_THRESHOLD)
            self.unstable_sensor.update(False)
        else:
            self.unstable_sensor.update(True)


async def init_network() -> None:
    wifi.radio.hostname = "CinnaScale"

    if wifi.radio.connected:
        print("Already connected to WiFi {} (RSSI: {})".format(wifi.radio.ap_info.ssid, wifi.radio.ap_info.rssi))
    else:
        # network_strength_test()
        connected = connect_to_network()
        # connected = False

        if not connected:
            await init_config_portal()
            # init_mdns()

    global pool, requests

    pool = socketpool.SocketPool(wifi.radio)
    requests = adafruit_requests.Session(pool, ssl.create_default_context())


def connect_to_network() -> bool:
    MAX_RETRIES = 10
    retry_count = 0

    # old_mac = ['0xf4', '0x12', '0xfa', '0x8d', '0x9e', '0xdc']
    new_mac = "f4:12:fa:8d:e9:cc"
    wifi.radio.mac_address = binascii.unhexlify(new_mac.replace(":", ""))

    print("My MAC addr:", [hex(i) for i in wifi.radio.mac_address])

    ssid = secrets["ssid"]

    while retry_count < MAX_RETRIES:
        try:
            # show_available_networks()
            # print("Connecting to %s... " % secrets["ssid"], end="")

            show_network_strength(ssid)
            wifi.radio.connect(ssid, secrets["password"])
            print("Connected with ip {}!".format(wifi.radio.ipv4_address))
            return True
        except Exception as e:
            print("Failed: {}".format(e))
            retry_count += 1

    print("Failed to connect to WiFi after {} retries".format(retry_count))
    return False


async def init_config_portal():
    print("Starting Config Portal...")
    wifi.radio.start_dhcp()
    wifi.radio.start_ap("CinnaScale")

    while wifi.radio.ipv4_address_ap is None:
        print("Waiting for AP setup...")
        await asyncio.sleep_ms(10)

    asyncio.create_task(serve(str(wifi.radio.ipv4_gateway_ap)))


async def serve(host: str, port: int = 80):
    pool = socketpool.SocketPool(wifi.radio)
    server = Server(pool, "/static", debug=True)
    server.start(host, port)

    while True:
        server.poll()
        await asyncio.sleep(0)


def init_mdns():
    print("Starting MDNS...", end="")
    mdns_server = mdns.Server(wifi.radio)
    mdns_server.hostname = "custom-mdns-hostname"
    mdns_server.advertise_service(service_type="_http", protocol="_tcp", port=80)
    print("Done!")


def show_available_networks():
    # print("My MAC addr:", [hex(i) for i in wifi.radio.mac_address])

    print("Available WiFi networks:")
    for network in wifi.radio.start_scanning_networks():
        if network.ssid != "VirtualHottub":
            continue
        print(
            "\t%s\t\tRSSI: %d\tChannel: %d"
            % (str(network.ssid, "utf-8"), network.rssi, network.channel)
        )
    wifi.radio.stop_scanning_networks()


def show_network_strength(ssid: str):
    for network in wifi.radio.start_scanning_networks():
        if network.ssid == ssid:
            print(
                "Connecting to %s (RSSI: %d, Channel: %d)... "
                % (str(network.ssid, "utf-8"), network.rssi, network.channel),
                end="",
            )
            break
    wifi.radio.stop_scanning_networks()


def network_strength_test():
    print("Network strength test... ")

    while True:
        show_available_networks()
