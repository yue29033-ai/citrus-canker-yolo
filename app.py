#!/usr/bin/env python3
"""Start the local workbench. No public listener, account, or cloud upload."""

import argparse
from canker_workbench.server import serve


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="柑橘病斑本地检测与复核工作台")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="在默认浏览器打开本地页面")
    args = parser.parse_args()
    serve(port=args.port, open_browser=args.open)
