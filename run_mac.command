#!/bin/bash
set -u
cd "$(dirname "$0")"

LOG_FILE="$(pwd)/packing_planner.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "=========================================="
echo " 包装与集装箱规划系统 V5.8.28 - Mac 启动器"
echo "=========================================="
echo "工作目录: $(pwd)"
echo "启动日志: $LOG_FILE"

# SQLite 数据库所在目录必须存在。ZIP 通常不会保留空目录，因此每次启动都主动创建。
mkdir -p data
if [ ! -w "." ]; then
  echo ""
  echo "[错误] 当前程序目录不可写，SQLite 无法创建数据库。"
  echo "请把整个程序文件夹移动到“下载”或“文稿”等可写目录后再运行。"
  read -r -p "按回车键退出..."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo ""
  echo "[错误] 未找到 python3。请先安装 Python 3.10 或更高版本。"
  echo "可在终端执行: python3 --version"
  echo ""
  read -r -p "按回车键退出..."
  exit 1
fi

PY_VER=$(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
echo "Python: $PY_VER"

if [ ! -d ".venv" ]; then
  echo "[1/4] 第一次运行：创建独立 Python 环境..."
  if ! python3 -m venv .venv; then
    echo "[错误] 创建 .venv 失败。"
    read -r -p "按回车键退出..."
    exit 1
  fi
else
  echo "[1/4] Python 环境已存在。"
fi

source .venv/bin/activate

echo "[2/4] 检查程序依赖..."
REQ_HASH=$(python - <<'PYHASH'
from pathlib import Path
import hashlib
p=Path('requirements.txt')
print(hashlib.sha256(p.read_bytes()).hexdigest())
PYHASH
)
INSTALLED_HASH=$(cat .venv/.packing_requirements_hash 2>/dev/null || true)
NEED_INSTALL=0
if [ "$REQ_HASH" != "$INSTALLED_HASH" ]; then NEED_INSTALL=1; fi
if ! python - <<'PYCHECK' >/dev/null 2>&1
import fastapi,uvicorn,jinja2,multipart,docx,httpx
PYCHECK
then NEED_INSTALL=1; fi
if [ "$NEED_INSTALL" -eq 1 ]; then
  echo "依赖首次安装或版本有变化，需要联网安装一次..."
  if ! python -m pip install -r requirements.txt; then
    echo ""
    echo "[错误] Python 依赖安装失败。首次安装/依赖升级需要网络。"
    echo "已成功安装过且 requirements.txt 未变化时，后续启动不会重复联网安装。"
    echo "请查看 packing_planner.log。"
    read -r -p "按回车键退出..."
    exit 1
  fi
  echo "$REQ_HASH" > .venv/.packing_requirements_hash
else
  echo "依赖已就绪，不执行 pip 联网检查。"
fi

echo "[3/4] 启动本地服务..."
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > uvicorn.log 2>&1 &
SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

READY=0
for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:8000/health" >/dev/null 2>&1; then
    READY=1
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

if [ "$READY" -ne 1 ]; then
  echo ""
  echo "[错误] 本地服务没有成功启动。"
  echo "---- uvicorn.log ----"
  cat uvicorn.log 2>/dev/null || true
  echo "---------------------"
  echo "请把 packing_planner.log 或 uvicorn.log 发给我。"
  read -r -p "按回车键退出..."
  exit 1
fi

echo "[4/4] 服务已就绪：http://127.0.0.1:8000"
echo "正在打开浏览器..."
open "http://127.0.0.1:8000"
echo ""
echo "程序正在运行。请不要关闭这个终端窗口。"
echo "要停止程序：在这里按 Control + C。"
echo ""

wait "$SERVER_PID"
