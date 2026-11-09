"""LARK mic page tests: pure Tk, a fake session, no HID access."""
import time
import tkinter as tk
from tkinter import ttk
import unittest

import lark_a2_control as ctl
import lark_settings as ls
from ui_lark_pages import LarkSettingsPage
from verify_lark_settings import TEMPLATE_0


def make_state():
    heart = ctl.parse_heart(bytes.fromhex('01005a00000000000000000000000400'))
    templates = {0: ctl.parse_template_info(TEMPLATE_0)}
    for num in range(1, ctl.TEMPLATE_COUNT):
        templates[num] = ctl.parse_template_info(bytes(43))
    return ls.LarkState(heart=heart, config=ctl.TemplateConfig(0, 0, 0, 0),
                        templates=templates,
                        key_names={(0, 0): '语音输入', (0, 1): '发送'},
                        speaker_on=True, voice_locked=0, tx_identify=False,
                        smart_battery=True)


class FakeSession:
    def __init__(self):
        self.state = make_state()
        self.calls = []

    def read_state(self):
        self.calls.append(('read',))
        return self.state

    def _record(self, name, *args):
        self.calls.append((name,) + args)
        return self.state

    def set_channel(self, mode): return self._record('channel', mode)
    def set_noise_level(self, level): return self._record('noise_level', level)
    def set_noise_switch(self, on): return self._record('noise_switch', on)
    def set_volume(self, level): return self._record('volume', level)
    def set_light(self, on): return self._record('light', on)
    def set_speaker(self, on): return self._record('speaker', on)
    def set_voice_lock(self, locked): return self._record('lock', locked)
    def set_auto_power_off(self, on): return self._record('auto_off', on)
    def set_tx_identify(self, on): return self._record('identify', on)
    def set_smart_battery(self, on): return self._record('smart', on)
    def set_template_config(self, *args): return self._record('template_config', *args)
    def save_template(self, num, name, work1, work2): return self._record('save_template', num, name, work1, work2)
    def save_template_with_keys(self, num, name, work1, work2, key0, key1):
        return self._record('save_template_with_keys', num, name, work1, work2, key0, key1)
    def save_key_name(self, num, key, name): return self._record('save_key_name', num, key, name)
    def clear_template(self, num): return self._record('clear_template', num)


class LarkPageTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.session = FakeSession()
        self.messages = []
        self.page = LarkSettingsPage(self.root, session=self.session)
        self.page.render(self.session.state)

    def run_now(self, work, done, message):
        """Execute the queued job inline so tests stay synchronous."""
        self.messages.append(message)
        done(work())

    def widgets(self, cls):
        found = []
        def walk(parent):
            for child in parent.winfo_children():
                if isinstance(child, cls):
                    found.append(child)
                walk(child)
        walk(self.page)
        return found

    def test_render_fills_the_controls_from_state(self):
        self.assertEqual(self.page.v_channel.get(), ctl.CHANNEL_MONO)
        self.assertEqual(self.page.v_noise_level.get(), 0)
        self.assertEqual(float(self.page.scale_volume.get()), 0)
        self.assertTrue(self.page.v_light.get())
        self.assertTrue(self.page.v_speaker.get())
        self.assertFalse(self.page.v_lock.get())
        self.assertTrue(self.page.v_auto_off.get())
        self.assertEqual(self.page.template_fields[0]['name'].get(), 'WorkBuddy 快捷键')
        self.assertEqual(self.page.template_fields[0]['w1'].get(), 'LCtrl+D')
        self.assertEqual(self.page.template_fields[0]['k1'].get(), '语音输入')
        self.assertEqual(self.page.template_fields[1]['name'].get(), '')
        self.assertIn('在线', self.page.lab_status.cget('text'))

    def test_channel_radio_sends_the_selected_mode(self):
        self.page.run = self.run_now
        self.page.v_channel.set(ctl.CHANNEL_STEREO)
        self.page.on_channel()
        self.assertEqual(self.session.calls[-1], ('channel', ctl.CHANNEL_STEREO))

    def test_noise_and_volume_actions(self):
        self.page.run = self.run_now
        self.page.v_noise_level.set(2)
        self.page.on_noise_level()
        self.assertEqual(self.session.calls[-1], ('noise_level', 2))
        self.page.scale_volume.set(4)
        self.page.on_volume()
        self.assertEqual(self.session.calls[-1], ('volume', 4))

    def test_template_save_writes_name_hotkeys_and_key_names(self):
        self.page.run = self.run_now
        fields = self.page.template_fields[1]
        fields['name'].insert(0, 'T2')
        fields['w1'].insert(0, 'Ctrl+A')
        fields['w2'].insert(0, 'F1')
        fields['k1'].insert(0, '单击')
        fields['k2'].insert(0, '双击')
        self.page.on_template_save(1)
        saved = [call for call in self.session.calls if call[0] == 'save_template_with_keys'][-1]
        self.assertEqual(saved[1:3], (1, 'T2'))
        self.assertEqual(saved[3], ctl.encode_hotkey6(['LCtrl', 'A']))
        self.assertEqual(saved[4], ctl.encode_hotkey6(['F1']))
        self.assertEqual(saved[5:], ('单击', '双击'))

    def test_clear_and_settings_actions(self):
        self.page.run = self.run_now
        self.page.on_template_clear(3)
        self.assertEqual(self.session.calls[-1], ('clear_template', 3))
        self.page.v_lock.set(True)
        self.page.on_lock()
        self.assertEqual(self.session.calls[-1], ('lock', True))
        self.page.v_identify.set(True)
        self.page.on_identify()
        self.assertEqual(self.session.calls[-1], ('identify', True))
        self.page.v_smart.set(False)
        self.page.on_smart_battery()
        self.assertEqual(self.session.calls[-1], ('smart', False))
        self.page.v_speaker.set(False)
        self.page.on_speaker()
        self.assertEqual(self.session.calls[-1], ('speaker', False))
        self.assertTrue(any('重启' in text for text in self.messages))

    def test_template_config_action(self):
        self.page.run = self.run_now
        self.page.v_work_mode.set(ctl.WORK_MODE_VIBE)
        self.page.v_tx1.set(2)
        self.page.v_tx2.set(0)
        self.page.v_led_mode.set(ctl.LED_MODE_LOW)
        self.page.on_template_config()
        self.assertEqual(self.session.calls[-1], ('template_config', ctl.WORK_MODE_VIBE, 2, 0, ctl.LED_MODE_LOW))

    def test_busy_disables_controls(self):
        self.page.set_busy(True)
        self.assertTrue(self.page.btn_reload.instate(['disabled']))
        self.assertTrue(self.page.scale_volume.instate(['disabled']))
        self.assertTrue(self.page.template_fields[0]['name'].instate(['disabled']))
        self.page.set_busy(False)
        self.assertFalse(self.page.btn_reload.instate(['disabled']))
        self.assertFalse(self.page.scale_volume.instate(['disabled']))

    def test_real_thread_and_queue_render_the_state(self):
        # No run() patching here: the worker thread, the job queue, poll() and
        # the busy flag all take their real path.
        self.page.on_show()
        deadline = time.time() + 10
        while time.time() < deadline and self.page.busy:
            self.root.update()
            time.sleep(0.02)
        self.root.update()
        self.assertFalse(self.page.busy)
        self.assertIn('在线', self.page.lab_status.cget('text'))
        self.assertEqual(self.page.template_fields[0]['name'].get(), 'WorkBuddy 快捷键')

    def test_failing_action_restores_the_previous_values(self):
        # Feed the error through the real queue branch: the page must snap the
        # radio back to what the device last reported instead of lying.
        self.page.set_busy(True)
        self.page.v_channel.set(ctl.CHANNEL_STEREO)
        self.page.jobs.put((self.page.generation, self.page.render, None, '设备拒绝'))
        self.page.poll()
        self.root.update()
        self.assertEqual(self.page.v_channel.get(), ctl.CHANNEL_MONO)
        self.assertIn('设备拒绝', self.page.message.cget('text'))
        self.assertIn('设备当前值', self.page.message.cget('text'))


if __name__ == '__main__':
    unittest.main()
