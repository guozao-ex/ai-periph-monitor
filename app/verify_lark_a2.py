"""HOLLYLAND LARK A2 fixtures captured live from the 0xFF90 WebHID channel."""
from types import SimpleNamespace
import unittest

import lark_a2_battery as lark


class LarkA2Tests(unittest.TestCase):
    def setUp(self):
        self.iface = SimpleNamespace(vid=0x3547, pid=0x0411, usage_page=0xFF90,
                                     input_len=64, output_len=64, usage=1,
                                     path='lark-test', product='Wireless Microphone',
                                     manufacturer='Shenzhen Hollyland Technology Co.,Ltd')
        # Live HEART reply: TX1 online 98%, TX2 offline, not charging.
        self.raw = bytes.fromhex(
            '55bbdd1080001001006200000000050000000000000400000000000000000000')

    def test_request_frame_shape(self):
        packet = lark.request()
        self.assertEqual(len(packet), 64)
        self.assertEqual(packet[0], 0x55)
        self.assertEqual(packet[1:7], bytes([0xAA, 0xDD, 0x10, 0x40, 0x00, 0x00]))
        self.assertEqual(packet[7:], bytes(57))

    def test_decode_live_heart_reply(self):
        self.assertEqual(lark.decode(self.raw), (98, 'discharging'))

    def test_decode_rejects_truncated_reply(self):
        self.assertIsNone(lark.decode(self.raw[:10]))

    def test_decode_rejects_wrong_head_and_command(self):
        for bad in (bytes([0x55, 0xAA, 0xDD]) + self.raw[3:],
                    bytes([0x55, 0xBB, 0xDD, 0x11]) + self.raw[4:]):
            self.assertIsNone(lark.decode(bad))

    def test_offline_transmitters_are_unknown(self):
        raw = bytearray(self.raw)
        raw[7] = raw[8] = 0
        self.assertIsNone(lark.decode(bytes(raw)))

    def test_matches_only_the_6118_vendor_page(self):
        self.assertTrue(lark.matches(self.iface))
        self.iface.usage_page = 0xFF82
        self.assertFalse(lark.matches(self.iface))
        self.iface.usage_page = 0xFF90
        self.iface.input_len = 32
        self.assertFalse(lark.matches(self.iface))
