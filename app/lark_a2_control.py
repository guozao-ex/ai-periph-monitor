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
  SPEAKER_SET       0x12 -> payload [0 = monitor on, 1 = monitor off]; the
                            receiver restarts and re-enumerates on every change
  VOICE_LOCK_SET    0x34 -> payload [0 unlocked, 1 locked]
  SHUTDOWN_TIME_SET 0x36 -> payload [0 = mic auto power off on, 1 = off]
  TX_IDENTIFY_SET   0x3B -> payload [1 start the TX finder, 0 stop]
  SMART_BATTERY_SET 0x3D -> payload [1 on, 0 off]
  VIBE_TEMPLATE_SET 0x42 -> [workMode, tx1 template, tx2 template, ledMode]
  VIBE_TEMPLATE_GET 0x43 -> the same four bytes
  VIBE_INFO_SET     0x44 -> [num][name 30][hotkey 6][hotkey 6]([attr])
  VIBE_INFO_GET     0x45 -> [num]; the reply is a 43/44 byte record, an empty
                            payload means "no such template" and a shorter
                            payload is a 5 bit availability mask
  VIBE_KEY_NAME_SET 0x46 -> [num][keyIndex][name 30]
  VIBE_KEY_NAME_GET 0x47 -> [num][keyIndex]; the reply is 32 bytes
  VIBE_LANGUAGE_SET 0x4E -> [0 zh, 1 en]; the web page gates it on firmware
                            3.0.2.030 and this receiver reports 3.0.2.026 and
                            never answers, so no builder is offered

The setting commands answer BB DD | cmd | 0x80 | 00 01 | 01 on success, which is
what parse_set_ack decodes. The VIBE commands answer the same frame but with a
0x00 payload byte, so they must be confirmed by reading the state back; that is
why they are not part of SET_ACK_COMMANDS.

Verified live 2026-11-09/10, state read back after every write:
  noise     0 -> 0x19 -> 1 -> 0x06[2] -> 2 -> 0x19 -> 0
  channel   0 -> 0x18[1] -> 1 -> 0x18[2] -> 2 -> 0x18[0] -> 0
  volume    0 -> 0x05[1] -> 1 -> 0x05[0] -> 0
  light     0 -> 0x39[1] -> 1 -> 0x39[0] -> 0 (wire 1 = light off)
  monitor   0 -> 0x12[1] -> 1 -> 0x12[0] -> 0 (receiver re-enumerated twice)
  lock      0 -> 0x34[1] -> 1 -> 0x34[0] -> 0
  power off 0 -> 0x36[1] -> heartbeat byte 10 = 1 -> 0x36[0] -> 0
  finder    0 -> 0x3B[1] -> 1 -> 0x3B[0] -> 0
  battery   1 -> 0x3D[0] -> 0 -> 0x3D[1] -> 1
  template  template 4 with a name and Ctrl+A / F1 read back byte-identical,
            then written empty and read back as all zeros
  key name  "测试单击" read back identical through 0x47, then cleared
  config    0x42 [0,0,0,0] -> [0,0,0,1] -> [1,0,0,0] -> back, all read back
Nothing here deletes a template a transmitter still uses, unpaired a
transmitter or resets the receiver: those stay documented only.
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
SPEAKER_SET = 0x12
SPEAKER_GET = 0x13
VOICE_LOCK_SET = 0x34
VOICE_LOCK_GET = 0x35
SHUTDOWN_TIME_SET = 0x36
TX_IDENTIFY_SET = 0x3B
TX_IDENTIFY_GET = 0x3C
SMART_BATTERY_SET = 0x3D
SMART_BATTERY_GET = 0x3E
DELETE_PAIR_SET = 0x3F      # documented only: un-pairs a transmitter
FACTORY_RESET_SET = 0x40    # documented only: clears every setting
VIBE_TEMPLATE_SET = 0x42
VIBE_TEMPLATE_GET = 0x43
VIBE_INFO_SET = 0x44
VIBE_INFO_GET = 0x45
VIBE_KEY_NAME_SET = 0x46
VIBE_KEY_NAME_GET = 0x47
VIBE_LANGUAGE_SET = 0x4E    # documented only: firmware 3.0.2.030 and newer

MAX_NOISE_LEVEL = 3
MAX_VOICE_LEVEL = 5
TEMPLATE_COUNT = 5
NAME_FIELD_BYTES = 30
HOTKEY_BYTES = 6
WORK_MODE_MIC = 0
WORK_MODE_VIBE = 1
LED_MODE_NORMAL = 0
LED_MODE_LOW = 1
VIBE_ACK_PAYLOAD = 0x00     # every VIBE set answers 00, not 01

