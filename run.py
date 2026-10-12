#!/usr/bin/env python3
"""Start the vocabulary trainer.

    python run.py                # http://127.0.0.1:8000
    python run.py --port 8080    # custom port
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    parser = argparse.ArgumentParser(description="PDF 英语词汇与听力训练器")
    parser.add_argument("--host", default=None, help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=None, help="监听端口（默认 8000）")
    args = parser.parse_args()

    try:
        import pdfplumber  # noqa: F401
    except Exception:
        print("缺少依赖 pdfplumber。请运行：pip install pdfplumber pypdf")
        print("（或使用项目自带的 run.ps1 / run.bat，它会自动选择合适的 Python）")
        return 2

    from app.server import run
    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
