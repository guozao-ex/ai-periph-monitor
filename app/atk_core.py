# -*- coding: utf-8 -*-
"""ATK 8K dongle core: identity, battery, DPI and polling rate on 0xFF02.

Self-contained on purpose: the lite entry imports this instead of battery.py /
mouse_settings.py, so a build of it never pulls in the other brands.

Frame (both directions), 17 bytes on the wire:
    [0x08, command, payload..., 0x00 pad to 15 bytes, checksum]
    checksum = (0x55 - sum(first 16 bytes)) & 0xFF
Every reply is accepted only when its own checksum adds up to 0x55.
"""
from dataclasses import dataclass, field
import time

import hid_win as hw

ATK_VID = 0x373B
# Shared ATK receivers that expose the 17-byte 0xFF02 configuration channel.
ATK_RECEIVER_PIDS = frozenset((0x102F, 0x101A, 0x101B))
CONFIG_USAGE_PAGE = 0xFF02
REPORT_ID = 0x08

# identity -> (name, encoder family, min dpi, max dpi)
ATK_MODELS = {
    (1, 8): ('ATK F1 Ultimate 2.0', 'ultra', 10, 42000),
    (2, 39): ('ATK X1 Pro Max', 'step50', 50, 36000),
    (2, 43): ('ATK X1 Ultra', 'ultra', 10, 42000),
    (2, 46): ('ATK X1 Ultimate 大师版', 'ultra', 10, 42000),
    (2, 83): ('ATK A9 Plus NearLink', 'step50', 100, 30000),
}

# Code table captured from the vendor web driver (v3-hub.atkgear.com) by
# logging its own HID writes against this exact mouse: clicking 125/250/500/1K/
# 2K/4K/8K sent rate bytes 0x08/0x04/0x02/0x01/0x10/0x20/0x40, each paired with
# its 0x55 complement. The 8K steps are 16/32/64, not 2/4/8.
RATE_CODES = {125: 0x08, 250: 0x04, 500: 0x02, 1000: 0x01,
              2000: 0x10, 4000: 0x20, 8000: 0x40}
RATE_CODES_1K = {hz: code for hz, code in RATE_CODES.items() if hz <= 1000}
RATE_ADDRESS = 0x00

# Sensor sample rate lives at 0xB5 as three 0x55 pairs; only the third byte
# carries the mode. Captured from the same page (basic / competitive / MAX).
SENSOR_ADDRESS = 0xB5
SENSOR_MODES = {0: '基础模式', 1: 'ATK绝鲨竞技固件', 2: 'ATK绝鲨竞技固件MAX'}
EIGHT_K_PIDS = frozenset((0x101B,))


class AtkError(RuntimeError):
    """The receiver or the mouse behind it did not answer usefully."""


def receiver(iface):
    """True when the interface is one of the shared ATK receivers."""
    return iface.vid == ATK_VID and iface.pid in ATK_RECEIVER_PIDS


def config_interface(ifaces=None):
    """The 17-byte 0xFF02 config interface of the ATK receiver, or None."""
    for iface in (ifaces if ifaces is not None else hw.enumerate_hid()):
        if (receiver(iface) and iface.usage_page == CONFIG_USAGE_PAGE
                and iface.usage == 2 and iface.output_len == 17 and iface.input_len >= 17):
            return iface
    return None


def frame(command, payload=()):
    body = bytes([command, *payload])
    if len(body) > 15:
        raise ValueError('ATK 数据过长')
    body += bytes(15 - len(body))
    return bytes([REPORT_ID]) + body + bytes([(0x55 - REPORT_ID - sum(body)) & 0xFF])


def exchange(dev, iface, packet, accept):
    if len(packet) > iface.output_len:
        raise ValueError('设备接口长度不匹配')
    dev.drain()
    dev.write_output(packet + bytes(iface.output_len - len(packet)), use_hidd=False)
    deadline = time.monotonic() + 0.9
    while time.monotonic() < deadline:
        reply = dev.read_input(timeout_ms=100)
        if reply and accept(reply):
            return reply
    raise AtkError('设备未回复，请唤醒鼠标并检查接收器')


def command(dev, iface, cmd, payload=(), predicate=lambda body: True):
    def accept(reply):
        return (len(reply) >= 17 and reply[0] == REPORT_ID
                and (sum(reply[:17]) & 0xFF) == 0x55 and predicate(reply[1:17]))
    return exchange(dev, iface, frame(cmd, payload), accept)[1:17]