SET_ACK_COMMANDS = frozenset((NOISE_LEVEL_SET, NOISE_SWITCH_SET, VOICE_LEVEL_SET,
                              VOICE_MODE_SET, LIGHT_SET, SPEAKER_SET, VOICE_LOCK_SET,
                              SHUTDOWN_TIME_SET, TX_IDENTIFY_SET, SMART_BATTERY_SET))

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
    # Byte 10 is inverted in the app's decoder (0 = auto power off on) and the
    # live 0x36 write/read round trip agrees: 0x36[1] -> byte 10 = 1.
    auto_power_off_on: bool = True
    beauty_audio_on: bool = False
    beauty_audio_mode: int = 0
    rx_cam_level: int | None = None
    rx_cam_charging: bool = False


@dataclass(frozen=True)
class TemplateConfig:
    """The 0x42/0x43 record: mode, which template each TX applies, light level."""
    work_mode: int
    tx1_template: int
    tx2_template: int
    led_mode: int


@dataclass(frozen=True)
class TemplateInfo:
    """The 0x44/0x45 record. attr exists only on firmware 3.0.2.046 and up."""
    num: int
    name: str
    work1: bytes
    work2: bytes
    attr: int | None = None

    @property
    def is_empty(self):
        """True when the slot holds no template; the app deletes a slot that way."""
        return not self.name.strip() and not any(self.work1) and not any(self.work2)


@dataclass(frozen=True)
class KeyName:
    """One 0x46/0x47 record: key_index 0 = single press, 1 = double press."""
    num: int
    key_index: int
    name: str


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
        auto_power_off_on=p[10] == 0,
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


MODIFIER_BITS = {
    'LCtrl': 0x01, 'LShift': 0x02, 'LAlt': 0x04, 'LWin': 0x08,
    'RCtrl': 0x10, 'RShift': 0x20, 'RAlt': 0x40, 'RWin': 0x80,
}

# The page also accepts these spellings and folds them onto the left keys.
MODIFIER_ALIASES = {
    'CTRL': 0x01, 'CONTROL': 0x01, 'SHIFT': 0x02, 'ALT': 0x04,
    'WIN': 0x08, 'GUI': 0x08, 'META': 0x08, 'LGUI': 0x08, 'RGUI': 0x80,
}
_MODIFIER_BY_UPPER = {name.upper(): bit for name, bit in MODIFIER_BITS.items()}
_MODIFIER_BY_UPPER.update(MODIFIER_ALIASES)

KEY_CODES = {
    'A': 0x04, 'B': 0x05, 'C': 0x06, 'D': 0x07, 'E': 0x08, 'F': 0x09,
    'G': 0x0A, 'H': 0x0B, 'I': 0x0C, 'J': 0x0D, 'K': 0x0E, 'L': 0x0F,
    'M': 0x10, 'N': 0x11, 'O': 0x12, 'P': 0x13, 'Q': 0x14, 'R': 0x15,
    'S': 0x16, 'T': 0x17, 'U': 0x18, 'V': 0x19, 'W': 0x1A, 'X': 0x1B,
    'Y': 0x1C, 'Z': 0x1D,
    '1': 0x1E, '2': 0x1F, '3': 0x20, '4': 0x21, '5': 0x22,
    '6': 0x23, '7': 0x24, '8': 0x25, '9': 0x26, '0': 0x27,
    'ENTER': 0x28, 'ESC': 0x29, 'BACKSPACE': 0x2A, 'TAB': 0x2B, 'SPACE': 0x2C,
    'MINUS': 0x2D, 'EQUAL': 0x2E, 'BRACKETLEFT': 0x2F, 'BRACKETRIGHT': 0x30,
    'BACKSLASH': 0x31, 'SEMICOLON': 0x33, 'QUOTE': 0x34, 'BACKQUOTE': 0x35,
    'COMMA': 0x36, 'PERIOD': 0x37, 'SLASH': 0x38, 'CAPSLOCK': 0x39,
    'F1': 0x3A, 'F2': 0x3B, 'F3': 0x3C, 'F4': 0x3D, 'F5': 0x3E, 'F6': 0x3F,
    'F7': 0x40, 'F8': 0x41, 'F9': 0x42, 'F10': 0x43, 'F11': 0x44, 'F12': 0x45,
    'PRINTSCREEN': 0x46, 'SCROLLLOCK': 0x47, 'PAUSE': 0x48, 'INSERT': 0x49,
    'HOME': 0x4A, 'PAGEUP': 0x4B, 'DELETE': 0x4C, 'END': 0x4D, 'PAGEDOWN': 0x4E,
    'ARROWRIGHT': 0x4F, 'ARROWLEFT': 0x50, 'ARROWDOWN': 0x51, 'ARROWUP': 0x52,
    'POWER': 0x66,
}


