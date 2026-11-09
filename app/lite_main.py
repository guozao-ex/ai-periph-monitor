# -*- coding: utf-8 -*-
"""精简入口：只带 ATK 烈空 X1 与 猛玛 LARK A2 两个设备的窗口。

用法：
    python app/lite_main.py

导入面刻意收窄：hid_win + atk_core + lark_a2_* + lark_settings + ui_lark_pages
+ ui_theme，不碰 battery.py / mouse_settings.py / ui_mouse_pages.py，
所以打包时其余品牌模块不会被带进来。
"""
import queue
import threading
import tkinter as tk
from tkinter import ttk

import ui_theme as ui
import atk_core
import hid_win as hw
from ui_lark_pages import LarkSettingsPage


class AtkPanel(ttk.Frame):
    """ATK 鼠标面板：电量、DPI 档位、回报率，写操作都读回确认。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.state = None
        self.battery = None
        self.jobs = queue.Queue()
        self.busy = False
        self.alive = True
        self.poll_id = None

        head = ttk.Frame(self, padding=(18, 14, 18, 8))
        head.pack(fill='x')
        ttk.Label(head, text='ATK 烈空 X1', style='H1.TLabel').pack(side='left')
        ttk.Button(head, text='重新读取', command=self.refresh).pack(side='right')

        self.message = ttk.Label(self, text='正在读取…', style='Muted.TLabel', wraplength=760)
        self.message.pack(fill='x', padx=18, pady=(0, 8))

        card = ui.Surface(self, padding=14)
        card.pack(fill='x', padx=18, pady=(0, 10))
        body = card.body
        self.lab_status = ttk.Label(body, text='—', style='Muted.TLabel', wraplength=720)
        self.lab_status.pack(anchor='w')

        card = ui.Surface(self, padding=14)
        card.pack(fill='x', padx=18, pady=(0, 10))
        body = card.body
        ttk.Label(body, text='DPI 档位', style='CardTitle.TLabel').pack(anchor='w')
        self.dpi_rows = ttk.Frame(body)
        self.dpi_rows.pack(fill='x', pady=(6, 6))
        edit = ttk.Frame(body)
        edit.pack(fill='x')
        ttk.Label(edit, text='档位').pack(side='left')
        self.v_stage = tk.IntVar(value=0)
        self.spin_stage = ttk.Spinbox(edit, from_=0, to=0, width=4, textvariable=self.v_stage)
        self.spin_stage.pack(side='left', padx=(6, 12))
        ttk.Label(edit, text='DPI').pack(side='left')
        self.v_dpi = tk.StringVar()
        self.entry_dpi = ttk.Entry(edit, width=8, textvariable=self.v_dpi)
        self.entry_dpi.pack(side='left', padx=(6, 12))
        self.btn_dpi = ttk.Button(edit, text='应用 DPI 并读回', command=self.apply_dpi)
        self.btn_dpi.pack(side='left')

        card = ui.Surface(self, padding=14)
        card.pack(fill='x', padx=18, pady=(0, 10))
        body = card.body
        ttk.Label(body, text='性能设置 · 回报率', style='CardTitle.TLabel').pack(anchor='w')
        row = ttk.Frame(body)
        row.pack(fill='x', pady=(6, 0))
        self.rate_label = ttk.Label(row, text='—', style='Muted.TLabel')
        self.rate_label.pack(side='left')
        self.v_rate = tk.StringVar()
        self.combo_rate = ttk.Combobox(row, textvariable=self.v_rate, state='readonly', width=8)
        self.combo_rate.pack(side='right')
        self.btn_rate = ttk.Button(row, text='应用回报率', command=self.apply_rate)
        self.btn_rate.pack(side='right', padx=(0, 8))

        row = ttk.Frame(body)
        row.pack(fill='x', pady=(8, 0))
        ttk.Label(row, text='传感器采样率').pack(side='left')
        self.v_sensor = tk.StringVar()
        self.combo_sensor = ttk.Combobox(row, textvariable=self.v_sensor, state='readonly', width=20,
                                         values=[atk_core.SENSOR_MODES[k] for k in sorted(atk_core.SENSOR_MODES)])
        self.combo_sensor.pack(side='right')
        self.btn_sensor = ttk.Button(row, text='应用采样率', command=self.apply_sensor)
        self.btn_sensor.pack(side='right', padx=(0, 8))

        self.controls = [self.spin_stage, self.entry_dpi, self.btn_dpi,
                         self.combo_rate, self.btn_rate, self.combo_sensor, self.btn_sensor]
        self._schedule()
        self.bind('<Destroy>', self._destroyed, add='+')

    # ---------------------------------------------------------- job queue
    def _destroyed(self, event):
        if event.widget is self:
            self.alive = False
            try:
                self.after_cancel(self.poll_id)
            except tk.TclError:
                pass

    def _schedule(self):
        if not self.alive:
            return
        try:
            if self.poll_id is not None:
                self.after_cancel(self.poll_id)
            self.poll_id = self.after(100, self._poll)
        except tk.TclError:
            self.alive = False

    def run(self, work, done, message):
        self.set_busy(True)
        self.message.configure(text=message)

        def worker():
            try:
                result, error = work(), None
            except Exception as exc:      # noqa: BLE001 - shown as text
                result, error = None, str(exc)
            self.jobs.put((done, result, error))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        try:
            while True:
                done, result, error = self.jobs.get_nowait()
                self.set_busy(False)
                if error is not None:
                    self.message.configure(text=error)
                else:
                    try:
                        done(result)
                    except Exception as exc:      # noqa: BLE001
                        self.message.configure(text='界面刷新失败：%s' % exc)
        except queue.Empty:
            pass
        finally:
            self._schedule()

    def set_busy(self, value):
        self.busy = value
        for widget in self.controls:
            try:
                widget.state(['disabled'] if value else ['!disabled'])
            except tk.TclError:
                pass

    # ------------------------------------------------------------- reading
    def refresh(self):
        if self.busy:
            return
        self.run(self._read_all, self.render, '正在读取 ATK 接收器…')

    @staticmethod
    def _read_all():
        iface = atk_core.config_interface()
        if iface is None:
            raise atk_core.AtkError('没有找到 ATK 接收器（373B:102F/101A/101B）')
        with hw.HidDevice(iface.path, write=True) as dev:
            ident = atk_core.identity(dev, iface)
            battery = atk_core.read_battery(dev, iface)
            dpi = atk_core.read_dpi(dev, iface, ident)
            rate = atk_core.read_rate(dev, iface)
            sensor = atk_core.read_sensor_mode(dev, iface)
        return {'iface': iface, 'identity': ident, 'battery': battery,
                'dpi': dpi, 'rate': rate, 'sensor': sensor}

    def render(self, snapshot):
        self.snapshot = snapshot
        battery, dpi, rate = snapshot['battery'], snapshot['dpi'], snapshot['rate']
        self.lab_status.configure(
            text='%s · 电量 %d%%%s · 配置层 %d · DPI 档位 %d 个（当前 %d）' % (
                dpi.name, battery.level, '（充电中）' if battery.charging else '',
                dpi.profile, len(dpi.stages), dpi.active + 1))
        for child in self.dpi_rows.winfo_children():
            child.destroy()
        for index, (x, y) in enumerate(dpi.stages):
            text = '档位 %d：%d' % (index + 1, x) if x == y else '档位 %d：%d × %d' % (index + 1, x, y)
            if index == dpi.active:
                text += '（当前）'
            ttk.Label(self.dpi_rows, text=text).pack(anchor='w')
        self.spin_stage.configure(from_=1, to=len(dpi.stages))
        self.v_stage.set(dpi.active + 1)
        self.v_dpi.set(str(dpi.stages[dpi.active][0]))
        hz_list = sorted(rate.table)
        self.combo_rate.configure(values=[str(v) for v in hz_list])
        self.v_rate.set(str(rate.hz) if rate.hz else '未知(0x%02X)' % rate.code)
        self.rate_label.configure(
            text='当前代码 0x%02X%s' % (rate.code, '' if rate.hz else ' · 该代码不在已知表里'))
        sensor = snapshot.get('sensor')
        self.v_sensor.set(atk_core.SENSOR_MODES.get(sensor, '未知（0x%02X）' % sensor if sensor is not None else '未知'))
        self.message.configure(text='已读取。DPI、回报率与采样率写入后都会读回确认。')

    # ------------------------------------------------------------- writing
    def apply_dpi(self):
        if self.busy or not getattr(self, 'snapshot', None):
            return
        try:
            stage = int(self.v_stage.get()) - 1
            value = int(self.v_dpi.get())
        except ValueError:
            self.message.configure(text='档位和 DPI 都要填整数。')
            return
        base = self.snapshot

        def work():
            iface = atk_core.config_interface()
            if iface is None:
                raise atk_core.AtkError('没有找到 ATK 接收器')
            with hw.HidDevice(iface.path, write=True) as dev:
                if atk_core.read_dpi(dev, iface) != base['dpi']:
                    raise atk_core.AtkError('设备配置已变化，请重新读取后再应用')
                state = atk_core.write_dpi(dev, iface, base['dpi'], stage, value, True)
                battery = atk_core.read_battery(dev, iface)
                rate = atk_core.read_rate(dev, iface)
            return {'iface': iface, 'identity': state.identity, 'battery': battery,
                    'dpi': state, 'rate': rate}

        self.run(work, self.render, '正在写入 DPI 并读回…')

    def apply_rate(self):
        if self.busy or not getattr(self, 'snapshot', None):
            return
        try:
            hz = int(self.v_rate.get())
        except ValueError:
            self.message.configure(text='请选择回报率。')
            return
        base = self.snapshot

        def work():
            iface = atk_core.config_interface()
            if iface is None:
                raise atk_core.AtkError('没有找到 ATK 接收器')
            with hw.HidDevice(iface.path, write=True) as dev:
                info = atk_core.write_rate(dev, iface, hz)
                if info.hz != hz:
                    raise atk_core.AtkError('回报率没有写入成功（读回 %s）' % info.hz)
                battery = atk_core.read_battery(dev, iface)
                dpi = atk_core.read_dpi(dev, iface, base['identity'])
            return {'iface': iface, 'identity': base['identity'], 'battery': battery,
                    'dpi': dpi, 'rate': info}

        self.run(work, self.render, '正在写入回报率并读回…')

    def apply_sensor(self):
        if self.busy or not getattr(self, 'snapshot', None):
            return
        name = self.v_sensor.get()
        mode = next((k for k, v in atk_core.SENSOR_MODES.items() if v == name), None)
        if mode is None:
            self.message.configure(text='请选择传感器采样率。')
            return
        base = self.snapshot

        def work():
            iface = atk_core.config_interface()
            if iface is None:
                raise atk_core.AtkError('没有找到 ATK 接收器')
            with hw.HidDevice(iface.path, write=True) as dev:
                if atk_core.read_dpi(dev, iface) != base['dpi']:
                    raise atk_core.AtkError('设备配置已变化，请重新读取后再应用')
                now = atk_core.write_sensor_mode(dev, iface, mode)
                if now != mode:
                    raise atk_core.AtkError('采样率没有写入成功（读回 %s）' % now)
                battery = atk_core.read_battery(dev, iface)
                rate = atk_core.read_rate(dev, iface)
            return {'iface': iface, 'identity': base['identity'], 'battery': battery,
                    'dpi': base['dpi'], 'rate': rate, 'sensor': now}

        self.run(work, self.render, '正在写入采样率并读回…')


class LiteApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('外设精简版 · ATK X1 / LARK A2')
        self.configure(bg=ui.BG)
        ui.select_theme('light')
        ui.configure(self)
        self.book = book = ttk.Notebook(self)
        book.pack(fill='both', expand=True)
        self.atk = AtkPanel(book)
        book.add(self.atk, text='ATK 鼠标')
        self.lark = LarkSettingsPage(book)
        book.add(self.lark, text='麦克风 LARK A2')
        book.bind('<<NotebookTabChanged>>', self._tab_changed)
        self.atk.refresh()

    def _tab_changed(self, _event=None):
        """The LARK page reads the device when it becomes visible."""
        try:
            if self.book.index(self.book.select()) == 1:
                self.lark.on_show()
        except tk.TclError:
            pass

    def destroy(self):
        try:
            self.atk.alive = False
        except Exception:
            pass
        super().destroy()


def main():
    app = LiteApp()
    app.geometry('960x680')
    app.mainloop()


if __name__ == '__main__':
    main()
