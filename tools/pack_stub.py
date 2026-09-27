#!/usr/bin/env python3
"""Add x86_64 JNI stub libs to the real (unmodified-dex) APK.

One compiled stub .so (tools/stubs.c -> libstub.so) is copied under every
lib name present in lib/arm64-v8a/ of the source APK, so System.loadLibrary
succeeds on an x86_64 emulator and every native method resolves.

Lib names that do NOT exist in the arm64 dir are deliberately NOT created:
those loadLibrary() calls fail on a real device too (and are caught there),
so behaviour stays identical.

Usage: pack_stub.py REAL_APK STUB_SO OUT_DIR
"""
import os
import re
import sys
import zipfile

src, stub, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(outdir, exist_ok=True)
dst = os.path.join(outdir, 'btse_stub-unsigned.apk')

libnames = set()
with zipfile.ZipFile(src) as zin:
    for n in zin.namelist():
        m = re.match(r'^lib/arm64-v8a/(.+\.so)$', n)
        if m:
            libnames.add(m.group(1))

with open(stub, 'rb') as f:
    stub_data = f.read()

written = 0
with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, 'w') as zout:
    for item in zin.infolist():
        if item.filename.startswith('lib/x86_64/'):
            continue
        zi = zipfile.ZipInfo(item.filename, date_time=item.date_time)
        zi.compress_type = item.compress_type
        zi.external_attr = item.external_attr
        zi.create_system = item.create_system
        zout.writestr(zi, zin.read(item.filename))
        written += 1
    for ln in sorted(libnames):
        zi = zipfile.ZipInfo('lib/x86_64/' + ln, date_time=(2020, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_STORED
        zi.external_attr = (0o100644 << 16)
        zi.create_system = 3
        zout.writestr(zi, stub_data)
        written += 1

print('PACK_OK entries=%d stub_libs=%d size=%d -> %s'
      % (written, len(libnames), os.path.getsize(dst), dst))
print('stub libs: %s' % ' '.join(sorted(libnames)))
