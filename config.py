import json
import struct
import microcontroller

# Settings can come from secrets.py or be saved to NVM by the setup portal.  Values saved in NVM take precedence so the
# device can be reconfigured without editing files.  NVM bytes 0-3 hold the tare weight (see scale.py), so the config
# blob starts at offset 16: 4 magic bytes, a 2 byte length, then JSON.
KEYS = ("ssid", "password", "homeassistant_url", "token")

_OFFSET = 16
_MAGIC = b"CSC1"
_HEADER = _OFFSET + len(_MAGIC) + 2

try:
    from secrets import secrets as _secrets
except ImportError:
    _secrets = {}


def _load_saved() -> dict:
    nvm = microcontroller.nvm
    if nvm[_OFFSET : _OFFSET + len(_MAGIC)] != _MAGIC:
        return {}
    length = struct.unpack(">H", nvm[_HEADER - 2 : _HEADER])[0]
    try:
        return json.loads(bytes(nvm[_HEADER : _HEADER + length]).decode())
    except (ValueError, UnicodeError):
        print("Saved config in NVM is corrupt, ignoring it")
        return {}


_saved = _load_saved()


def get(key: str, default: str = None) -> str:
    return _saved.get(key) or _secrets.get(key) or default


def needs_setup() -> bool:
    return not (get("ssid") and get("homeassistant_url") and get("token"))


def save(values: dict):
    """
    Save the given settings to NVM, merged with any previously saved settings.  Empty values are ignored so that blank
    form fields keep the current setting.
    """
    merged = dict(_saved)
    merged.update({k: v for k, v in values.items() if k in KEYS and v})
    data = json.dumps(merged).encode()

    nvm = microcontroller.nvm
    if _HEADER + len(data) > len(nvm):
        raise ValueError("Config too large for NVM ({} bytes)".format(len(data)))

    nvm[_HEADER : _HEADER + len(data)] = data
    nvm[_HEADER - 2 : _HEADER] = struct.pack(">H", len(data))
    # Write the magic last so a partial write is never treated as valid.
    nvm[_OFFSET : _OFFSET + len(_MAGIC)] = _MAGIC
    _saved.clear()
    _saved.update(merged)
