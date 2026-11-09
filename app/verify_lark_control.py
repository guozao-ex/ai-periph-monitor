"""LARK A2 control-path tests; every fixture is a live HEART reply or ack."""
import unittest

import lark_a2_battery as base
import lark_a2_control as ctl


def _pad(raw):
    """Live reads carry the report id and 63 trailing zero bytes."""
    return bytes(raw).ljust(64, b'\x00')


class LarkControlTests(unittest.TestCase):
    # Live HEART payload (16 bytes): TX1 online 94%, TX2 offline, noise 0,
    # neither transmitter charging; byte 15 carries the mute/charge bits.
    HEART = bytes.fromhex('01005e00000000050000000000000400')

    # Live HEART payloads captured after each 0x18 write (channel mode).
    HEART_MONO = bytes.fromhex('01005a00000000000000000000000400')
    HEART_STEREO = bytes.fromhex('01005a00000000000001000000000400')
    HEART_SAFE = bytes.fromhex('01005a00000000000002000000000400')

    # Live HEART payload after 0x05[1] (receiver volume).
    HEART_VOLUME_1 = bytes.fromhex('01005a00000000010000000000000400')

    # Live HEART payload after 0x39[1] (light off) and 0x3A[1,2] (beauty on).
    HEART_LIGHT_OFF = bytes.fromhex('01005a00000000000000000000010400')
    HEART_BEAUTY_ON = bytes.fromhex('01005a00000000000000000000000500')

    # Live set acknowledgements: cmd, device 0x80, length 1, value 1.
    ACK_MODE = bytes.fromhex('55bbdd1880000101')
    ACK_LEVEL = bytes.fromhex('55bbdd0580000101')
    ACK_LIGHT = bytes.fromhex('55bbdd3980000101')

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

    def test_heart_reports_channel_mode(self):
        self.assertEqual(ctl.parse_heart(self.HEART_MONO).voice_mode, ctl.CHANNEL_MONO)
        self.assertEqual(ctl.parse_heart(self.HEART_STEREO).voice_mode, ctl.CHANNEL_STEREO)
        self.assertEqual(ctl.parse_heart(self.HEART_SAFE).voice_mode, ctl.CHANNEL_SAFE_TRACK)
        self.assertEqual(ctl.channel_mode_name(ctl.CHANNEL_STEREO), '立体声')
        self.assertIsNone(ctl.channel_mode_name(7))

    def test_heart_reports_volume_and_light(self):
        self.assertEqual(ctl.parse_heart(self.HEART_VOLUME_1).voice_level, 1)
        self.assertTrue(ctl.parse_heart(self.HEART_MONO).light_on)
        self.assertFalse(ctl.parse_heart(self.HEART_LIGHT_OFF).light_on)

    def test_heart_reports_beauty_audio(self):
        # Live p14 = 0x04 while beauty audio is off: bit0 clear, preset 2 kept.
        off = ctl.parse_heart(self.HEART_MONO)
        self.assertFalse(off.beauty_audio_on)
        self.assertEqual(off.beauty_audio_mode, 2)
        beauty = ctl.parse_heart(self.HEART_BEAUTY_ON)
        self.assertTrue(beauty.beauty_audio_on)
        self.assertEqual(beauty.beauty_audio_mode, 2)

    def test_heart_leaves_camera_battery_unknown(self):
        # This receiver variant stops at byte 15, so byte 16 must not become 0.
        self.assertIsNone(ctl.parse_heart(self.HEART_MONO).rx_cam_level)
        self.assertEqual(ctl.parse_heart(self.HEART_MONO + bytes([0x37])).rx_cam_level, 0x37)
        self.assertIsNone(ctl.parse_heart(self.HEART_MONO + bytes([0x80])).rx_cam_level)

    def test_voice_mode_frame_shape(self):
        for mode in (ctl.CHANNEL_MONO, ctl.CHANNEL_STEREO, ctl.CHANNEL_SAFE_TRACK):
            packet = ctl.voice_mode_request(mode)
            self.assertEqual(len(packet), 64)
            self.assertEqual(packet[0], base.REPORT_ID)
            self.assertEqual(packet[1:7], bytes([0xAA, 0xDD, 0x18, 0x40, 0x00, 0x01]))
            self.assertEqual(packet[7], mode)

    def test_voice_mode_bounds(self):
        for bad in (-1, 3, 255, '1', 1.0, None):
            with self.assertRaises(ValueError):
                ctl.voice_mode_request(bad)

    def test_voice_level_frame_and_bounds(self):
        packet = ctl.voice_level_request(3)
        self.assertEqual(packet[1:7], bytes([0xAA, 0xDD, 0x05, 0x40, 0x00, 0x01]))
        self.assertEqual(packet[7], 3)
        for good in range(0, ctl.MAX_VOICE_LEVEL + 1):
            self.assertEqual(ctl.voice_level_request(good)[7], good)
        # The receiver clamps 8/16/100/255 to 5, so 6 is not a valid request.
        for bad in (-1, 6, 255, '5', 5.0, None):
            with self.assertRaises(ValueError):
                ctl.voice_level_request(bad)

    def test_light_frame_is_inverted(self):
        on, off = ctl.light_request(True), ctl.light_request(False)
        self.assertEqual(on[1:7], bytes([0xAA, 0xDD, 0x39, 0x40, 0x00, 0x01]))
        self.assertEqual(on[7], 0)
        self.assertEqual(off[7], 1)

    def test_light_requires_a_bool(self):
        for bad in (0, 1, None, 'on', 1.0):
            with self.assertRaises(ValueError):
                ctl.light_request(bad)

    def test_parse_set_ack_live_frames(self):
        self.assertIs(ctl.parse_set_ack(_pad(self.ACK_MODE), ctl.VOICE_MODE_SET), True)
        self.assertIs(ctl.parse_set_ack(_pad(self.ACK_LEVEL), ctl.VOICE_LEVEL_SET), True)
        self.assertIs(ctl.parse_set_ack(_pad(self.ACK_LIGHT), ctl.LIGHT_SET), True)
        # The same bytes without the report id still parse.
        self.assertIs(ctl.parse_set_ack(self.ACK_MODE, ctl.VOICE_MODE_SET), True)

    def test_parse_set_ack_rejects_other_frames(self):
        self.assertIsNone(ctl.parse_set_ack(_pad(self.ACK_MODE), ctl.VOICE_LEVEL_SET))
        self.assertIsNone(ctl.parse_set_ack(_pad(self.ACK_MODE)[:6], ctl.VOICE_MODE_SET))
        self.assertIsNone(ctl.parse_set_ack(b'', ctl.VOICE_MODE_SET))
        # A real heartbeat frame answers cmd 0x10, so it cannot pass as 0x18.
        heartbeat_frame = _pad(bytes.fromhex('55bbdd10800010') + self.HEART_MONO)
        self.assertIsNone(ctl.parse_set_ack(heartbeat_frame, ctl.VOICE_MODE_SET))
        # A transmitter-addressed reply is not the receiver ack either.
        tx = bytearray(_pad(self.ACK_MODE))
        tx[4] = 0x81
        self.assertIsNone(ctl.parse_set_ack(bytes(tx), ctl.VOICE_MODE_SET))
        # Only set commands have this one-byte acknowledgement shape.
        for cmd in (ctl.HEART_GET, 0x04, 0x11, 0x13):
            with self.assertRaises(ValueError):
                ctl.parse_set_ack(_pad(self.ACK_MODE), cmd)
        # A two-byte payload is not the acknowledged shape.
        wide = _pad(bytes.fromhex('55bbdd188000020101'))
        self.assertIsNone(ctl.parse_set_ack(wide, ctl.VOICE_MODE_SET))

    def test_parse_set_ack_reports_failure(self):
        failed = bytearray(_pad(self.ACK_MODE))
        failed[7] = 0
        self.assertIs(ctl.parse_set_ack(bytes(failed), ctl.VOICE_MODE_SET), False)
        odd = bytearray(_pad(self.ACK_MODE))
        odd[7] = 2
        self.assertIsNone(ctl.parse_set_ack(bytes(odd), ctl.VOICE_MODE_SET))
