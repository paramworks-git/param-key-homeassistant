"""IR codes from service data or keymap entries, as infrared-protocols commands.

The protocols are HA's own (the infrared integration's library), so a code from
HA's IR integrations, a Pronto code from a public database, or raw timings all
take the same path to the remote.
"""

from typing import Any

from infrared_protocols.commands import Command
from infrared_protocols.commands.nec import NECCommand
from infrared_protocols.commands.pronto import ProntoCommand
from infrared_protocols.commands.rc5 import RC5Command
from infrared_protocols.commands.rc6 import RC6Command
from infrared_protocols.commands.samsung import Samsung32Command
from infrared_protocols.commands.sony import SonyCommand
import voluptuous as vol

PROTOCOLS = ["nec", "samsung", "sony", "rc5", "rc6", "pronto", "raw"]


def code_int(value: Any) -> int:
    """An address or command: 4, "4", "0x04" or "0b100"."""
    if isinstance(value, bool):
        raise vol.Invalid("expected a number")
    if isinstance(value, int):
        number = value
    else:
        try:
            number = int(str(value).strip(), 0)
        except ValueError as err:
            raise vol.Invalid(f"expected a number, got {value!r}") from err
    if number < 0:
        raise vol.Invalid("must not be negative")
    return number


class RawCommand(Command):
    """Timings as given: microseconds, positive = carrier on."""

    def __init__(self, *, timings: list[int], modulation: int = 38000) -> None:
        super().__init__(modulation=modulation)
        self.timings = list(timings)

    def get_raw_timings(self) -> list[int]:
        return self.timings


def build_command(data: dict[str, Any]) -> Command:
    """Command for the fields of param_key.send_ir (or an `ir:` keymap entry).

    Raises vol.Invalid when fields are missing or don't fit the protocol.
    """
    protocol = str(data.get("protocol", "")).lower()
    repeat = int(data.get("repeat", 0))

    def need(*fields: str) -> list[Any]:
        missing = [f for f in fields if data.get(f) is None]
        if missing:
            raise vol.Invalid(f"{protocol} needs {', '.join(missing)}")
        return [data[f] for f in fields]

    try:
        if protocol == "nec":
            address, command = need("address", "command")
            return NECCommand(address=code_int(address), command=code_int(command), repeat_count=repeat)
        if protocol == "samsung":
            address, command = need("address", "command")
            return Samsung32Command(address=code_int(address), command=code_int(command), repeat_count=repeat)
        if protocol == "sony":
            address, command = need("address", "command")
            bits = int(data.get("address_bits", 5))
            # Sony devices want a code at least 3 times.
            return SonyCommand(address=code_int(address), address_bits=bits, command=code_int(command),
                               repeat_count=int(data.get("repeat", 2)))
        if protocol in ("rc5", "rc6"):
            address, command = need("address", "command")
            cls = RC5Command if protocol == "rc5" else RC6Command
            return cls(address=code_int(address), command=code_int(command),
                       toggle=int(data.get("toggle", 0)), repeat_count=repeat)
        if protocol == "pronto":
            (code,) = need("pronto")
            words = "".join(str(code).split())
            return ProntoCommand(pronto_data=bytes.fromhex(words), repeat_count=repeat)
        if protocol == "raw":
            (timings,) = need("timings")
            timings = [int(t) for t in timings]
            if not timings or 0 in timings:
                raise vol.Invalid("raw timings must be non-zero microseconds, positive = carrier on")
            return RawCommand(timings=timings, modulation=int(data.get("carrier", 38000)))
    except (ValueError, TypeError) as err:
        raise vol.Invalid(f"{protocol}: {err}") from err
    raise vol.Invalid(f"protocol must be one of {', '.join(PROTOCOLS)}")

