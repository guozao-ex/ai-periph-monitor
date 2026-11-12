# -*- coding: utf-8 -*-
"""精简入口：只带 ATK 烈空 X1 与 猛玛 LARK A2 两个设备的窗口。

用法：
    python app/lite_main.py

导入面刻意收窄：hid_win + atk_core + lark_a2_* + lark_settings + ui_lark_pages
+ ui_theme（连带 ui_switch_pixels），启动时不碰 battery.py /
mouse_settings.py / ui_mouse_pages.py。注意：打包后托盘与桌面挂件跟主窗口共用
同一个 exe，它们需要 battery.py 等模块，所以那些模块**在包里**——精简指的是
启动时不去 import 它们，`--selftest` 报告里的 local_modules 就是实测口径。
"""
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

# --windowed 打包后没有控制台：sys.stdout / sys.stderr 为 None，任何 print 或
# traceback 输出都会直接抛错，这里统一绑到 devnull。
for _stream in ('stdout', 'stderr'):
    if getattr(sys, _stream, '') is None:
        try:
            setattr(sys, _stream, open(os.devnull, 'w', encoding='utf-8', errors='ignore'))
        except OSError:
            pass

import ui_theme as ui
import atk_core
import hid_win as hw
from ui_lark_pages import LarkSettingsPage

# 精简入口启动时应当出现的本地模块（ui_switch_pixels 由 ui_theme 带入）。
# selftest 不拿它当「已加载清单」，而是当「期望集合」：实际的本地模块靠
# __file__ 落在 app 目录里动态识别，多出任何一个都算失败。入口自身在源码里叫
# lite_main、在 exe 里叫 __main__，两个名字都不参与比对。
LEAN_MODULES = ('atk_core', 'hid_win', 'lark_a2_battery', 'lark_a2_control',
                'lark_settings', 'ui_lark_pages', 'ui_switch_pixels', 'ui_theme')
SELF_NAMES = ('__main__', 'lite_main')
HEAVY_MODULES = ('battery', 'mouse_settings', 'ui_mouse_pages', 'receiver_battery',
                 'shared_readings', 'desktop_widget', 'installer', 'my_devices')
