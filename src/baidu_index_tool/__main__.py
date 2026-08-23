from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1:
        from .cli import main as cli_main
        return cli_main()
    if sys.platform in ("win32", "darwin"):
        from .webview_gui import main as gui_main
        return gui_main([])
    from .cli import main as cli_main
    return cli_main([])


if __name__ == "__main__":
    raise SystemExit(main())
