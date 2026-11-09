# -*- coding: utf-8 -*-
"""LARK A2 desktop session layer: read the whole state, write, read it back.

The UI never touches raw frames: it calls one function per control and gets a
fresh LarkState back, so every switch reflects what the receiver reported
after the write, not what the user asked for.
"""
from dataclasses import dataclass, field
import time

import hid_win
import lark_a2_battery as base
import lark_a2_control as ctl


class LarkError(RuntimeError):
    """The receiver is absent, silent, or did not confirm a write."""


@dataclass
class LarkState:
    heart: ctl.Heart
    config: ctl.TemplateConfig
    templates: dict = field(default_factory=dict)
    key_names: dict = field(default_factory=dict)
    speaker_on: bool | None = None
    voice_locked: int | None = None
    tx_identify: bool | None = None
    smart_battery: bool | None = None


# ---------------------------------------------------------------- transport
def find_interface():
    """The 0xFF90 interface of the receiver, or None."""
    for iface in hid_win.enumerate_hid():
        if base.matches(iface):
            return iface
    return None


def _open(wait=6.0):
    deadline = time.monotonic() + wait
    while True:
        iface = find_interface()
        if iface is not None:
            try:
                return iface, hid_win.HidDevice(iface.path, write=True)
            except OSError:
                pass
        if time.monotonic() >= deadline:
            raise LarkError('没有找到猛玛 LARK A2 接收器（3547:0411 / 0xFF90）')
        time.sleep(0.4)


