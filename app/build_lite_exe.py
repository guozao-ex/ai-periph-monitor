"""Build the lean two-device edition; never deploy, stop processes or alter autostart."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile

NAME = 'PeripheralLite'
TARGET = '外设精简版-v1.0.2.exe'
FONT_NAME = 'ZCOOLQingKeHuangYou-Regular.ttf'
# Modules that the tray and the desktop widget need at run time, so they are in
# the package even though the lean window never imports them at start-up.
HEAVY_MODULES = ('battery', 'mouse_settings', 'ui_mouse_pages', 'receiver_battery',
                 'shared_readings', 'desktop_widget', 'installer', 'my_devices')


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def archive_names(exe):
    """Entry names inside the onefile archive (PyInstaller's own reader)."""
    from PyInstaller.archive.readers import CArchiveReader
    return {name.replace('\\', '/') for name in CArchiveReader(str(exe)).toc}


def bundled_modules(exe):
    """Module names inside the embedded PYZ, i.e. what the exe can import."""
    from PyInstaller.archive.readers import CArchiveReader
    reader = CArchiveReader(str(exe))
    pyz = next((name for name in reader.toc if name.startswith('PYZ')), None)
    if pyz is None:
        return set()
    return set(reader.open_embedded_archive(pyz).toc)


def build():
    # Fail before building if the environment cannot initialize Tcl/Tk. Otherwise
    # PyInstaller silently excludes tkinter and produces an unusable application.
    import tkinter
    probe = tkinter.Tcl()
    probe.eval('info library')
    source = Path(__file__).resolve().parent
    output = source.parent/'release'
    output.mkdir(exist_ok=True)
    target = output/TARGET
    manifest_path = output/'lite-manifest.json'
    # Never leave an older exe behind that looks like the result of this run.
    for stale in (target, manifest_path):
        if stale.exists():
            try:
                stale.unlink()
            except OSError as exc:
                raise RuntimeError(f'上一个产物还被占用（先关掉正在运行的精简版）：{exc}')
    stage = Path(tempfile.mkdtemp(prefix='peripheral_lite_build_'))
    try:
        for file in source.iterdir():
            if file.suffix in ('.py', '.ico', '.png'):
                shutil.copy2(file, stage/file.name)
        # Only the tray numeral font travels with the lean build; brand_assets and
        # the rest of app/fonts belong to the full edition.
        (stage/'fonts').mkdir(exist_ok=True)
        shutil.copy2(source/'fonts'/FONT_NAME, stage/'fonts'/FONT_NAME)
        shutil.copy2(source/'THIRD_PARTY_NOTICES.txt', stage/'THIRD_PARTY_NOTICES.txt')
        command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                   '--onefile', '--windowed', '--name', NAME,
                   '--icon', 'app_icon.ico', '--add-data', 'app_icon.ico;.',
                   '--add-data', 'app_icon.png;.',
                   '--add-data', 'fonts/%s;fonts' % FONT_NAME,
                   '--add-data', 'THIRD_PARTY_NOTICES.txt;.',
                   '--distpath', str(stage/'dist'), '--workpath', str(stage/'build'),
                   '--specpath', str(stage), 'lite_main.py']
        log_path = output/'build-lite.log'
        with log_path.open('w', encoding='utf8') as log:
            log.write(f'Staging: {stage}\n')
            log.write('Command: %s\n' % subprocess.list2cmdline(command))
            log.flush()
            result = subprocess.run(command, cwd=stage, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f'Build failed. See {log_path}')
        log_text = log_path.read_text(encoding='utf8', errors='replace')
        if 'tkinter installation is broken' in log_text or 'pyi_rth__tkinter.py' not in log_text:
            raise RuntimeError(f'Tcl/Tk was not bundled. See {log_path}')

        exe = stage/'dist'/f'{NAME}.exe'
        # Keep PyInstaller's missing-module list next to the log; it is the only
        # record of what the analysis could not resolve.
        warn = stage/'build'/NAME/f'warn-{NAME}.txt'
        if warn.is_file():
            shutil.copy2(warn, output/'build-lite-warnings.txt')
        names = archive_names(exe)
        for required in ('app_icon.ico', f'fonts/{FONT_NAME}', 'THIRD_PARTY_NOTICES.txt'):
            if required not in names:
                raise RuntimeError(f'{required} is missing from the archive')

        # The frozen exe is --windowed, so the smoke test reports through a file.
        report_path = stage/'selftest.json'
        try:
            run = subprocess.run([str(exe), '--selftest', str(report_path)],
                                 cwd=str(stage), timeout=180,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f'Self-test timed out. See {log_path}')
        if not report_path.is_file():
            raise RuntimeError(f'Self-test produced no report (exit {run.returncode}). '
                               f'See {log_path}')
        report = json.loads(report_path.read_text(encoding='utf-8'))
        if not report.get('ok'):
            raise RuntimeError(f'Self-test failed: {json.dumps(report, ensure_ascii=False)}')
        if not report.get('frozen') or not report.get('tray_font'):
            raise RuntimeError(f'Self-test did not run as a frozen build: {report}')

        shutil.copy2(exe, target)
        modules = bundled_modules(exe)
        manifest = {
            'name': TARGET,
            'entry': 'app/lite_main.py',
            'source_sha256': {name: sha256(source/name)
                              for name in ('lite_main.py', 'win_toast.py', 'build_lite_exe.py')},
            'python': report['python'],
            'tk': report['tk'],
            'bytes': target.stat().st_size,
            'sha256': sha256(target),
            'archive_entries': len(names),
            'bundled_modules': len(modules),
            'bundled_heavy_modules': sorted(set(HEAVY_MODULES) & modules),
            'local_modules': report['local_modules'],
            'unexpected_modules': report['unexpected_modules'],
            'heavy_modules': report['heavy_modules'],
            'window_built': report['window_built'],
            'tray_font': report['tray_font'],
            'child_roles': report['child_roles'],
            'bundled_font': f'fonts/{FONT_NAME}',
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                 encoding='utf-8')
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    print(target)
    print(f'{target.stat().st_size/1048576:.1f} MB  sha256={manifest["sha256"]}')
    print('bundled heavy modules (tray/widget need them): %s'
          % (', '.join(manifest['bundled_heavy_modules']) or 'none'))
    return target


if __name__ == '__main__':
    build()