def identity(dev, iface):
    """(CID, MID) of the mouse behind the receiver."""
    for attempt in range(3):
        try:
            body = command(dev, iface, 0x10,
                           predicate=lambda b: b[0] == 0x10 and b[1] == 0 and 2 <= b[4] <= 10)
            return (body[5], body[6])
        except AtkError:
            # The first frame after an idle period is often lost; retry twice.
            if attempt == 2:
                raise
            time.sleep(0.08)


def model(identity_value):
    return ATK_MODELS.get(identity_value)


def read(dev, iface, address, count):
    body = command(dev, iface, 8, (0, address >> 8, address & 0xFF, count),
                   lambda b: b[0] in (7, 8) and b[1] == 0
                   and b[2:4] == address.to_bytes(2, 'big') and b[4] >= count)
    return body[5:5 + count]


def write(dev, iface, address, data):
    data = bytes(data)
    body = command(dev, iface, 7, (0, address >> 8, address & 0xFF, len(data), *data),
                   lambda b: b[0] in (7, 8))
    return body


def profile(dev, iface):
    body = command(dev, iface, 0x0E, predicate=lambda b: b[0] == 0x0E and b[1] == 0 and 1 <= b[4] <= 10)
    return body[5] + 1


# ------------------------------------------------------------------- battery
@dataclass(frozen=True)
class Battery:
    level: int
    charging: bool
    millivolts: int | None


def read_battery(dev, iface):
    """Command 0x04 decoded exactly like wireless_battery.decode_compx.

    The reply body here already has the report id stripped, so every index is
    one lower than in the raw 17-byte report: level body[5], charging body[6],
    millivolts body[7:9] (big endian).
    """
    body = command(dev, iface, 0x04, (0,) * 14, lambda b: b[0] == 0x04)
    if body[1] != 0 or not 2 <= body[4] <= 10:
        raise AtkError('电量回包格式不符')
    level = body[5]
    if not 0 <= level <= 100:
        raise AtkError('电量回包超出 0-100')
    millivolts = (body[7] << 8) | body[8] if body[4] >= 4 else 0
    if not 2500 <= millivolts <= 5000:
        millivolts = 0
    return Battery(level=level, charging=body[6] == 1, millivolts=millivolts or None)


# ----------------------------------------------------------------------- DPI
@dataclass(frozen=True)
class DpiState:
    identity: tuple
    name: str
    family: str
    stages: tuple
    active: int
    minimum: int
    maximum: int
    step: int
    profile: int


def _axis(value, mode, family):
    if family == 'paw3395se':
        raise AtkError('该型号请使用专用编码')
    count = ((mode >> 2) & 3) * 256 + value
    base = ((10050 + count * 50) if mode & 2 else (count + 1) * 10) if family == 'ultra' else (count + 1) * 50
    return base * (2 if mode & 1 else 1)


def decode_dpi(data, family):
    data = bytes(data)
    if len(data) != 4 or sum(data) & 0xFF != 0x55:
        raise AtkError('DPI 数据校验失败')
    return (_axis(data[0], data[2] & 15, family), _axis(data[1], data[2] >> 4, family))


def encode_dpi(x, y, family):
    def axis(value):
        doubled = int(value > 30000)
        base = value // 2 if doubled else value
        if family == 'ultra':
            count = (base - 10050) // 50 if base > 10000 else base // 10 - 1
            mode = ((count >> 8) << 2) | (2 if base > 10000 else 0) | doubled
        else:
            count = base // 50 - 1
            mode = ((count >> 8) << 2) | doubled
        if count < 0:
            raise AtkError('该传感器无法表示 %d DPI，请换相邻数值' % value)
        return count & 255, mode
    a, ma = axis(x)
    b, mb = axis(y)
    body = bytes([a, b, ma | (mb << 4)])
    result = body + bytes([(0x55 - sum(body)) & 0xFF])
    if decode_dpi(result, family) != (x, y):
        raise AtkError('该传感器不能精确表示此 DPI，请调整数值')
    return result


def read_dpi(dev, iface, ident=None):
    ident = ident or identity(dev, iface)
    spec = model(ident)
    if spec is None:
        raise AtkError('未登记的 ATK 身份 %s' % (ident,))
    name, family, lo, hi = spec
    current_profile = profile(dev, iface)
    system = read(dev, iface, 0, 6)
    if any((system[i] + system[i + 1]) & 0xFF != 0x55 for i in (0, 2, 4)):
        raise AtkError('档位配置校验失败')
    count, active = system[2], system[4]
    if not 1 <= count <= 6 or not 0 <= active < count:
        raise AtkError('设备返回的档位配置无效')
    stages = tuple(decode_dpi(read(dev, iface, 12 + i * 4, 4), family) for i in range(count))
    if profile(dev, iface) != current_profile:
        raise AtkError('读取期间配置层已变化，请重新读取')
    return DpiState(identity=ident, name=name, family=family, stages=stages, active=active,
                    minimum=lo, maximum=hi, step=10 if family == 'ultra' else 50,
                    profile=current_profile)


