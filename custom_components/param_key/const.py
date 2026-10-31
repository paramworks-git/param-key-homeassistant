"""Constants for PARAM KEY (protocol: docs/protocol.md in param-key)."""

from datetime import timedelta

DOMAIN = "param_key"

# Events the bridge fires (each carries `bridge`, its ESPHome node name).
EVENT_KEY = "esphome.openhomeremote_key"
EVENT_HELLO = "esphome.openhomeremote_hello"
EVENT_PAIR_REQUEST = "esphome.openhomeremote_pair_request"
EVENT_BRIDGE_ONLINE = "esphome.openhomeremote_bridge_online"

# Bridge actions: esphome.<bridge>_<action>. A bridge is any ESPHome device
# that offers set_remote.
ACTION_SET_REMOTE = "set_remote"
ACTION_FORGET_REMOTE = "forget_remote"
ACTION_PAIR_REPLY = "pair_reply"
ACTION_SEND_IR = "send_ir"

CONF_REMOTE = "remote"
CONF_KEY = "key"
CONF_FIRMWARE = "firmware"

# A remote that sent nothing for this long is taken to be asleep. Awake, it
# sends a hello at least every 60 s.
AWAKE_FOR = timedelta(seconds=65)
# Pairing requests older than this aren't offered any more.
PAIR_REQUEST_FRESH = timedelta(minutes=2)

# Most IR timings one packet carries (MAX_IR_TIMINGS in ohr_protocol.h).
MAX_IR_TIMINGS = 726

SIGNAL_REMOTE_SEEN = f"{DOMAIN}_remote_seen_{{}}"
