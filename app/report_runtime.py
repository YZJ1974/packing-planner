"""Locate an installed report runtime without downloading dependencies."""
from pathlib import Path
import os,sys,shutil


def paths():
    bundle=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies'
    python=os.environ.get('PACKING_REPORT_PYTHON') or (str(bundle/'python/bin/python3') if (bundle/'python/bin/python3').exists() else sys.executable)
    node=os.environ.get('PACKING_REPORT_NODE') or (str(bundle/'node/bin/node') if (bundle/'node/bin/node').exists() else shutil.which('node'))
    modules=os.environ.get('PACKING_REPORT_NODE_MODULES') or (str(bundle/'node/node_modules') if (bundle/'node/node_modules/sharp').exists() else str(Path(__file__).resolve().parents[1]/'node_modules'))
    if not node or not Path(python).exists() or not (Path(modules)/'sharp').exists():raise RuntimeError('Word导出运行环境未就绪，请按软件说明配置文档依赖。')
    return python,node,modules
