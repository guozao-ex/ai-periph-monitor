"""LARK session-layer tests against an in-memory receiver.

The fake speaks the wire format the real one does (report id 0x55, BB DD reply,
big endian length), so the tests exercise the same code path the UI uses.
"""
from types import SimpleNamespace
from unittest import mock
import unittest

import lark_settings as ls
import lark_a2_control as ctl


TEMPLATE_0 = bytes.fromhex(
    '00' '576f726b427564647920e5bfabe68db7e994ae' '0000000000000000000000'
    '010700000000' '002800000000')


class FakeReceiver:
    """Minimal 6118 receiver: keeps state, answers like the hardware."""
    def __init__(self):
        self.heart = bytearray(bytes.fromhex('01005a00000000000000000000000400'))
        self.config = bytearray(4)
        self.templates = {num: bytearray(43) for num in range(5)}
        self.templates[0][:] = TEMPLATE_0
        self.key_names = {(0, 0): '语音输入'.encode().ljust(30, b'\x00'),
                          (0, 1): '发送'.encode().ljust(30, b'\x00')}
        self.flags = {0x13: 0, 0x35: 0, 0x3C: 0, 0x3E: 1}
        self.writes = []
        self.ignore = set()          # no reply at all
        self.refuse = set()          # replies 00 and leaves the state untouched
        self.empty_slots_answer_empty = False
        self.queue = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        pass

    def drain(self):
        self.queue.clear()

    def read_input(self, timeout_ms=100, length=64):
        return self.queue.pop(0) if self.queue else b''

    def write_output(self, packet, use_hidd=False):
        packet = bytes(packet)
        self.writes.append(packet)
        cmd = packet[3]
        size = (packet[5] << 8) | packet[6]
        payload = packet[7:7 + size]
        if cmd in self.ignore:
            return
        if cmd in self.refuse:
            reply = b'\x00'
        else:
            reply = self.handle(cmd, payload)
        if reply is not None:
            body = bytes([0x55, 0xBB, 0xDD, cmd, 0x80, 0, len(reply)]) + reply
            self.queue.append(body.ljust(64, b'\x00'))

    def handle(self, cmd, payload):
        if cmd == ctl.HEART_GET:
            return bytes(self.heart)
        if cmd == ctl.VIBE_TEMPLATE_GET:
            return bytes(self.config)
        if cmd == ctl.VIBE_INFO_GET:
            record = bytes(self.templates[payload[0]])
            if self.empty_slots_answer_empty and not any(record):
                return b''            # the web page's "no such template" form
            return record
        if cmd == ctl.VIBE_KEY_NAME_GET:
            num, key = payload[0], payload[1]
            return bytes([num, key]) + self.key_names.get((num, key), bytes(30))
        if cmd == ctl.SPEAKER_GET:
            return bytes([self.flags[0x13]])
        if cmd == ctl.VOICE_LOCK_GET:
            return bytes([self.flags[0x35]])
        if cmd == ctl.TX_IDENTIFY_GET:
            return bytes([self.flags[0x3C]])
        if cmd == ctl.SMART_BATTERY_GET:
            return bytes([self.flags[0x3E]])
        if cmd == ctl.VOICE_MODE_SET:
            self.heart[9] = payload[0]
            return b'\x01'
        if cmd == ctl.NOISE_LEVEL_SET:
            self.heart[6] = payload[0]
            return b'\x01'
        if cmd == ctl.NOISE_SWITCH_SET:
            self.heart[6] = 0 if self.heart[6] else 1
            return b'\x01'
        if cmd == ctl.VOICE_LEVEL_SET:
            self.heart[7] = payload[0]
            return b'\x01'
        if cmd == ctl.LIGHT_SET:
            self.heart[13] = payload[0]
            return b'\x01'
        if cmd == ctl.SHUTDOWN_TIME_SET:
            self.heart[10] = payload[0]
            return b'\x01'
        if cmd == ctl.VOICE_LOCK_SET:
            self.flags[0x35] = payload[0]
            return b'\x01'
        if cmd == ctl.TX_IDENTIFY_SET:
            self.flags[0x3C] = payload[0]
            return b'\x01'
        if cmd == ctl.SMART_BATTERY_SET:
            self.flags[0x3E] = payload[0]
            return b'\x01'
        if cmd == ctl.SPEAKER_SET:
            self.flags[0x13] = payload[0]
            return b'\x01'
        if cmd == ctl.VIBE_TEMPLATE_SET:
            self.config[:] = payload[:4]
            return b'\x00'
        if cmd == ctl.VIBE_INFO_SET:
            num = payload[0]
            self.templates[num][:] = payload[:43]
            return b'\x00'
        if cmd == ctl.VIBE_KEY_NAME_SET:
            num, key = payload[0], payload[1]
            self.key_names[(num, key)] = bytes(payload[2:32])
            return b'\x00'
        return None


