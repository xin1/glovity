# -*- coding: utf-8 -*-
"""pytest 根配置：把 ``host/`` 加入 sys.path，使 ``import awtest`` 直接可用
（无需先 ``pip install -e .``）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