def write_dpi(dev, iface, state, stage, value, activate=True):
    """Set one stage (both axes) and optionally activate it, then read back."""
    if type(stage) is not int or not 0 <= stage < len(state.stages):
        raise ValueError('档位无效')
    if value is not None and (type(value) is not int
                              or not state.minimum <= value <= state.maximum
                              or value % state.step):
        raise ValueError('DPI 必须为 %d–%d，步进 %d' % (state.minimum, state.maximum, state.step))
    change = value is not None and state.stages[stage] != (value, value)
    if change:
        write(dev, iface, 12 + stage * 4, encode_dpi(value, value, state.family))
        time.sleep(0.03)
    if activate and state.active != stage:
        write(dev, iface, 4, (stage, (0x55 - stage) & 0xFF))
        time.sleep(0.03)
    confirmed = read_dpi(dev, iface, state.identity)
    if change and confirmed.stages[stage] != (value, value):
        raise AtkError('DPI 写入后读回不一致（读回 %s）' % (confirmed.stages[stage],))
    if activate and confirmed.active != stage:
        raise AtkError('档位没有切换成功（读回 %d）' % (confirmed.active + 1))
    return confirmed


# -------------------------------------------------------------- polling rate
@dataclass(frozen=True)
class RateInfo:
    code: int
    hz: int | None
    table: dict = field(default_factory=dict, repr=False)


def eight_k(pid):
    return pid in EIGHT_K_PIDS


def rate_table(pid):
    """8K receivers expose the whole table, 1K receivers stop at 1000 Hz."""
    return RATE_CODES if eight_k(pid) else RATE_CODES_1K


def read_rate(dev, iface):
    system = read(dev, iface, RATE_ADDRESS, 10)
    if len(system) != 10 or any((system[i] + system[i + 1]) & 0xFF != 0x55 for i in range(0, 10, 2)):
        raise AtkError('ATK 配置校验失败')
    table = rate_table(iface.pid)
    code = system[0]
    hz = {v: k for k, v in table.items()}.get(code)
    return RateInfo(code=code, hz=hz, table=table)


def write_rate(dev, iface, hz):
    """Rewrite the 10-byte config block with only the rate pair changed."""
    table = rate_table(iface.pid)
    if hz not in table:
        raise ValueError('此接收器只支持 %s Hz' % (' / '.join(str(v) for v in sorted(table))))
    current = read(dev, iface, RATE_ADDRESS, 10)
    if len(current) != 10 or any((current[i] + current[i + 1]) & 0xFF != 0x55 for i in range(0, 10, 2)):
        raise AtkError('写入前读取配置失败')
    code = table[hz]
    block = bytes([code, (0x55 - code) & 0xFF]) + bytes(current[2:10])
    write(dev, iface, RATE_ADDRESS, block)
    time.sleep(0.05)
    return read_rate(dev, iface)


# ------------------------------------------------------------ sensor mode
def read_sensor_mode(dev, iface):
    """Mode byte of the sensor sample-rate register, None when unreadable."""
    data = read(dev, iface, SENSOR_ADDRESS, 6)
    if len(data) != 6 or any((data[i] + data[i + 1]) & 0xFF != 0x55 for i in range(0, 6, 2)):
        return None
    return data[4]


def write_sensor_mode(dev, iface, mode):
    """0 基础模式 / 1 ATK绝鲨竞技固件 / 2 ATK绝鲨竞技固件MAX, then read back."""
    if type(mode) is not int or mode not in SENSOR_MODES:
        raise ValueError('传感器采样率必须是 0、1 或 2')
    current = read(dev, iface, SENSOR_ADDRESS, 6)
    if len(current) != 6 or any((current[i] + current[i + 1]) & 0xFF != 0x55 for i in range(0, 6, 2)):
        current = bytes([0x01, 0x54, 0x0C, 0x49, 0, 0x55])
    block = bytes(current[:4]) + bytes([mode, (0x55 - mode) & 0xFF])
    write(dev, iface, SENSOR_ADDRESS, block)
    time.sleep(0.05)
    return read_sensor_mode(dev, iface)
