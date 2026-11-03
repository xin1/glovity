"""Run offline service/codec regressions: python -m pip install ziglang beforehand."""
from pathlib import Path
import subprocess, sys
here=Path(__file__).resolve().parent
main=here.parent
build=main.parent/'build'/'live_tests'
build.mkdir(parents=True, exist_ok=True)
for name,sources in [('playback',['test/test_playback.c','svc_play.c','live_codec.c']),
                     ('live_codec',['test/test_live_codec.c','live_codec.c'])]:
    exe=build/(name+'.exe')
    subprocess.run([sys.executable,'-m','ziglang','cc','-std=c11','-Wall','-Wextra','-Werror',
        '-Wno-unused-parameter','-I',str(here/'stubs'),'-I',str(main),
        *[str(main/s) for s in sources],'-o',str(exe),'-lm'],check=True)
    subprocess.run([str(exe)],check=True)