def encode_name30(name):
    """A name in exactly 30 UTF-8 bytes, cut on a character boundary."""
    raw = str(name or '').encode('utf-8')[:NAME_FIELD_BYTES]
    while raw:
        try:
            raw.decode('utf-8')
            break
        except UnicodeDecodeError:
            raw = raw[:-1]
    return raw + bytes(NAME_FIELD_BYTES - len(raw))


def decode_name30(raw):
    """Text of a zero padded 30 byte name field."""
    field = bytes(raw or b'')[:NAME_FIELD_BYTES]
    end = field.find(b'\x00')
    if end >= 0:
        field = field[:end]
    return field.decode('utf-8', 'replace').strip()


def encode_hotkey6(keys):
    """Six bytes for a combination: modifier mask, then up to five key codes."""
    if isinstance(keys, (str, bytes, bytearray)) or keys is None:
        raise ValueError('快捷键必须是按键名列表，例如 ["LCtrl", "D"]')
    try:
        items = list(keys)
    except TypeError:
        raise ValueError('快捷键必须是按键名列表，例如 ["LCtrl", "D"]') from None
    modifier = 0
    codes = []
    for key in items:
        name = str(key).strip()
        upper = name.upper()
        if upper in _MODIFIER_BY_UPPER:
            modifier |= _MODIFIER_BY_UPPER[upper]
        elif upper in KEY_CODES:
            codes.append(KEY_CODES[upper])
        else:
            raise ValueError('未知按键：%s' % name)
    if len(codes) > HOTKEY_BYTES - 1:
        raise ValueError('快捷键最多 5 个普通键')
    return bytes([modifier] + codes + [0] * (HOTKEY_BYTES - 1 - len(codes)))


def decode_hotkey6(raw):
    """Key names of a six byte hotkey field, modifiers first."""
    field = bytes(raw or b'')[:HOTKEY_BYTES]
    field = field + bytes(HOTKEY_BYTES - len(field))
    names = [name for name, bit in MODIFIER_BITS.items() if field[0] & bit]
    by_code = {code: name for name, code in KEY_CODES.items()}
    names.extend(by_code[code] for code in field[1:] if code in by_code)
    return names


def format_hotkey6(raw):
    """Readable combination such as "LCtrl+D"; empty when nothing is bound."""
    return '+'.join(decode_hotkey6(raw))


def _hotkey_field(value):
    """Six wire bytes from either raw bytes or a list of key names.

    Shorter byte strings are zero padded, so b'' means "no binding" and is what
    an empty template (a deleted slot) carries.
    """
    if isinstance(value, str):
        raise ValueError('快捷键必须是按键名列表，例如 ["LCtrl", "D"]')
    if isinstance(value, (bytes, bytearray)):
        field = bytes(value)
        if len(field) > HOTKEY_BYTES:
            raise ValueError('快捷键字段最多 %d 字节' % HOTKEY_BYTES)
        return field + bytes(HOTKEY_BYTES - len(field))
    return encode_hotkey6(list(value or []))


def _template_num(num, label='模板序号'):
    if type(num) is not int or not 0 <= num < TEMPLATE_COUNT:
        raise ValueError('%s必须是 0-%d' % (label, TEMPLATE_COUNT - 1))
    return num


def _switch(value, label):
    if type(value) is not bool:
        raise ValueError('%s 必须是 True/False' % label)
    return 0 if value else 1


def template_get_request():
    """VIBE_TEMPLATE_GET: the four byte mode/template/light record."""
    return build(VIBE_TEMPLATE_GET)


def template_set_request(work_mode, tx1_template, tx2_template, led_mode):
    """VIBE_TEMPLATE_SET: mode, the template each TX applies, light level."""
    for label, value in (('交互模式', work_mode), ('灯光档位', led_mode)):
        if type(value) is not int or value not in (0, 1):
            raise ValueError('%s必须是 0 或 1' % label)
    _template_num(tx1_template, 'MIC1 模板号')
    _template_num(tx2_template, 'MIC2 模板号')
    return build(VIBE_TEMPLATE_SET, bytes([work_mode, tx1_template, tx2_template, led_mode]))


def template_info_get_request(num):
    """VIBE_INFO_GET frame for one template slot."""
    return build(VIBE_INFO_GET, bytes([_template_num(num)]))


def template_info_set_request(num, name, work1=b'\x00'[:0], work2=b'\x00'[:0], attr=None):
    """VIBE_INFO_SET: 43 bytes, or 44 when the trigger-mode byte is included."""
    body = (bytes([_template_num(num)]) + encode_name30(name)
            + _hotkey_field(work1) + _hotkey_field(work2))
    if attr is None:
        return build(VIBE_INFO_SET, body)
    if type(attr) is not int or not 0 <= attr <= 0xFF:
        raise ValueError('触发模式字节必须是 0-255')
    return build(VIBE_INFO_SET, body + bytes([attr]))


