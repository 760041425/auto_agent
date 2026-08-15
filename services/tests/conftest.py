"""services/tests 共享 pytest 配置。

确保项目根目录在 sys.path 最前，避免 site-packages/scripts（uniception 等包安装）
遮蔽本地 scripts/ 目录，导致 ``from scripts import benchmark_localizers`` 失败。
"""
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT in sys.path:
    sys.path.remove(_REPO_ROOT)
sys.path.insert(0, _REPO_ROOT)
