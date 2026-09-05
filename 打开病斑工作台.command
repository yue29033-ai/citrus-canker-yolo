#!/bin/zsh
# Reuse an existing environment. Never install or update dependencies here.
cd "$(dirname "$0")" || exit 1
if [[ -n "${CANKER_PYTHON:-}" && -x "$CANKER_PYTHON" ]]; then
  task_python="$CANKER_PYTHON"
elif [[ -x ".venv/bin/python" ]]; then
  task_python="$PWD/.venv/bin/python"
elif [[ -x "/opt/miniconda3/bin/python" ]]; then
  task_python="/opt/miniconda3/bin/python"
else
  task_python="$(command -v python3)"
fi
"$task_python" app.py --open
task_exit=$?
if [[ "$task_exit" -ne 0 ]]; then
  echo "启动未完成。若工作台已经打开，可访问 http://127.0.0.1:8765；其他问题请查看 WORKBENCH.md。"
  read "?按回车关闭此窗口。"
fi
