#!/bin/zsh
set -eu
DIR=${0:A:h}
if [[ -x "$DIR/.webviewvenv/bin/python" ]]; then
  PYTHON="$DIR/.webviewvenv/bin/python"
else
  PYTHON=${PYTHON:-$(command -v python3)}
fi
export PYTHONPATH="$DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON" -m baidu_index_tool.webview_gui
