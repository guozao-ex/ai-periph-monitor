# -*- coding: utf-8 -*-
"""Mic settings page: every LARK A2 control, each one confirmed by a readback.

All device work runs on a worker thread and comes back through a queue, the
same pattern as the mouse page, so the Tk main loop never blocks on HID I/O.
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

import ui_theme as ui
import lark_settings as ls
import lark_a2_control as ctl


class LarkSettingsPage(ttk.Frame):
    def __init__(self, parent, session=None):
        super().__init__(parent)
        self.session = session or ls
        self.state = None
        self.busy = False
        self.jobs = queue.Queue()
        self.generation = 0
        self.template_fields = {}
        self.controls = []

        head = ttk.Frame(self, padding=(24, 22, 24, 16))
        head.pack(fill='x')
        ttk.Label(head, text='麦克风设置', style='H1.TLabel').pack(anchor='w')
        ttk.Label(head, text='猛玛 LARK A2 · 每项改完都会读回设备确认',
                  style='Muted.TLabel').pack(anchor='w', pady=(5, 0))
        bar = ttk.Frame(self, padding=(24, 0, 24, 10))
        bar.pack(fill='x')
        self.btn_reload = ttk.Button(bar, text='重新读取', command=self.on_show)
        self.btn_reload.pack(side='right')
        self.message = ttk.Label(self, text='正在读取…', style='Muted.TLabel', wraplength=650)
        self.message.pack(fill='x', padx=24, pady=(0, 10))
        self.scroll = ui.ScrollFrame(self, content_gap=16)
        self.scroll.pack(fill='both', expand=True, padx=24, pady=(0, 18))
        self.body = self.scroll.body

        self._status_section()
        self._channel_section()
        self._noise_section()
        self._volume_section()
        self._light_section()
        self._template_section()
        self._settings_section()

        self.alive = True
        self.poll_id = None
        self._schedule()
        self.bind('<Destroy>', self.destroyed, add='+')

    # ---------------------------------------------------------------- layout
    def _card(self, title):
        card = ui.Surface(self.body, padding=14)
        card.pack(fill='x')
        ttk.Label(card.body, text=title, style='CardTitle.TLabel').pack(anchor='w')
        body = ttk.Frame(card.body)
        body.pack(fill='x', pady=(8, 0))
        return body

    def _radio_row(self, parent, label, variable, options, command):
        row = ttk.Frame(parent)
        row.pack(fill='x', pady=2)
        ttk.Label(row, text=label, width=10).pack(side='left')
        buttons = []
        for text, value in options:
            button = ttk.Radiobutton(row, text=text, value=value, variable=variable, command=command)
            button.pack(side='left', padx=(0, 10))
            buttons.append(button)
        self.controls.extend(buttons)
        return row

    def _status_section(self):
        body = self._card('设备状态')
        self.lab_status = ttk.Label(body, text='—', style='Muted.TLabel', wraplength=560)
        self.lab_status.pack(anchor='w')

    def _channel_section(self):
        body = self._card('声道模式')
        self.v_channel = tk.IntVar(value=0)
        self._radio_row(body, '模式', self.v_channel,
                        [('单声道', ctl.CHANNEL_MONO),
                         ('立体声', ctl.CHANNEL_STEREO),
                         ('安全音轨', ctl.CHANNEL_SAFE_TRACK)],
                        self.on_channel)

    def _noise_section(self):
        body = self._card('降噪')
        self.v_noise_level = tk.IntVar(value=0)
        self._radio_row(body, '档位', self.v_noise_level,
                        [('关闭', 0), ('一档', 1), ('二档', 2), ('三档', 3)],
                        self.on_noise_level)

    def _volume_section(self):
        body = self._card('接收器音量')
        row = ttk.Frame(body)
        row.pack(fill='x')
        self.lab_volume = ttk.Label(row, text='0', width=3)
        self.lab_volume.pack(side='left')
        self.scale_volume = ttk.Scale(row, from_=0, to=ctl.MAX_VOICE_LEVEL, orient='horizontal',
                                      command=lambda value: self.lab_volume.configure(text=str(int(float(value)))))
        self.scale_volume.pack(side='left', fill='x', expand=True, padx=(8, 8))
        self.btn_volume = ttk.Button(row, text='应用音量', command=self.on_volume)
        self.btn_volume.pack(side='right')
        self.controls.extend([self.btn_volume, self.scale_volume])

    def _light_section(self):
        body = self._card('指示灯')
        self.v_light = tk.BooleanVar(value=True)
        self.cb_light = ttk.Checkbutton(body, text='指示灯亮起', variable=self.v_light, command=self.on_light)
        self.cb_light.pack(anchor='w')
        self.controls.append(self.cb_light)

    def _template_section(self):
        body = self._card('模板（最多 5 套）')
        config = ttk.Frame(body)
        config.pack(fill='x', pady=(0, 8))
        self.v_work_mode = tk.IntVar(value=0)
        self.v_led_mode = tk.IntVar(value=0)
        self.v_tx1 = tk.IntVar(value=0)
        self.v_tx2 = tk.IntVar(value=0)
        ttk.Label(config, text='交互模式').pack(side='left')
        for text, value in (('麦克风', ctl.WORK_MODE_MIC), ('模板', ctl.WORK_MODE_VIBE)):
            button = ttk.Radiobutton(config, text=text, value=value, variable=self.v_work_mode)
            button.pack(side='left', padx=(6, 0))
            self.controls.append(button)
        ttk.Label(config, text='灯光').pack(side='left', padx=(16, 0))
        for text, value in (('正常', ctl.LED_MODE_NORMAL), ('暗', ctl.LED_MODE_LOW)):
            button = ttk.Radiobutton(config, text=text, value=value, variable=self.v_led_mode)
            button.pack(side='left', padx=(6, 0))
            self.controls.append(button)
        for label, variable in (('MIC1 模板', self.v_tx1), ('MIC2 模板', self.v_tx2)):
            ttk.Label(config, text=label).pack(side='left', padx=(16, 4))
            box = ttk.Spinbox(config, from_=0, to=ctl.TEMPLATE_COUNT - 1, width=3, textvariable=variable)
            box.pack(side='left')
            self.controls.append(box)
        apply_config = ttk.Button(config, text='应用配置', command=self.on_template_config)
        apply_config.pack(side='left', padx=(16, 0))
        self.controls.append(apply_config)

        for num in range(ctl.TEMPLATE_COUNT):
            row = ttk.Frame(body)
            row.pack(fill='x', pady=2)
            ttk.Label(row, text='模板 %d' % (num + 1), width=7).pack(side='left')
            fields = {}
            for key, label, width in (('name', '名称', 16), ('w1', '单击', 11), ('w2', '双击', 11),
                                      ('k1', '单击名', 8), ('k2', '双击名', 8)):
                ttk.Label(row, text=label).pack(side='left', padx=(6, 2))
                entry = ttk.Entry(row, width=width)
                entry.pack(side='left')
                fields[key] = entry
                self.controls.append(entry)
            save = ttk.Button(row, text='保存', width=5, command=lambda n=num: self.on_template_save(n))
            save.pack(side='left', padx=(8, 2))
            clear = ttk.Button(row, text='清空', width=5, command=lambda n=num: self.on_template_clear(n))
            clear.pack(side='left')
            self.controls.extend([save, clear])
            self.template_fields[num] = fields
        ttk.Label(body, text='快捷键写法：Ctrl+D、LShift+F1、Enter；留空表示没有绑定。',
                  style='Muted.TLabel').pack(anchor='w', pady=(6, 0))

    def _settings_section(self):
        body = self._card('更多设置')
        self.v_speaker = tk.BooleanVar(value=True)
        self.v_lock = tk.BooleanVar(value=False)
        self.v_auto_off = tk.BooleanVar(value=True)
        self.v_identify = tk.BooleanVar(value=False)
        self.v_smart = tk.BooleanVar(value=True)
        for text, variable, command in (
                ('监听（改完接收器会重启）', self.v_speaker, self.on_speaker),
                ('按键锁定', self.v_lock, self.on_lock),
                ('Mic 自动关机', self.v_auto_off, self.on_auto_off),
                ('查找发射器（闪烁）', self.v_identify, self.on_identify),
                ('智能续航', self.v_smart, self.on_smart_battery)):
            box = ttk.Checkbutton(body, text=text, variable=variable, command=command)
            box.pack(anchor='w')
            self.controls.append(box)

    # ------------------------------------------------------------- job queue
    def destroyed(self, event):
        if event.widget is self:
            self.alive = False
            self.generation += 1
            try:
                self.after_cancel(self.poll_id)
            except tk.TclError:
                pass

    def on_hide(self):
        pass

    def on_show(self):
        if self.busy:
            return
        self.run(self.session.read_state, self.render, '正在读取 LARK A2 …')

    def run(self, work, done, message):
        self.set_busy(True)
        self.message.configure(text=message)
        token = self.generation

        def worker():
            try:
                result, error = work(), None
            except Exception as exc:      # noqa: BLE001 - shown to the user as text
                result, error = None, str(exc)
            self.jobs.put((token, done, result, error))

        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                token, done, result, error = self.jobs.get_nowait()
                if token != self.generation:
                    continue
                self.set_busy(False)
                if error:
                    # Snap the widgets back to what the device actually reports,
                    # otherwise the control the user just flipped would lie.
                    if self.state is not None:
                        self.render(self.state)
                    self.message.configure(text=error + '（界面已恢复为设备当前值）')
                else:
                    try:
                        done(result)
                    except Exception as exc:      # noqa: BLE001
                        self.message.configure(text='界面刷新失败：%s' % exc)
        except queue.Empty:
            pass
        finally:
            self._schedule()

    def _schedule(self):
        """Keep exactly one pending poll timer, even when tests call poll()."""
        if not self.alive:
            return
        try:
            if self.poll_id is not None:
                self.after_cancel(self.poll_id)
            self.poll_id = self.after(100, self.poll)
        except tk.TclError:
            self.alive = False

    def set_busy(self, value):
        self.busy = value
        self.btn_reload.configure(state='disabled' if value else 'normal')
        for widget in getattr(self, 'controls', []):
            try:
                widget.state(['disabled'] if value else ['!disabled'])
            except tk.TclError:
                pass

    # --------------------------------------------------------------- render
    def render(self, state):
        self.state = state
        heart = state.heart
        online = []
        if heart.tx1_online:
            online.append('TX1 %s%%' % (heart.tx1_level if heart.tx1_level is not None else '?'))
        if heart.tx2_online:
            online.append('TX2 %s%%' % (heart.tx2_level if heart.tx2_level is not None else '?'))
        self.lab_status.configure(
            text='在线：%s · 声道 %s · 降噪 %d · 音量 %d · 灯 %s' % (
                ' / '.join(online) or '无发射器',
                ctl.channel_mode_name(heart.voice_mode) or '未知(%d)' % heart.voice_mode,
                heart.noise_level, heart.voice_level, '开' if heart.light_on else '关'))
        self.v_channel.set(heart.voice_mode)
        self.v_noise_level.set(heart.noise_level)
        self.scale_volume.set(heart.voice_level)
        self.lab_volume.configure(text=str(heart.voice_level))
        self.v_light.set(bool(heart.light_on))
        self.v_work_mode.set(state.config.work_mode)
        self.v_led_mode.set(state.config.led_mode)
        self.v_tx1.set(state.config.tx1_template)
        self.v_tx2.set(state.config.tx2_template)
        self.v_speaker.set(bool(state.speaker_on))
        self.v_lock.set(state.voice_locked == 1)
        self.v_auto_off.set(bool(heart.auto_power_off_on))
        self.v_identify.set(bool(state.tx_identify))
        self.v_smart.set(bool(state.smart_battery))
        for num, fields in self.template_fields.items():
            info = state.templates.get(num)
            empty = info is None or info.is_empty
            fields['name'].delete(0, 'end')
            fields['w1'].delete(0, 'end')
            fields['w2'].delete(0, 'end')
            if not empty:
                fields['name'].insert(0, info.name)
                fields['w1'].insert(0, ls.hotkey_text(info.work1))
                fields['w2'].insert(0, ls.hotkey_text(info.work2))
            for key, index in (('k1', 0), ('k2', 1)):
                fields[key].delete(0, 'end')
                name = state.key_names.get((num, index))
                if not empty and name:
                    fields[key].insert(0, name)
        self.message.configure(text='已同步设备状态。')

    # -------------------------------------------------------------- actions
    def _action(self, work, message):
        if self.busy:
            return
        self.run(work, self.render, message)

    def on_channel(self):
        mode = self.v_channel.get()
        self._action(lambda: self.session.set_channel(mode), '正在切换声道模式…')

    def on_noise_level(self):
        level = self.v_noise_level.get()
        self._action(lambda: self.session.set_noise_level(level), '正在设置降噪档位…')

    def on_volume(self):
        level = int(float(self.scale_volume.get()))
        self._action(lambda: self.session.set_volume(level), '正在设置接收器音量…')

    def on_light(self):
        on = bool(self.v_light.get())
        self._action(lambda: self.session.set_light(on), '正在切换指示灯…')

    def on_template_config(self):
        args = (self.v_work_mode.get(), self.v_tx1.get(), self.v_tx2.get(), self.v_led_mode.get())
        self._action(lambda: self.session.set_template_config(*args), '正在写入模板配置…')

    def on_template_save(self, num):
        # Every Tk read happens here on the main thread; the worker only talks
        # to the device, so a render() in the meantime cannot race with it.
        fields = self.template_fields[num]
        name = fields['name'].get().strip()
        key0 = fields['k1'].get().strip()
        key1 = fields['k2'].get().strip()
        work1 = ls.parse_hotkey(fields['w1'].get())
        work2 = ls.parse_hotkey(fields['w2'].get())

        def work():
            return self.session.save_template_with_keys(num, name, work1, work2, key0, key1)
        self._action(work, '正在保存模板 %d …' % (num + 1))

    def on_template_clear(self, num):
        # The app reassigns a transmitter before deleting its template; refuse
        # instead of leaving a microphone pointed at an empty slot.
        state = self.state
        if state is not None and num in (state.config.tx1_template, state.config.tx2_template):
            self.message.configure(
                text='模板 %d 正被 MIC1/MIC2 使用：先把「MIC1 模板 / MIC2 模板」改到别的模板，再清空。'
                     % (num + 1))
            return
        self._action(lambda: self.session.clear_template(num), '正在清空模板 %d …' % (num + 1))

    def on_speaker(self):
        on = bool(self.v_speaker.get())
        self._action(lambda: self.session.set_speaker(on), '正在切换监听，接收器会重启，请稍候…')

    def on_lock(self):
        locked = bool(self.v_lock.get())
        self._action(lambda: self.session.set_voice_lock(locked), '正在切换按键锁…')

    def on_auto_off(self):
        on = bool(self.v_auto_off.get())
        self._action(lambda: self.session.set_auto_power_off(on), '正在设置自动关机…')

    def on_identify(self):
        on = bool(self.v_identify.get())
        self._action(lambda: self.session.set_tx_identify(on), '正在切换查找发射器…')

    def on_smart_battery(self):
        on = bool(self.v_smart.get())
        self._action(lambda: self.session.set_smart_battery(on), '正在切换智能续航…')
