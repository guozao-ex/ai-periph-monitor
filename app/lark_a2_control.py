"""HOLLYLAND LARK A2 control paths: heartbeat state and noise switching.

Protocol facts come from the official WebHID page (res.hollyland.com/6118):
same 6118 frame as lark_a2_battery, report id 0x55, PACKET_SIZE 63.

  HEART_GET        0x10  -> payload[0]/[1] tx online, [2]/[3] tx level,
                            [6] noise level (0=off, 1..3 = 10/15/20 dB),
                            [15] bit0/1 mute, bit2/3 charging
  NOISE_LEVEL_SET  0x06  -> payload [level]
  NOISE_SWITCH_SET 0x19  -> payload [0x02]; this is a TOGGLE, not a value

Verified live 2026-11-09: heart noise 0 -> 0x19 -> 1 -> 0x06[2] -> 2 -> 0x19 -> 0.
Nothing here writes templates, key names or language.
"""
from dataclasses import dataclass

import lark_a2_battery as base

HEART_GET = 0x10
NOISE_LEVEL_SET = 0x06
NOISE_SWITCH_SET = 0x19
NOISE_SWITCH_PAYLOAD = b'\x02'
MAX_NOISE_LEVEL = 3


@dataclass(frozen=True)
class Heart:
    tx1_online: bool
    tx2_online: bool
    tx1_level: int | None
    tx2_level: int | None
    noise_level: int
    tx1_charging: bool
    tx2_charging: bool
    tx1_muted: bool
    tx2_muted: bool


def build(cmd, payload=b'\x00'[:0], device=base.DEVICE_RX):
    """64 bytes including the leading report id, ready for write_output."""
    body = bytes([cmd & 0xFF, device & 0xFF, (len(payload) >> 8) & 0xFF, len(payload) & 0xFF]) + payload
    frame = bytes(base.FRAME_HEAD) + body
    return bytes([base.REPORT_ID]) + frame.ljust(base.PACKET_SIZE, b'\x00')


def parse_heart(payload):
    """Decode a HEART_GET payload; None when it is too short."""
    p = bytes(payload or b'')
    if len(p) < 16:
        return None
    flags = p[15]
    tx1, tx2 = p[0] == 1, p[1] == 1
    return Heart(
        tx1_online=tx1,
        tx2_online=tx2,
        tx1_level=p[2] if tx1 and p[2] <= 100 else None,
        tx2_level=p[3] if tx2 and p[3] <= 100 else None,
        noise_level=p[6],
        tx1_charging=bool(flags & 0x04),
        tx2_charging=bool(flags & 0x08),
        tx1_muted=bool(flags & 0x01),
        tx2_muted=bool(flags & 0x02),
    )


def noise_level_request(level):
    """NOISE_LEVEL_SET frame for one of 0..3."""
    if type(level) is not int or not 0 <= level <= MAX_NOISE_LEVEL:
        raise ValueError('降噪档位必须是 0-3')
    return build(NOISE_LEVEL_SET, bytes([level]))


def noise_switch_request():
    """NOISE_SWITCH_SET frame; sending it flips the noise gate on/off."""
    return build(NOISE_SWITCH_SET, NOISE_SWITCH_PAYLOAD)
