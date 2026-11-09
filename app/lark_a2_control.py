"""HOLLYLAND LARK A2 control paths: heartbeat, noise gate, channel mode, volume.

Two official sources back this file.

* The WebHID page (res.hollyland.com/6118) carries the 6118 frame and the
  heartbeat/noise commands already used by lark_a2_battery.
* The Android app v3.2.2 (com.hollyland.larkc1, protocol package c6118) carries
  the rest. Its LegacyUsb6118ProtocolCodec builds the same frame: header
  0xAA/0xDD, command at [2], device at [3], big endian length at [4..5],
  payload from [6]; request device bytes are 0x40 RX / 0x41 TX1 / 0x42 TX2 and
  responses answer as 0x80 / 0x81 / 0x82. Set commands prepend the logical
  device 0/1/2 to their payload and the codec lifts it into the header, so a
  set frame on the wire is AA DD | cmd | 0x40 | len | value.

  HEART_GET         0x10 -> heartbeat, see parse_heart; the volume comes from
                            its byte 7, so VOICE_LEVEL_GET (0x04) is not used
  NOISE_LEVEL_SET   0x06 -> payload [0..3]
  NOISE_SWITCH_SET  0x19 -> payload [0x02]; this is a TOGGLE, not a value
  VOICE_LEVEL_SET   0x05 -> payload [0..5]; 8/16/100/255 all read back as 5,
                            so the ceiling is measured, not assumed
  VOICE_MODE_SET    0x18 -> payload [0 mono, 1 stereo, 2 safe track]
  LIGHT_SET         0x39 -> payload [0 = light on, 1 = light off]

Every set command answers BB DD | cmd | 0x80 | 00 01 | 01 on success; that is
what parse_set_ack decodes.

Verified live 2026-11-09, heartbeat read back after each write:
  noise     0 -> 0x19 -> 1 -> 0x06[2] -> 2 -> 0x19 -> 0
  channel   0 -> 0x18[1] -> 1 -> 0x18[2] -> 2 -> 0x18[0] -> 0
  volume    0 -> 0x05[1] -> 1 -> 0x05[0] -> 0
  light     0 -> 0x39[1] -> 1 -> 0x39[0] -> 0   (wire 1 = light off)
Nothing here writes templates, key names, language, pairing, power or OTA state.
"""
from dataclasses import dataclass

import lark_a2_battery as base

HEART_GET = 0x10
NOISE_LEVEL_SET = 0x06
NOISE_SWITCH_SET = 0x19
NOISE_SWITCH_PAYLOAD = b'\x02'
VOICE_LEVEL_SET = 0x05
VOICE_MODE_SET = 0x18
LIGHT_SET = 0x39
MAX_NOISE_LEVEL = 3
MAX_VOICE_LEVEL = 5
SET_ACK_COMMANDS = frozenset((NOISE_LEVEL_SET, NOISE_SWITCH_SET, VOICE_LEVEL_SET,
                              VOICE_MODE_SET, LIGHT_SET))

CHANNEL_MONO = 0
CHANNEL_STEREO = 1
CHANNEL_SAFE_TRACK = 2
CHANNEL_NAMES = {CHANNEL_MONO: '单声道', CHANNEL_STEREO: '立体声',
                 CHANNEL_SAFE_TRACK: '安全音轨'}


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
    voice_level: int = 0
    voice_mode: int = 0
    light_on: bool = True
    beauty_audio_on: bool = False
    beauty_audio_mode: int = 0
    rx_cam_level: int | None = None
    rx_cam_charging: bool = False


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
        voice_level=p[7],
        voice_mode=p[9],
        light_on=p[13] == 0,
        beauty_audio_on=bool(p[14] & 0x01),
        # The preset bits keep the last choice while beauty audio is off, so
        # this is not "the current preset" - the UI must gate it on the flag.
        beauty_audio_mode=(p[14] >> 1) & 0x7F,
        # Camera-receiver fields: the app decodes byte 16 and bit 4 of byte 15,
        # but this receiver only reports 16 payload bytes with that bit clear,
        # so both are inferred, never observed here.
        rx_cam_level=p[16] if len(p) > 16 and p[16] <= 100 else None,
        rx_cam_charging=bool(flags & 0x10),
    )


def channel_mode_name(mode):
    """Chinese label for a heartbeat channel mode, None when unregistered."""
    return CHANNEL_NAMES.get(mode)


def noise_level_request(level):
    """NOISE_LEVEL_SET frame for one of 0..3."""
    if type(level) is not int or not 0 <= level <= MAX_NOISE_LEVEL:
        raise ValueError('降噪档位必须是 0-3')
    return build(NOISE_LEVEL_SET, bytes([level]))


def noise_switch_request():
    """NOISE_SWITCH_SET frame; sending it flips the noise gate on/off."""
    return build(NOISE_SWITCH_SET, NOISE_SWITCH_PAYLOAD)


def voice_mode_request(mode):
    """VOICE_MODE_SET frame: 0 mono, 1 stereo, 2 safe track."""
    if type(mode) is not int or mode not in CHANNEL_NAMES:
        raise ValueError('声道模式必须是 0（单声道）、1（立体声）或 2（安全音轨）')
    return build(VOICE_MODE_SET, bytes([mode]))


def voice_level_request(level):
    """VOICE_LEVEL_SET frame for the receiver volume, 0..5."""
    if type(level) is not int or not 0 <= level <= MAX_VOICE_LEVEL:
        raise ValueError('接收器音量必须是 0-5')
    return build(VOICE_LEVEL_SET, bytes([level]))


def light_request(on):
    """LIGHT_SET frame; the wire value is inverted, 0 keeps the light on."""
    if type(on) is not bool:
        raise ValueError('灯光开关必须是 True/False')
    return build(LIGHT_SET, bytes([0 if on else 1]))


def parse_set_ack(raw, cmd):
    """True/False for a set reply, None when this is not the expected ack.

    Only the set commands in SET_ACK_COMMANDS may be passed: their ack carries
    exactly one payload byte (1 = applied, 0 = refused) and is structurally
    identical to every other reply of the same command, so a get reply would
    look like a success. That is why anything else raises ValueError.
    """
    if cmd not in SET_ACK_COMMANDS:
        raise ValueError('parse_set_ack 只接受写命令的回执')
    r = bytes(raw or b'')
    off = 1 if len(r) > 6 and r[0] == base.REPORT_ID else 0
    if len(r) < off + 6 or (r[off], r[off + 1]) != base.REPLY_HEAD:
        return None
    if r[off + 2] != cmd or r[off + 3] != base.DEVICE_REPLY_RX:
        return None
    if ((r[off + 4] << 8) | r[off + 5]) != 1 or len(r) < off + 7:
        return None
    value = r[off + 6]
    if value not in (0, 1):
        return None
    return value == 1
