#!/usr/bin/env python3
"""Fill base_template.xml placeholders from env (GitHub secrets) -> out file.

Usage: fill_prefs.py TEMPLATE OUT
Required env: BTSE_LOGIN_TOKEN, BTSE_REFRESH_TOKEN, BTSE_USER, BTSE_EMAIL
"""
import os
import sys

src, dst = sys.argv[1], sys.argv[2]
tpl = open(src).read()
pairs = (
    ("__LOGIN_TOKEN__", "BTSE_LOGIN_TOKEN"),
    ("__REFRESH_TOKEN__", "BTSE_REFRESH_TOKEN"),
    ("__USERNAME__", "BTSE_USER"),
    ("__LOGIN_EMAIL__", "BTSE_EMAIL"),
)
for key, env in pairs:
    val = os.environ.get(env)
    if not val:
        sys.exit("missing env: " + env)
    tpl = tpl.replace(key, val)
left = [k for k, _ in pairs if k in tpl]
if left:
    sys.exit("unreplaced placeholders: %s" % left)
with open(dst, "w") as f:
    f.write(tpl)
print("FILL_OK bytes=%d" % len(tpl))
