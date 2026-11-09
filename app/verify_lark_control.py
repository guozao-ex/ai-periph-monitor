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
    # Same shape, captured 2026-11-10 for the five extra settings.
    ACK_12 = bytes.fromhex('55bbdd1280000101')
    ACK_34 = bytes.fromhex('55bbdd3480000101')
    ACK_36 = bytes.fromhex('55bbdd3680000101')
    ACK_3B = bytes.fromhex('55bbdd3b80000101')
    ACK_3D = bytes.fromhex('55bbdd3d80000101')

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

    def test_heart_reports_auto_power_off(self):
        # 0x36[0] leaves byte 10 at 0 (auto power off on), 0x36[1] sets it to 1.
        self.assertTrue(ctl.parse_heart(self.HEART_MONO).auto_power_off_on)
        payload = bytearray(self.HEART_MONO)
        payload[10] = 1
        self.assertFalse(ctl.parse_heart(bytes(payload)).auto_power_off_on)

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

    def test_setting_frames(self):
        # 0x12 and 0x36 are inverted on the wire; 0x34/0x3B/0x3D are not.
        self.assertEqual(ctl.speaker_request(True)[7], 0)
        self.assertEqual(ctl.speaker_request(False)[7], 1)
        self.assertEqual(ctl.speaker_get_request()[1:7],
                         bytes([0xAA, 0xDD, 0x13, 0x40, 0x00, 0x00]))
        self.assertEqual(ctl.voice_lock_request(True)[7], 1)
        self.assertEqual(ctl.voice_lock_request(False)[7], 0)
        self.assertEqual(ctl.auto_power_off_request(True)[7], 0)
        self.assertEqual(ctl.auto_power_off_request(False)[7], 1)
        self.assertEqual(ctl.tx_identify_request(True)[7], 1)
        self.assertEqual(ctl.tx_identify_get_request()[1:7],
                         bytes([0xAA, 0xDD, 0x3C, 0x40, 0x00, 0x00]))
        self.assertEqual(ctl.smart_battery_request(True)[7], 1)
        self.assertEqual(ctl.smart_battery_request(False)[7], 0)
        for bad in (0, 1, None, 'on'):
            with self.assertRaises(ValueError):
                ctl.speaker_request(bad)
            with self.assertRaises(ValueError):
                ctl.voice_lock_request(bad)
            with self.assertRaises(ValueError):
                ctl.auto_power_off_request(bad)
            with self.assertRaises(ValueError):
                ctl.tx_identify_request(bad)
            with self.assertRaises(ValueError):
                ctl.smart_battery_request(bad)

    def test_setting_acks_are_accepted(self):
        # Every frame below was captured from the receiver, not synthesised.
        for cmd, frame in ((ctl.SPEAKER_SET, self.ACK_12),
                           (ctl.VOICE_LOCK_SET, self.ACK_34),
                           (ctl.SHUTDOWN_TIME_SET, self.ACK_36),
                           (ctl.TX_IDENTIFY_SET, self.ACK_3B),
                           (ctl.SMART_BATTERY_SET, self.ACK_3D)):
            self.assertIs(ctl.parse_set_ack(frame, cmd), True)
            self.assertEqual(ctl.parse_reply(frame, cmd), b'\x01')
        # VIBE commands answer 00 instead of 01, so they must not be treated as
        # acknowledgements; parse_set_ack refuses them outright.
        for cmd in (ctl.VIBE_TEMPLATE_SET, ctl.VIBE_INFO_SET, ctl.VIBE_KEY_NAME_SET):
            with self.assertRaises(ValueError):
                ctl.parse_set_ack(_pad(bytes([0x55, 0xBB, 0xDD, cmd, 0x80, 0x00, 0x01, 0x00])), cmd)

    def test_flag_and_int_replies(self):
        self.assertIs(ctl.parse_flag_reply(b'\x01'), True)
        self.assertIs(ctl.parse_flag_reply(b'\x00'), False)
        self.assertIs(ctl.parse_flag_reply(b''), None)
        # The monitor state is inverted: 0 means on.
        self.assertIs(ctl.parse_flag_reply(b'\x00', open_value=0), True)
        self.assertIs(ctl.parse_flag_reply(b'\x01', open_value=0), False)
        self.assertIs(ctl.parse_int_reply(b'\x07'), 7)
        self.assertIsNone(ctl.parse_int_reply(b''))


