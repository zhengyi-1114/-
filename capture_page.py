#!/usr/bin/env python3
"""可复用入口：懒加载滚屏截取网页长图。

用法:
  python capture_page.py "https://example.com" -o out.png
  python -m screen_translator.web_capture "https://example.com" -o out.png
"""

from screen_translator.web_capture import main

if __name__ == "__main__":
    raise SystemExit(main())
