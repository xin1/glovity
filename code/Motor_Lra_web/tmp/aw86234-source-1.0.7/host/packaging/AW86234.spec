from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

host = Path(SPECPATH).parent
datas = collect_data_files('awtest', includes=['resources/**/*.json'])
a = Analysis(
    [str(Path(SPECPATH) / 'launcher.py')],
    pathex=[str(host)],
    binaries=[], datas=datas,
    hiddenimports=collect_submodules('awtest'),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'scipy', 'pytest', 'IPython', 'notebook', 'PyQt5', 'PyQt6', 'PySide2'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='AW86234-Haptic-Control', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
)