class LarkTemplateTests(unittest.TestCase):
    # Live 0x43 reply: mic mode, both transmitters on template 0, light normal.
    TEMPLATE_CFG = bytes.fromhex('00000000')
    # Live 0x45 reply for slot 0: "WorkBuddy 快捷键", LCtrl+D / Enter, no attr.
    TEMPLATE_0 = bytes.fromhex(
        '00' '576f726b427564647920e5bfabe68db7e994ae' '0000000000000000000000'
        '010700000000' '002800000000')
    # Live 0x47 replies: key names of template 0.
    KEY_0_0 = bytes.fromhex('0000' 'e8afade99fb3e8be93e585a5' + '00' * 18)
    KEY_0_1 = bytes.fromhex('0001' 'e58f91e98081' + '00' * 24)

    def test_name_field_is_thirty_bytes(self):
        field = ctl.encode_name30('WorkBuddy 快捷键')
        self.assertEqual(len(field), 30)
        self.assertEqual(field, self.TEMPLATE_0[1:31])
        self.assertEqual(ctl.decode_name30(field), 'WorkBuddy 快捷键')
        self.assertEqual(ctl.decode_name30(self.KEY_0_0[2:32]), '语音输入')
        self.assertEqual(ctl.decode_name30(self.KEY_0_1[2:32]), '发送')

    def test_name_truncation_never_splits_a_character(self):
        field = ctl.encode_name30('测' * 20)
        self.assertEqual(len(field), 30)
        self.assertEqual(ctl.decode_name30(field), '测' * 10)
        # 31 bytes would end mid character, so the last one has to be dropped.
        mixed = ctl.encode_name30('a' + '测' * 10)
        self.assertEqual(mixed, b'a' + '测'.encode() * 9 + b'\x00\x00')
        self.assertEqual(len(mixed), 30)
        self.assertEqual(ctl.encode_name30(None), bytes(30))
        self.assertEqual(ctl.decode_name30(bytes(30)), '')

    def test_hotkey_round_trip(self):
        self.assertEqual(ctl.encode_hotkey6(['LCtrl', 'D']), b'\x01\x07\x00\x00\x00\x00')
        self.assertEqual(ctl.format_hotkey6(self.TEMPLATE_0[31:37]), 'LCtrl+D')
        self.assertEqual(ctl.format_hotkey6(self.TEMPLATE_0[37:43]), 'ENTER')
        self.assertEqual(ctl.format_hotkey6(bytes(6)), '')
        keys = ['LShift', 'RAlt', 'A', 'F1']
        self.assertEqual(ctl.decode_hotkey6(ctl.encode_hotkey6(keys)), keys)

    def test_hotkey_rejects_bad_input(self):
        for bad in ('LCtrl+D', b'\x01\x07', None, 5):
            with self.assertRaises(ValueError):
                ctl.encode_hotkey6(bad)
        with self.assertRaises(ValueError):
            ctl.encode_hotkey6(['NOPE'])
        with self.assertRaises(ValueError):
            ctl.encode_hotkey6(['A', 'B', 'C', 'D', 'E', 'F'])
        with self.assertRaises(ValueError):
            ctl._hotkey_field(b'\x01' * 7)
        # b'' means "nothing bound" and pads to six zero bytes.
        self.assertEqual(ctl._hotkey_field(b''), bytes(6))
        # A string must not be iterated character by character.
        for text in ('AB', 'LCtrl+D'):
            with self.assertRaises(ValueError):
                ctl._hotkey_field(text)

    def test_modifier_aliases(self):
        # The page accepts CTRL/SHIFT/ALT/WIN and folds them onto the left keys.
        self.assertEqual(ctl.encode_hotkey6(['ctrl', 'A']), ctl.encode_hotkey6(['LCtrl', 'A']))
        self.assertEqual(ctl.encode_hotkey6(['shift', 'A']), ctl.encode_hotkey6(['LShift', 'A']))
        self.assertEqual(ctl.encode_hotkey6(['GUI', 'A']), ctl.encode_hotkey6(['LWin', 'A']))
        self.assertEqual(ctl.decode_hotkey6(ctl.encode_hotkey6(['META', 'F5'])),
                         ['LWin', 'F5'])

    def test_template_config_frame(self):
        packet = ctl.template_set_request(ctl.WORK_MODE_VIBE, 1, 2, ctl.LED_MODE_LOW)
        self.assertEqual(len(packet), 64)
        self.assertEqual(packet[1:7], bytes([0xAA, 0xDD, 0x42, 0x40, 0x00, 0x04]))
        self.assertEqual(packet[7:11], bytes([1, 1, 2, 1]))
        self.assertEqual(ctl.template_get_request()[1:7],
                         bytes([0xAA, 0xDD, 0x43, 0x40, 0x00, 0x00]))
        for args in ((-1, 0, 0, 0), (2, 0, 0, 0), (0, 5, 0, 0), (0, 0, -1, 0),
                     (0, 0, 0, 2), (True, 0, 0, 0)):
            with self.assertRaises(ValueError):
                ctl.template_set_request(*args)

    def test_template_info_frame_shape(self):
        packet = ctl.template_info_set_request(3, 'AB', ['LCtrl', 'A'], ['F1'])
        self.assertEqual(packet[1:7], bytes([0xAA, 0xDD, 0x44, 0x40, 0x00, 43]))
        self.assertEqual(packet[7], 3)
        self.assertEqual(packet[8:38], b'AB' + bytes(28))
        self.assertEqual(packet[38:44], b'\x01\x04\x00\x00\x00\x00')
        self.assertEqual(packet[44:50], b'\x00\x3a\x00\x00\x00\x00')
        wide = ctl.template_info_set_request(3, 'AB', [], [], attr=1)
        self.assertEqual((wide[5] << 8) | wide[6], 44)
        self.assertEqual(wide[50], 1)
        for bad in ((5, 'x'), (-1, 'x')):
            with self.assertRaises(ValueError):
                ctl.template_info_set_request(*bad)
        with self.assertRaises(ValueError):
            ctl.template_info_set_request(0, 'x', attr=256)

    def test_key_name_frames(self):
        packet = ctl.key_name_set_request(2, 1, 'HI')
        self.assertEqual(packet[1:7], bytes([0xAA, 0xDD, 0x46, 0x40, 0x00, 32]))
        self.assertEqual(packet[7], 2)
        self.assertEqual(packet[8], 1)
        self.assertEqual(packet[9:39], b'HI' + bytes(28))
        get = ctl.key_name_get_request(2, 1)
        self.assertEqual(get[1:9], bytes([0xAA, 0xDD, 0x47, 0x40, 0x00, 0x02, 2, 1]))
        for bad in ((5, 0), (0, 2), (0, -1)):
            with self.assertRaises(ValueError):
                ctl.key_name_get_request(*bad)

    def test_vibe_ack_payload_is_zero(self):
        # Live 0x44 reply: 55 bb dd 44 80 00 01 00 - success carries a 00 byte.
        frame = _pad(bytes.fromhex('55bbdd4480000100'))
        self.assertEqual(ctl.parse_reply(frame, ctl.VIBE_INFO_SET),
                         bytes([ctl.VIBE_ACK_PAYLOAD]))
        # That is exactly why a template write cannot use the setting ack parser.
        with self.assertRaises(ValueError):
            ctl.parse_set_ack(frame, ctl.VIBE_INFO_SET)

    def test_parse_live_template_info(self):
        info = ctl.parse_template_info(self.TEMPLATE_0)
        self.assertEqual(info.num, 0)
        self.assertEqual(info.name, 'WorkBuddy 快捷键')
        self.assertEqual(ctl.format_hotkey6(info.work1), 'LCtrl+D')
        self.assertEqual(ctl.format_hotkey6(info.work2), 'ENTER')
        self.assertIsNone(info.attr)
        self.assertFalse(info.is_empty)

    def test_empty_slot_and_mask(self):
        blank = bytes(43)
        self.assertTrue(ctl.parse_template_info(blank).is_empty)
        filled = ctl.parse_template_info(self.TEMPLATE_0)
        self.assertEqual((filled.num, filled.name), (0, 'WorkBuddy 快捷键'))
        self.assertIsNone(ctl.parse_template_info(b'\x00' * 42))
        self.assertEqual(ctl.template_availability(b'\x05'),
                         (True, False, True, False, False))
        self.assertIsNone(ctl.template_availability(b''))
        self.assertIsNone(ctl.template_availability(self.TEMPLATE_0))
        # Only a one byte mask is a mask; a truncated record is not.
        self.assertIsNone(ctl.template_availability(b'\xff' * 40))

    def test_parse_live_key_names(self):
        single = ctl.parse_key_name(self.KEY_0_0)
        double = ctl.parse_key_name(self.KEY_0_1)
        self.assertEqual((single.num, single.key_index, single.name), (0, 0, '语音输入'))
        self.assertEqual((double.num, double.key_index, double.name), (0, 1, '发送'))
        self.assertIsNone(ctl.parse_key_name(self.KEY_0_0[:31]))

    def test_parse_template_config(self):
        cfg = ctl.parse_template_config(self.TEMPLATE_CFG)
        self.assertEqual((cfg.work_mode, cfg.tx1_template, cfg.tx2_template, cfg.led_mode),
                         (0, 0, 0, 0))
        self.assertIsNone(ctl.parse_template_config(b'\x00\x00\x00'))

    def test_parse_reply_frame(self):
        frame = _pad(bytes.fromhex('55bbdd45' '80002b') + self.TEMPLATE_0)
        self.assertEqual(ctl.parse_reply(frame, ctl.VIBE_INFO_GET), self.TEMPLATE_0)
        self.assertIsNone(ctl.parse_reply(frame, ctl.VIBE_TEMPLATE_GET))
        self.assertIsNone(ctl.parse_reply(frame[:6], ctl.VIBE_INFO_GET))
        self.assertIsNone(ctl.parse_reply(b'', ctl.VIBE_INFO_GET))
        self.assertIsNone(ctl.parse_reply(_pad(self.TEMPLATE_0), ctl.VIBE_INFO_GET))
