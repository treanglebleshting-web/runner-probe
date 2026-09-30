#!/usr/bin/env python3
"""Parse `uiautomator dump` output, find nodes with a label (text/content-desc), print the center "cx cy".

Usage: python3 uibounds.py /tmp/ui.xml Login [Retry ...]
Prefer clickable nodes; fall back to plain nodes. Exit 1 if none is found.
"""
import re
import sys

f = sys.argv[1]
labels = [s.lower() for s in sys.argv[2:]]
if not f or not labels:
    sys.exit(2)
try:
    data = open(f, errors="replace").read()
except OSError:
    sys.exit(1)

nodes = []
for m in re.finditer(r"<node\b[^>]*?/?>", data):
    attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', m.group(0)))
    if not attrs.get("bounds"):
        continue
    lbl = (attrs.get("text", "") + " " + attrs.get("content-desc", "")).lower()
    if any(l in lbl for l in labels):
        nodes.append(attrs)

for want_clickable in (True, False):
    for attrs in nodes:
        if (attrs.get("clickable") == "true") != want_clickable:
            continue
        n = [int(x) for x in re.findall(r"-?\d+", attrs["bounds"])]
        if len(n) == 4:
            print((n[0] + n[2]) // 2, (n[1] + n[3]) // 2)
            sys.exit(0)
sys.exit(1)
