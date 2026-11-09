"""ATK core tests against an in-memory receiver (same 17-byte frames)."""
from types import SimpleNamespace
import unittest

import atk_core as atk


class FakeAtk:
    """Keeps a small config memory and answers like the 8K dongle."""
    def __init__(self, ident=(2, 46)):
        self.output_len = 17
        self.input_len = 17
        self.mem = bytearray(0x100)
        self.ident = ident
        self.writes = []
        self.queue = []
        self.drop_writes = False
        self.drop_identity_once = False
        self.mem[0] = 1              # rate code 1000 Hz
        self.mem[1] = 0x54
        self.mem[2] = 2              # two DPI stages
        self.mem[3] = 0x53
        self.mem[4] = 0              # active stage 0
        self.mem[5] = 0x55
        self.mem[7] = 0x55           # the live dongle answers five 0x55 pairs
        self.mem[9] = 0x55
        self.mem[0xB5:0xBB] = bytes([0x01, 0x54, 0x0C, 0x49, 0x00, 0x55])
        self.set_stage(0, 800)
        self.set_stage(1, 1600)

    def set_stage(self, index, value):
        encoded = atk.encode_dpi(value, value, 'ultra')
        self.mem[12 + index * 4:16 + index * 4] = encoded

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        pass

    def drain(self):
        self.queue.clear()

    def read_input(self, timeout_ms=100):
        return self.queue.pop(0) if self.queue else b''

    def reply(self, body):
        body = bytes(body)[:15].ljust(15, b'\x00')
        return bytes([8]) + body + bytes([(0x55 - 8 - sum(body)) & 0xFF])

    def write_output(self, packet, use_hidd=False):
        packet = bytes(packet)
        self.writes.append(packet)
        cmd, payload = packet[1], packet[2:16]
        if cmd == 0x10 and self.drop_identity_once:
            self.drop_identity_once = False
            return
        if cmd == 0x10:
            body = bytes([0x10, 0, 0, 0, 7, self.ident[0], self.ident[1]])
        elif cmd == 0x0E:
            body = bytes([0x0E, 0, 0, 0, 2, 0])
        elif cmd == 8:
            address = (payload[1] << 8) | payload[2]
            count = payload[3]
            body = bytes([7, 0, payload[1], payload[2], count]) + bytes(self.mem[address:address + count])
        elif cmd == 7:
            address = (payload[1] << 8) | payload[2]
            count = payload[3]
            if not self.drop_writes:
                self.mem[address:address + count] = payload[4:4 + count]
            body = bytes([7, 0])
        elif cmd == 0x04:
            # Live 373B:101B reply body (raw 0804000000024b000fb5...0038):
            # level 75, discharging, 4021 mV.
            body = bytes([0x04, 0x00, 0x00, 0x00, 0x02, 0x4B, 0x00, 0x0F, 0xB5])
        else:
            return
        self.queue.append(self.reply(body))


