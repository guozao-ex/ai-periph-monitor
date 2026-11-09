"""LARK A2 control-path tests; fixture is a live HEART reply."""
from types import SimpleNamespace
import unittest

import lark_a2_battery as base
import lark_a2_control as ctl


class LarkControlTests(unittest.TestCase):
    # Live HEART payload (16 bytes): TX1 online 94%, TX2 offline, noise 0,
    # neither transmitter charging; byte 15 carries the mute/charge bits.
    HEART = bytes.fromhex('01005e00000000050000000000000400')

    def test_parse_heart_live(self):
        h = ctl.parse_heart(self.HEART)
        self.assertTrue(h.tx1_online)
        self.assertFalse(h.tx2_online)
        self.assertEqual((h.tx1_level, h.tx2_level), (94, None))
        self.assertEqual(h.noise_level, 0)
        self.assertFalse(h.tx1_charging)
        self.assertFalse(h.tx1_muted)

    def test_parse_heart_rejects_short_payload(self):
        self.assertIsNone(ctl.parse_heart(self.HEART[:12]))
        self.assertIsNone(ctl.parse_heart(b''))

    def test_noise_level_frame_shape(self):
        packet = ctl.noise_level_request(2)
        self.assertEqual(len(packet), 64)
        self.assertEqual(packet[0], base.REPORT_ID)
        self.assertEqual(packet[1:7], bytes([0xAA, 0xDD, 0x06, 0x40, 0x00, 0x01]))
        self.assertEqual(packet[7], 2)

    def test_noise_level_bounds(self):
        for bad in (-1, 4, 255, '2', 2.0, None):
            with self.assertRaises(ValueError):
                ctl.noise_level_request(bad)
        for good in (0, 1, 2, 3):
            self.assertEqual(ctl.noise_level_request(good)[7], good)

    def test_noise_switch_frame_shape(self):
        packet = ctl.noise_switch_request()
        self.assertEqual(packet[1:7], bytes([0xAA, 0xDD, 0x19, 0x40, 0x00, 0x01]))
        self.assertEqual(packet[7], 0x02)

    def test_heart_reports_noise_after_field_layout(self):
        payload = bytearray(self.HEART)
        payload[6] = 3
        self.assertEqual(ctl.parse_heart(bytes(payload)).noise_level, 3)
        payload[15] = 0x01
        h = ctl.parse_heart(bytes(payload))
        self.assertTrue(h.tx1_muted)
        self.assertFalse(h.tx1_charging)