# 子角色路由标记：打包后 tray_common 会用同一个 exe 加这些参数拉起子进程。
# verify_lite_entry 会扫仓库里所有 build_subcommand 调用点，保证没漏。
ROUTE_FLAGS = ('--tray', '--widget', '--ui', '--toast')
WINDOW_TITLE = '外设精简版 · ATK X1 / LARK A2'


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
    def __init__(self, read_devices=True):
        # Must happen before Tk() creates the window: without it Windows treats the
        # process as DPI-unaware and bitmap-stretches the whole window on a scaled
        # display, which is what makes the text look soft next to the full edition.
        ui.prepare_process()
        super().__init__()
        self.title(WINDOW_TITLE)
        self.configure(bg=ui.BG)
        ui.select_theme('light')
        ui.configure(self)
        bar = ttk.Frame(self, padding=(14, 10, 14, 6))
        bar.pack(fill='x')
        self.lab_tray = ttk.Label(bar, text='托盘：未启动', style='Muted.TLabel')
        self.lab_tray.pack(side='right')
        self.btn_tray = ttk.Button(bar, text='启动托盘', command=self.on_tray_toggle)
        self.btn_tray.pack(side='right', padx=(0, 10))
        self.tray_jobs = queue.Queue()
        self.tray_busy = False
        self.alive = True
        self.tray_after = None          # 2 s status poll
        self.tray_poll_after = None     # 300 ms result poll
        self.tray_notice = ''
        self.tray_notice_until = 0.0
        self.tray_after = self.after(300, self.refresh_tray)

        self.book = book = ttk.Notebook(self)
        book.pack(fill='both', expand=True)
        self.atk = AtkPanel(book)
        book.add(self.atk, text='ATK 鼠标')
        self.lark = LarkSettingsPage(book)
        book.add(self.lark, text='麦克风 LARK A2')
        book.bind('<<NotebookTabChanged>>', self._tab_changed)
        if read_devices:
            self.atk.refresh()

    # ------------------------------------------------------------- tray
    @staticmethod
    def _tray_module():
        """Imported lazily so the lean entry still starts without the tray stack."""
        import tray_common as tc
        return tc

    def refresh_tray(self):
        """One 2 s chain: show the notice for a few seconds, then the real state."""
        if not self.alive:
            return
        try:
            running = self._tray_module().is_tray_running()
        except Exception:
            running = False
        if not self.tray_busy:
            self.btn_tray.configure(text='退出托盘' if running else '启动托盘')
        if self.tray_notice and time.time() < self.tray_notice_until:
            self.lab_tray.configure(text=self.tray_notice)
        else:
            self.lab_tray.configure(text='托盘：运行中' if running else '托盘：未启动')
        try:
            if self.tray_after is not None:
                self.after_cancel(self.tray_after)
            self.tray_after = self.after(2000, self.refresh_tray)
        except tk.TclError:
            self.alive = False

    def on_tray_toggle(self):
        if self.tray_busy:
            return
        self.tray_busy = True
        self.btn_tray.configure(state='disabled', text='处理中…')

        def worker():
            try:
                tc = self._tray_module()
                if tc.is_tray_running():
                    tc.post_to_tray(tc.MSG_QUIT)
                    ok = True
                else:
                    ok = tc.start_tray_app()
            except Exception as exc:      # noqa: BLE001
                ok = False
                print('tray error:', exc)
            self.tray_jobs.put(ok)

        threading.Thread(target=worker, daemon=True).start()
        self._poll_tray()

    def _poll_tray(self):
        try:
            ok = self.tray_jobs.get_nowait()
        except queue.Empty:
            self.tray_poll_after = self.after(300, self._poll_tray)
            return
        self.tray_busy = False
        self.btn_tray.configure(state='normal')
        # Keep the outcome on screen for a moment; the 2 s chain must not
        # overwrite it in the same frame.
        self.tray_notice = '托盘：运行中' if ok else '托盘：操作失败，稍后重试'
        self.tray_notice_until = time.time() + (1.5 if ok else 4.0)
        self.lab_tray.configure(text=self.tray_notice)
        # Refresh right away so the button label follows the new state. The
        # chain cancels its own pending timer, so this cannot fork a second one.
        self.refresh_tray()

    def _tab_changed(self, _event=None):
        """The LARK page reads the device when it becomes visible."""
        try:
            if self.book.index(self.book.select()) == 1:
                self.lark.on_show()
        except tk.TclError:
            pass

    def destroy(self):
        self.alive = False
        try:
            self.atk.alive = False
        except Exception:
            pass
        for attr in ('tray_after', 'tray_poll_after'):
            after_id = getattr(self, attr, None)
            if after_id is not None:
                try:
                    self.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        super().destroy()