class LarkSettingsTests(unittest.TestCase):
    def setUp(self):
        self.dev = FakeReceiver()
        self.iface = SimpleNamespace(input_len=64, path='fake-lark')
        for patcher in (mock.patch.object(ls, '_open', lambda wait=6.0: (self.iface, self.dev)),
                        mock.patch.object(ls, 'find_interface', lambda: self.iface)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_read_state_matches_the_receiver(self):
        state = ls.read_state()
        self.assertTrue(state.heart.tx1_online)
        self.assertEqual(state.heart.tx1_level, 90)
        self.assertEqual(state.config.work_mode, ctl.WORK_MODE_MIC)
        self.assertEqual(state.templates[0].name, 'WorkBuddy 快捷键')
        self.assertTrue(state.templates[1].is_empty)
        self.assertEqual(state.key_names[(0, 0)], '语音输入')
        self.assertEqual(state.key_names[(0, 1)], '发送')
        self.assertIs(state.speaker_on, True)      # wire 0 means the monitor is on
        self.assertEqual(state.voice_locked, 0)
        self.assertIs(state.tx_identify, False)
        self.assertIs(state.smart_battery, True)

    def test_set_channel_writes_and_reads_back(self):
        state = ls.set_channel(ctl.CHANNEL_STEREO)
        self.assertEqual(state.heart.voice_mode, ctl.CHANNEL_STEREO)
        modes = [packet for packet in self.dev.writes if packet[3] == ctl.VOICE_MODE_SET]
        self.assertEqual(len(modes), 1)
        self.assertEqual(modes[0][7], ctl.CHANNEL_STEREO)

    def test_setting_that_does_not_stick_raises(self):
        # No reply at all: the acknowledgement check fires first.
        self.dev.ignore.add(ctl.VOICE_LEVEL_SET)
        with self.assertRaises(ls.LarkError):
            ls.set_volume(3)
        # A 00 acknowledgement that changes nothing must fail the readback check.
        self.dev.ignore.clear()
        self.dev.refuse.add(ctl.VOICE_LEVEL_SET)
        with self.assertRaises(ls.LarkError):
            ls.set_volume(3)

    def test_empty_template_reply_is_tolerated(self):
        self.dev.empty_slots_answer_empty = True
        state = ls.read_state()
        self.assertIn(0, state.templates)
        self.assertNotIn(1, state.templates)

    def test_noise_switch_only_writes_when_it_differs(self):
        def toggles():
            return [packet[3] for packet in self.dev.writes].count(ctl.NOISE_SWITCH_SET)
        state = ls.set_noise_switch(False)       # already off, so nothing is sent
        self.assertEqual(toggles(), 0)
        self.assertEqual(state.heart.noise_level, 0)
        state = ls.set_noise_switch(True)
        self.assertEqual(toggles(), 1)
        self.assertEqual(state.heart.noise_level, 1)

    def test_set_speaker_survives_the_restart(self):
        state = ls.set_speaker(False)
        self.assertIs(state.speaker_on, False)

    def test_template_round_trip_and_clear(self):
        field = ls.parse_hotkey('Ctrl+A')
        state = ls.save_template(2, 'T2', field, ls.parse_hotkey('F1'))
        self.assertEqual(state.templates[2].name, 'T2')
        self.assertEqual(ls.hotkey_text(state.templates[2].work1), 'LCtrl+A')
        state = ls.save_key_name(2, 0, '单击名')
        self.assertEqual(state.key_names[(2, 0)], '单击名')
        state = ls.clear_template(2)
        self.assertTrue(state.templates[2].is_empty)

    def test_template_config_round_trip(self):
        state = ls.set_template_config(ctl.WORK_MODE_VIBE, 0, 1, ctl.LED_MODE_LOW)
        self.assertEqual((state.config.work_mode, state.config.tx1_template,
                          state.config.tx2_template, state.config.led_mode),
                         (ctl.WORK_MODE_VIBE, 0, 1, ctl.LED_MODE_LOW))

    def test_helpers(self):
        self.assertEqual(ls.expected_name('WorkBuddy 快捷键'), 'WorkBuddy 快捷键')
        self.assertEqual(ls.expected_name('a' + '测' * 10), 'a' + '测' * 9)
        self.assertEqual(ls.parse_hotkey('ctrl+d'), bytes.fromhex('010700000000'))
        self.assertEqual(ls.parse_hotkey('Shift+F1'), bytes.fromhex('023a00000000'))
        self.assertEqual(ls.parse_hotkey(''), bytes(6))
        self.assertEqual(ls.hotkey_text(bytes.fromhex('002800000000')), 'ENTER')

    def test_flags_are_reported_as_booleans(self):
        state = ls.read_state()
        self.assertIsInstance(state.speaker_on, bool)
        self.assertIsInstance(state.tx_identify, bool)


if __name__ == '__main__':
    unittest.main()