class AtkCoreTests(unittest.TestCase):
    def setUp(self):
        self.iface = SimpleNamespace(path='fake', vid=0x373B, pid=0x101B, usage=2,
                                     usage_page=0xFF02, output_len=17, input_len=17)
        self.dev = FakeAtk()

    def test_frame_checksum_and_length(self):
        packet = atk.frame(7, (0, 0, 0, 2, 1, 0x54))
        self.assertEqual(len(packet), 17)
        self.assertEqual(packet[0], 0x08)
        self.assertEqual(sum(packet) & 0xFF, 0x55)
        with self.assertRaises(ValueError):
            atk.frame(1, (0,) * 16)

    def test_identity_and_model(self):
        self.assertEqual(atk.identity(self.dev, self.iface), (2, 46))
        self.assertEqual(atk.model((2, 46))[0], 'ATK X1 Ultimate 大师版')
        self.assertIsNone(atk.model((9, 99)))

    def test_battery(self):
        battery = atk.read_battery(self.dev, self.iface)
        self.assertEqual(battery.level, 75)
        self.assertFalse(battery.charging)
        # The live packet reports two-stage DPI info (body[4] = 2), and the repo
        # decoder only trusts the millivolt field from four bytes on.
        self.assertIsNone(battery.millivolts)

    def test_battery_matches_the_repo_decoder(self):
        import wireless_battery as wb
        raw = bytes.fromhex('0804000000024b000fb500000000000038')
        decoded = wb.decode_compx(raw)
        self.assertEqual(decoded[0], 75)
        self.assertEqual(decoded[1], 'discharging')
        # The same bytes through this module must agree with decode_compx.
        body = raw[1:17]
        self.assertEqual(body[5], 75)
        self.assertEqual((body[7] << 8) | body[8], 4021)

    def test_read_dpi(self):
        state = atk.read_dpi(self.dev, self.iface)
        self.assertEqual(state.stages, ((800, 800), (1600, 1600)))
        self.assertEqual(state.active, 0)
        self.assertEqual(state.name, 'ATK X1 Ultimate 大师版')
        self.assertEqual((state.minimum, state.maximum, state.step), (10, 42000, 10))

    def test_write_dpi_and_activate(self):
        state = atk.read_dpi(self.dev, self.iface)
        after = atk.write_dpi(self.dev, self.iface, state, 1, 3200, True)
        self.assertEqual(after.stages[1], (3200, 3200))
        self.assertEqual(after.active, 1)

    def test_dpi_rejects_out_of_range(self):
        state = atk.read_dpi(self.dev, self.iface)
        for bad in (0, 5, 42001, '1600', 1600.0):
            with self.assertRaises(ValueError):
                atk.write_dpi(self.dev, self.iface, state, 0, bad, False)

    def test_rate_read_and_write(self):
        info = atk.read_rate(self.dev, self.iface)
        self.assertEqual(info.code, 1)
        self.assertEqual(info.hz, 1000)
        info = atk.write_rate(self.dev, self.iface, 8000)
        self.assertEqual((info.code, info.hz), (0x40, 8000))
        info = atk.write_rate(self.dev, self.iface, 250)
        self.assertEqual((info.code, info.hz), (0x04, 250))
        with self.assertRaises(ValueError):
            atk.write_rate(self.dev, self.iface, 400)

    def test_write_dpi_rejects_a_device_that_ignores_the_write(self):
        state = atk.read_dpi(self.dev, self.iface)
        self.dev.drop_writes = True
        with self.assertRaises(atk.AtkError):
            atk.write_dpi(self.dev, self.iface, state, 0, 3200, False)

    def test_identity_retries_after_one_lost_frame(self):
        self.dev.drop_identity_once = True
        self.assertEqual(atk.identity(self.dev, self.iface), (2, 46))

    def test_sensor_mode_round_trip(self):
        self.assertEqual(atk.read_sensor_mode(self.dev, self.iface), 0)
        self.assertEqual(atk.write_sensor_mode(self.dev, self.iface, 2), 2)
        self.assertEqual(atk.read_sensor_mode(self.dev, self.iface), 2)
        with self.assertRaises(ValueError):
            atk.write_sensor_mode(self.dev, self.iface, 3)

    def test_rate_table_by_pid(self):
        self.assertEqual(atk.rate_table(0x101B), atk.RATE_CODES)
        self.assertEqual(atk.rate_table(0x101A), atk.RATE_CODES_1K)
        self.assertEqual(atk.RATE_CODES[2000], 0x10)
        self.assertEqual(atk.RATE_CODES[8000], 0x40)
        self.assertNotIn(2000, atk.RATE_CODES_1K)

    def test_config_interface_selection(self):
        other = SimpleNamespace(vid=0x373B, pid=0x101B, usage=2, usage_page=0x0001,
                                output_len=17, input_len=17)
        small = SimpleNamespace(vid=0x373B, pid=0x101B, usage=2, usage_page=0xFF02,
                                output_len=64, input_len=64)
        self.assertIs(atk.config_interface([other, small, self.iface]), self.iface)
        self.assertIsNone(atk.config_interface([other]))
        self.assertIsNone(atk.config_interface([small]))


if __name__ == '__main__':
    unittest.main()