def ask(dev, iface, packet, cmd, timeout=1.2):
    """Send one frame and return the receiver's payload for cmd, or None."""
    dev.drain()
    dev.write_output(packet, use_hidd=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = dev.read_input(timeout_ms=100, length=iface.input_len)
        if not raw:
            continue
        payload = ctl.parse_reply(bytes(raw), cmd)
        if payload is not None:
            return payload
    return None


def _require(payload, what):
    if payload is None:
        raise LarkError('%s 没有回包' % what)
    return payload


# ----------------------------------------------------------------- reading
def read_state(wait=6.0):
    """Read everything the page shows, in one session."""
    iface, dev = _open(wait)
    with dev:
        payload = _require(ask(dev, iface, ctl.build(ctl.HEART_GET), ctl.HEART_GET), '心跳')
        heart = ctl.parse_heart(payload)
        if heart is None:
            raise LarkError('心跳回包长度不对')
        config = ctl.parse_template_config(
            _require(ask(dev, iface, ctl.template_get_request(), ctl.VIBE_TEMPLATE_GET), '模板配置'))
        if config is None:
            raise LarkError('模板配置回包长度不对')
        templates = {}
        key_names = {}
        for num in range(ctl.TEMPLATE_COUNT):
            info = ctl.parse_template_info(
                ask(dev, iface, ctl.template_info_get_request(num), ctl.VIBE_INFO_GET) or b'')
            if info is None:
                continue
            templates[num] = info
            if info.is_empty:
                continue
            for key_index in (0, 1):
                record = ctl.parse_key_name(
                    ask(dev, iface, ctl.key_name_get_request(num, key_index), ctl.VIBE_KEY_NAME_GET) or b'')
                key_names[(num, key_index)] = record.name if record else ''
        speaker = ask(dev, iface, ctl.speaker_get_request(), ctl.SPEAKER_GET)
        lock = ask(dev, iface, ctl.voice_lock_get_request(), ctl.VOICE_LOCK_GET)
        identify = ask(dev, iface, ctl.tx_identify_get_request(), ctl.TX_IDENTIFY_GET)
        smart = ask(dev, iface, ctl.smart_battery_get_request(), ctl.SMART_BATTERY_GET)
    return LarkState(
        heart=heart, config=config, templates=templates, key_names=key_names,
        speaker_on=ctl.parse_flag_reply(speaker, open_value=0),
        voice_locked=ctl.parse_int_reply(lock),
        tx_identify=ctl.parse_flag_reply(identify),
        smart_battery=ctl.parse_flag_reply(smart),
    )


# ----------------------------------------------------------------- writing
def _write(packet, cmd, *, restart=False, wait=6.0):
    """Send a set frame, check its acknowledgement, and report the new state."""
    iface, dev = _open(wait)
    try:
        ack = ask(dev, iface, packet, cmd)
    finally:
        dev.close()
    if ack is None:
        raise LarkError('设备没有回执')
    if restart:
        return _read_after_restart()
    return read_state()


def _read_after_restart(attempts=6, delay=2.0):
    """0x12 reboots the receiver: wait, then retry the whole read a few times.

    The interface often still enumerates for a moment after the write, so
    "is it there now" is not a useful signal; only a successful heartbeat is.
    """
    time.sleep(1.5)
    last = None
    for _ in range(attempts):
        try:
            return read_state(wait=3.0)
        except (LarkError, OSError) as exc:
            last = exc
            time.sleep(delay)
    raise LarkError('接收器重启后没能读回状态：%s' % last)


def _expect(state, check, message):
    if not check(state):
        raise LarkError('写入后回读不一致：' + message)
    return state


def set_channel(mode):
    state = _write(ctl.voice_mode_request(mode), ctl.VOICE_MODE_SET)
    return _expect(state, lambda s: s.heart.voice_mode == mode, '声道模式')


def set_noise_level(level):
    """Pick a noise level; 0x06 alone cannot open the gate (verified live)."""
    state = read_state()
    gate_on = state.heart.noise_level != 0
    if state.heart.noise_level == level:
        return state
    if (level > 0) != gate_on:
        state = _write(ctl.noise_switch_request(), ctl.NOISE_SWITCH_SET)
        if (state.heart.noise_level != 0) != (level > 0):
            raise LarkError('降噪开关没能切换')
    if level == 0:
        return _expect(state, lambda s: s.heart.noise_level == 0, '降噪关闭')
    state = _write(ctl.noise_level_request(level), ctl.NOISE_LEVEL_SET)
    return _expect(state, lambda s: s.heart.noise_level == level, '降噪档位')


def set_noise_switch(on):
    state = read_state()
    current = state.heart.noise_level != 0
    if current == on:
        return state
    state = _write(ctl.noise_switch_request(), ctl.NOISE_SWITCH_SET)
    return _expect(state, lambda s: (s.heart.noise_level != 0) == on, '降噪开关')


def set_volume(level):
    state = _write(ctl.voice_level_request(level), ctl.VOICE_LEVEL_SET)
    return _expect(state, lambda s: s.heart.voice_level == level, '接收器音量')


def set_light(on):
    state = _write(ctl.light_request(on), ctl.LIGHT_SET)
    return _expect(state, lambda s: s.heart.light_on == on, '指示灯')


def set_speaker(on):
    # Changing this restarts the receiver; the readback happens after it is back.
    state = _write(ctl.speaker_request(on), ctl.SPEAKER_SET, restart=True)
    return _expect(state, lambda s: s.speaker_on == on, '监听开关')


def set_voice_lock(locked):
    state = _write(ctl.voice_lock_request(locked), ctl.VOICE_LOCK_SET)
    return _expect(state, lambda s: s.voice_locked == (1 if locked else 0), '按键锁')


def set_auto_power_off(on):
    state = _write(ctl.auto_power_off_request(on), ctl.SHUTDOWN_TIME_SET)
    return _expect(state, lambda s: s.heart.auto_power_off_on == on, '自动关机')


def set_tx_identify(on):
    state = _write(ctl.tx_identify_request(on), ctl.TX_IDENTIFY_SET)
    return _expect(state, lambda s: s.tx_identify == on, '查找发射器')


def set_smart_battery(on):
    state = _write(ctl.smart_battery_request(on), ctl.SMART_BATTERY_SET)
    return _expect(state, lambda s: s.smart_battery == on, '智能续航')


# --------------------------------------------------------------- templates
def expected_name(name):
    """What the receiver stores for a name: UTF-8, 30 bytes, cut on a character."""
    return ctl.decode_name30(ctl.encode_name30(name))


def parse_hotkey(text):
    """Six wire bytes from a text such as "Ctrl+D" or "LCtrl+Shift+A"."""
    parts = [part for part in str(text or '').replace(' ', '+').split('+') if part]
    return ctl.encode_hotkey6(parts)


def hotkey_text(field):
    """Readable form of a six byte hotkey, "" when nothing is bound."""
    return ctl.format_hotkey6(field)


def _apply_template(dev, iface, num, name, work1, work2):
    """Write one slot and confirm the record that comes back."""
    ask(dev, iface, ctl.template_info_set_request(num, name, work1, work2), ctl.VIBE_INFO_SET)
    record = ctl.parse_template_info(
        _require(ask(dev, iface, ctl.template_info_get_request(num), ctl.VIBE_INFO_GET), '模板回读'))
    if record is None or record.num != num:
        raise LarkError('模板回读失败')
    return record


def _apply_key_name(dev, iface, num, key_index, name):
    ask(dev, iface, ctl.key_name_set_request(num, key_index, name), ctl.VIBE_KEY_NAME_SET)
    record = ctl.parse_key_name(
        _require(ask(dev, iface, ctl.key_name_get_request(num, key_index), ctl.VIBE_KEY_NAME_GET), '按键名回读'))
    if record is None or record.name != expected_name(name):
        raise LarkError('按键名回读不一致')
    return record


def save_template(num, name, work1, work2):
    """Write one slot and confirm it by reading the slot back."""
    iface, dev = _open()
    try:
        record = _apply_template(dev, iface, num, name, work1, work2)
    finally:
        dev.close()
    if record.name != expected_name(name):
        raise LarkError('模板名称回读不一致')
    if isinstance(work1, (bytes, bytearray)) and record.work1 != bytes(work1).ljust(ctl.HOTKEY_BYTES, b'\x00'):
        raise LarkError('单击快捷键回读不一致')
    if isinstance(work2, (bytes, bytearray)) and record.work2 != bytes(work2).ljust(ctl.HOTKEY_BYTES, b'\x00'):
        raise LarkError('双击快捷键回读不一致')
    return read_state()


def save_template_with_keys(num, name, work1, work2, key0='', key1=''):
    """Write a slot and both key names in one session, then read the state once."""
    iface, dev = _open()
    try:
        record = _apply_template(dev, iface, num, name, work1, work2)
        _apply_key_name(dev, iface, num, 0, key0)
        _apply_key_name(dev, iface, num, 1, key1)
    finally:
        dev.close()
    if record.name != expected_name(name):
        raise LarkError('模板名称回读不一致')
    return read_state()


def save_key_name(num, key_index, name):
    iface, dev = _open()
    try:
        _apply_key_name(dev, iface, num, key_index, name)
    finally:
        dev.close()
    return read_state()


def clear_template(num):
    """Delete a slot: an empty name with both hotkeys zero is how the app does it."""
    iface, dev = _open()
    try:
        ask(dev, iface, ctl.template_info_set_request(num, ''), ctl.VIBE_INFO_SET)
        for key_index in (0, 1):
            ask(dev, iface, ctl.key_name_set_request(num, key_index, ''), ctl.VIBE_KEY_NAME_SET)
        record = ctl.parse_template_info(
            _require(ask(dev, iface, ctl.template_info_get_request(num), ctl.VIBE_INFO_GET), '模板回读'))
        names = [ctl.parse_key_name(
            _require(ask(dev, iface, ctl.key_name_get_request(num, key_index), ctl.VIBE_KEY_NAME_GET),
                     '按键名回读'))
            for key_index in (0, 1)]
    finally:
        dev.close()
    if record is None or not record.is_empty:
        raise LarkError('模板删除后回读仍有内容')
    if any(record and record.name for record in names):
        raise LarkError('模板删除后按键名仍有内容')
    return read_state()


def set_template_config(work_mode, tx1_template, tx2_template, led_mode):
    state = _write(ctl.template_set_request(work_mode, tx1_template, tx2_template, led_mode),
                   ctl.VIBE_TEMPLATE_SET)
    return _expect(state,
                   lambda s: (s.config.work_mode, s.config.tx1_template,
                              s.config.tx2_template, s.config.led_mode)
                   == (work_mode, tx1_template, tx2_template, led_mode),
                   '模板配置')
