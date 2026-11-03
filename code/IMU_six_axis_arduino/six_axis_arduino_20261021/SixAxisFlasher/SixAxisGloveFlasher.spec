# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['D:/Desktop/six_axis_arduino_20261021/SixAxisFlasher/six_axis_flasher.py'],
    pathex=[],
    binaries=[],
    datas=[('D:/Desktop/six_axis_arduino_20261021/six_axis_arduino_2files_20261021', 'firmware')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SixAxisGloveFlasher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