def _local_modules():
    """Local modules already imported: everything whose file sits in this dir.

    A whitelist alone would only catch a shrinking import surface; this catches
    any new brand module pulled in at import time as well.
    """
    appdir = os.path.dirname(os.path.abspath(__file__))
    stdlib = set(sys.stdlib_module_names)
    names = []
    for name, module in list(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if not path or os.path.dirname(os.path.abspath(path)) != appdir:
            continue
        # In a frozen build the standard library is unpacked into the same
        # directory as the app modules, so the location alone cannot tell them
        # apart - the stdlib name set can.
        if name.split('.')[0] in stdlib:
            continue
        names.append(name)
    return sorted(names)


def _dpi_awareness():
    """0 unaware, 1 system, 2 per-monitor; 0 is what blurs the window."""
    try:
        import ctypes
        value = ctypes.c_int()
        ctypes.WinDLL('shcore').GetProcessDpiAwareness(None, ctypes.byref(value))
        return value.value
    except Exception:      # noqa: BLE001 - Windows before 8.1
        return -1


def selftest(report_path=None):
    """打包后的冒烟自检：导入面、窗口能否搭起、内置字体、子角色、设备枚举。

    返回 0 表示精简目标和资源都成立；设备读不到不算失败（无线设备会休眠）。
    """
    local = _local_modules()
    report = {
        'frozen': bool(getattr(sys, 'frozen', False)),
        'executable': sys.executable,
        'python': sys.version.split()[0],
        'tk': tk.TkVersion,
        'local_modules': local,
        'unexpected_modules': sorted(set(local) - set(LEAN_MODULES) - set(SELF_NAMES)),
        'missing_modules': sorted(set(LEAN_MODULES) - set(local)),
        'heavy_modules': sorted(m for m in HEAVY_MODULES if m in sys.modules),
    }
    # Build the real window once (without touching the device): this is what
    # catches a heavy import added to LiteApp.__init__ later on.
    try:
        window = LiteApp(read_devices=False)
        window.update_idletasks()
        report['window_built'] = True
        report['window_title'] = window.title()
        # A DPI-unaware process gets its whole window bitmap-stretched on a
        # scaled display, which is exactly what makes the text look soft.
        report['dpi_awareness'] = _dpi_awareness()
        report['tk_scaling'] = float(window.tk.call('tk', 'scaling'))
        report['window_px'] = [window.winfo_width(), window.winfo_height()]
        window.destroy()
    except Exception as exc:      # noqa: BLE001 - reported as text
        report['window_built'] = False
        report['window_error'] = str(exc)
    report['heavy_modules_after_window'] = sorted(m for m in HEAVY_MODULES if m in sys.modules)
    try:
        import tray_font
        report['tray_font'] = bool(any(tray_font.digit_mask('100', 16)))
    except Exception as exc:      # noqa: BLE001 - reported as text
        report['tray_font'] = False
        report['tray_font_error'] = str(exc)
    try:
        iface = atk_core.config_interface()
        report['atk_interface'] = None if iface is None else iface.path
    except Exception as exc:      # noqa: BLE001
        report['atk_error'] = str(exc)
    try:
        import lark_settings
        iface = lark_settings.find_interface()
        report['lark_interface'] = None if iface is None else iface.path
    except Exception as exc:      # noqa: BLE001
        report['lark_error'] = str(exc)
    roles = {}
    for name in ('tray_app', 'desktop_widget', 'win_toast'):
        try:
            __import__(name)      # proves the child roles are bundled
            roles[name] = True
        except Exception as exc:      # noqa: BLE001
            roles[name] = str(exc)
    report['child_roles'] = roles
    report['ok'] = bool(
        not report['heavy_modules']
        and not report['heavy_modules_after_window']
        and not report['unexpected_modules']
        and report['window_built']
        and report.get('window_title') == WINDOW_TITLE
        and report.get('dpi_awareness') != 0      # 0 = unaware -> stretched text
        and report['tray_font']
        and all(v is True for v in roles.values()))
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if report_path:
        try:
            with open(report_path, 'w', encoding='utf-8') as fh:
                fh.write(text + '\n')
        except OSError as exc:
            print('selftest report not written: %s' % exc)
            return 2
    print(text)
    return 0 if report['ok'] else 1


def child_argv(argv, flag):
    """Drop one routing flag; every other argument belongs to the child role."""
    out = list(argv)
    try:
        out.remove(flag)
    except ValueError:
        pass
    return out


def activate_existing(title):
    """Bring the window with this exact title forward; True when one exists."""
    try:
        import ctypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.FindWindowW.restype = ctypes.c_void_p
        user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        handle = ctypes.c_void_p(hwnd)
        # SW_RESTORE only when minimized: on a maximized window it would undo
        # the maximized state and shrink the window the user left there.
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, 9)
        if not user32.SetForegroundWindow(handle):
            user32.BringWindowToTop(handle)     # foreground lock fallback
        return True
    except Exception:      # noqa: BLE001 - never trade a window for an icon
        return False


def main():
    # Frozen builds route every child role to this same executable (tray_common
    # builds [sys.executable, "--tray"] / "--widget" / "--ui" / "--toast"), so
    # each of those must be dispatched here instead of opening a second window.
    args = sys.argv[1:]
    if '--selftest' in args:
        rest = args[args.index('--selftest') + 1:]
        # --selftest 后面只接受一个不以 - 开头的路径；其余一律当标志位忽略。
        report_path = rest[0] if rest and not rest[0].startswith('-') else None
        return selftest(report_path)
    if '--tray' in args:
        import tray_app
        # tray_app's parser only knows --debug/--status/--quit; leaving --tray in
        # argv makes argparse exit with "unrecognized arguments" instead of
        # starting the tray.
        sys.argv = child_argv(sys.argv, '--tray')
        return tray_app.main()
    if '--widget' in args:
        import desktop_widget
        sys.argv = child_argv(sys.argv, '--widget')
        return int(desktop_widget.run_widget() or 0)
    if '--toast' in args:
        import win_toast
        return win_toast.main(child_argv(sys.argv, '--toast')[1:])
    # --ui (tray menu "open interface") and a plain double click both mean: show
    # the lean window, one instance only.
    if activate_existing(WINDOW_TITLE):
        return 0
    app = LiteApp()
    factor = ui.scale(app)
    width = min(round(960 * factor), app.winfo_screenwidth() - 80)
    height = min(round(680 * factor), app.winfo_screenheight() - 100)
    app.geometry('%dx%d' % (width, height))
    app.mainloop()
    return 0


if __name__ == '__main__':
    sys.exit(main())
