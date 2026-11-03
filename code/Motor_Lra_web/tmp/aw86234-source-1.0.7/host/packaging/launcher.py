"""Windowed executable entry point with persistent startup diagnostics."""
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path


def run():
    log_dir = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'AW86234' / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / 'startup.log'
    if sys.stdout is None:
        sys.stdout = open(log_path, 'a', encoding='utf-8', buffering=1)
    if sys.stderr is None:
        sys.stderr = sys.stdout
    print(f'\n[{datetime.now().isoformat(timespec="seconds")}] AW86234 start {sys.argv[1:]}')
    try:
        from awtest.__main__ import main
        result = main()
    except Exception:
        traceback.print_exc()
        result = 1
    if result and '--selfcheck' not in sys.argv and '--cli' not in sys.argv:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, f'程序启动失败。诊断日志：\n{log_path}', 'AW86234 上位机', 0x10)
    return result


if __name__ == '__main__':
    raise SystemExit(run())
