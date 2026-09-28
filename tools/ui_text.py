#!/usr/bin/env python3
"""Print visible text nodes from a uiautomator dump as a single line."""
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ui.xml"
try:
    x = open(path, encoding="utf-8", errors="replace").read()
except Exception:
    sys.exit(0)
ts = [t for t in re.findall(r'text="([^"]+)"', x) if t.strip()]
print(" | ".join(ts[:16])[:320])
