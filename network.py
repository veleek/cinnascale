import asyncio
import wifi
import mdns
import microcontroller
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

    @property
    def sensor_name(self):
        return self.sensor_type + "." + self.name

    def update(self, value: int):
        if self.value == value:
            print("Value for {} unchanged, skipping update".format(self.sensor_name))
            return

        headers = {
            "Authorization": "Bearer " + secrets["token"],
            "Content-Type": "application/json",
        }

        data = {"state": value, "attributes": self.attributes}

        print("Updating {} value to {}... ".format(self.sensor_name, value), end="")

        url = "{}/api/states/{}".format(HASS_URL, self.sensor_name)

        global requests
        try:
            response = requests.post(url, json=data, headers=headers, timeout=2)
        except adafruit_requests.OutOfRetries as oor:
            print(
                "Failed to update {} value.  Out of retries.".format(self.sensor_name)
            )
            traceback.print_exception(oor)
            return
            # raise CinnaScaleError("Failed to update {} value".format(self.sensor_name)) from e
        except RuntimeError as re:
            # Check if the cause of the exception was an OSError with EHOSTUNREACH
            if isinstance(re.__cause__, OSError) and re.__cause__.errno == EHOSTUNREACH:
                print("Host unreachable.  Restarting network... ", end="")
                connect_to_network()
                print("Reconnected!")
            else:
                print(
                    "Failed to update {} value.  Runtime error.".format(
                        self.sensor_name
                    )
                )
                traceback.print_exception(re)

            return
        except Exception as e:
            print("Unhandled exception!  Resetting...")
            traceback.print_exception(e)
            microcontroller.reset()
            print("Does this happen?")

        if response.status_code < 200 or response.status_code >= 300:
            print("Unexpected response code {}".format(response.status_code))
        else:
            print("Success! Status: {}".format(response.status_code))


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


async def init_network() -> bool:
    # network_strength_test()
    connected = connect_to_network()
    # connected = False

    if not connected:
        await init_config_portal()

    # init_mdns()


def connect_to_network() -> bool:
    MAX_RETRIES = 10
    retry_count = 0

    print("My MAC addr:", [hex(i) for i in wifi.radio.mac_address])
    wifi.radio.hostname = "CinnaScale"

    while retry_count < MAX_RETRIES:
        try:
            # show_available_networks()
            # print("Connecting to %s... " % secrets["ssid"], end="")

            show_network_strength(secrets["ssid"])
            wifi.radio.connect(secrets["ssid"], secrets["password"])
            print("Connected with ip {}!".format(wifi.radio.ipv4_address))
            break
        except Exception as e:
            print("Failed: {}".format(e))
            retry_count += 1

    if retry_count >= MAX_RETRIES:
        print("Failed to connect to WiFi after {} retries".format(retry_count))
        return False

    global pool, requests

    pool = socketpool.SocketPool(wifi.radio)
    requests = adafruit_requests.Session(pool, ssl.create_default_context())

    return True


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