def key_name_get_request(num, key_index):
    """VIBE_KEY_NAME_GET for one key of one template."""
    _template_num(num)
    if type(key_index) is not int or key_index not in (0, 1):
        raise ValueError('按键序号必须是 0（单击）或 1（双击）')
    return build(VIBE_KEY_NAME_GET, bytes([num, key_index]))


def key_name_set_request(num, key_index, name):
    """VIBE_KEY_NAME_SET: 32 bytes, [num][key][30 byte name]."""
    _template_num(num)
    if type(key_index) is not int or key_index not in (0, 1):
        raise ValueError('按键序号必须是 0（单击）或 1（双击）')
    return build(VIBE_KEY_NAME_SET, bytes([num, key_index]) + encode_name30(name))


def speaker_request(on):
    """SPEAKER_SET; the wire value is inverted (0 = monitor on). Restarts the RX."""
    return build(SPEAKER_SET, bytes([_switch(on, '监听开关')]))


def speaker_get_request():
    return build(SPEAKER_GET)


def voice_lock_request(locked):
    """VOICE_LOCK_SET: 1 locks the transmitter keys, 0 unlocks them."""
    if type(locked) is not bool:
        raise ValueError('按键锁必须是 True/False')
    return build(VOICE_LOCK_SET, bytes([1 if locked else 0]))


def voice_lock_get_request():
    return build(VOICE_LOCK_GET)


def auto_power_off_request(on):
    """SHUTDOWN_TIME_SET; the wire value is inverted (0 = auto power off on)."""
    return build(SHUTDOWN_TIME_SET, bytes([_switch(on, '自动关机')]))


def tx_identify_request(on):
    """TX_IDENTIFY_SET: 1 makes the transmitter flash, 0 stops it."""
    if type(on) is not bool:
        raise ValueError('查找发射器必须是 True/False')
    return build(TX_IDENTIFY_SET, bytes([1 if on else 0]))


def tx_identify_get_request():
    return build(TX_IDENTIFY_GET)


def smart_battery_request(on):
    """SMART_BATTERY_SET: 1 enables the smart battery behaviour, 0 disables it."""
    if type(on) is not bool:
        raise ValueError('智能续航必须是 True/False')
    return build(SMART_BATTERY_SET, bytes([1 if on else 0]))


def smart_battery_get_request():
    return build(SMART_BATTERY_GET)


def parse_reply(raw, cmd):
    """Payload of the receiver reply to cmd, None when this is another frame."""
    r = bytes(raw or b'')
    off = 1 if len(r) > 6 and r[0] == base.REPORT_ID else 0
    if len(r) < off + 6 or (r[off], r[off + 1]) != base.REPLY_HEAD:
        return None
    if r[off + 2] != (cmd & 0xFF) or r[off + 3] != base.DEVICE_REPLY_RX:
        return None
    length = (r[off + 4] << 8) | r[off + 5]
    if len(r) < off + 6 + length:
        return None
    return r[off + 6:off + 6 + length]


def parse_template_config(payload):
    """The four byte 0x43 record; None when it is short."""
    p = bytes(payload or b'')
    if len(p) < 4:
        return None
    return TemplateConfig(work_mode=p[0], tx1_template=p[1],
                          tx2_template=p[2], led_mode=p[3])


def parse_template_info(payload):
    """The 43/44 byte 0x45 record; None when this is not a full record."""
    p = bytes(payload or b'')
    if len(p) < 43:
        return None
    return TemplateInfo(num=p[0], name=decode_name30(p[1:31]),
                        work1=p[31:37], work2=p[37:43],
                        attr=p[43] if len(p) >= 44 else None)


def template_availability(payload):
    """Five slot flags from a mask reply; None when this is a full record."""
    p = bytes(payload or b'')
    if len(p) != 1:
        return None
    return tuple(bool(p[0] & (1 << index)) for index in range(TEMPLATE_COUNT))


def parse_key_name(payload):
    """The 32 byte 0x47 record; None when it is short."""
    p = bytes(payload or b'')
    if len(p) < 32:
        return None
    return KeyName(num=p[0], key_index=p[1], name=decode_name30(p[2:32]))


def parse_flag_reply(payload, open_value=1):
    """True/False for a one byte state reply; None when it is empty."""
    p = bytes(payload or b'')
    if not p:
        return None
    return p[0] == open_value


def parse_int_reply(payload):
    """The first payload byte, None when the reply is empty."""
    p = bytes(payload or b'')
    return p[0] if p else None

