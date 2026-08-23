import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from baidu_index_tool.webview_gui import main

raise SystemExit(main())
