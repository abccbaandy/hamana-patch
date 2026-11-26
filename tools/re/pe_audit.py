#!/usr/bin/env python3
"""PE 安全旗標 / 匯入 / 內嵌函式庫指紋盤點。

用法：
    .venv/Scripts/python.exe tools/re/pe_audit.py <file_or_dir> [...] [--imports] [--root PREFIX]

輸出每個 PE（.exe/.dll/.spi）的：
  machine、TimeDateStamp、DYNAMIC_BASE(ASLR)、NX_COMPAT(DEP)、NO_SEH、GUARD_CF、
  relocs（有無 .reloc，決定 ASLR 能否事後開）、
  GS（LoadConfig SecurityCookie；無 LoadConfig 時以 __security_check_cookie 位元組樣式判斷，標 Y(chk)）、
  SafeSEH（LoadConfig SEHandlerTable/Count；'-' = 無 LoadConfig = 無 SafeSEH）、內嵌函式庫版本字串。
--root 會把路徑前綴替換成 <H>，避免輸出含本機安裝路徑。
"""
import argparse
import os
import re
import sys

import pefile

FLAGS = {
    'ASLR': 0x0040,      # IMAGE_DLLCHARACTERISTICS_DYNAMIC_BASE
    'DEP': 0x0100,       # NX_COMPAT
    'NO_SEH': 0x0400,
    'CFG': 0x4000,       # GUARD_CF
}


def _b(hexstr):
    return re.escape(bytes.fromhex(hexstr))


# __security_check_cookie：cmp ecx,[cookie]（3B 0D imm32）; jne +1/+2（75 01|02）; [rep] ret（F3 C3 | C3）
GS_CHECK = re.compile(_b('3b0d') + b'.{4}' + _b('75') + b'(?:' + _b('01') + b'|' + _b('02') + b')'
                      + b'(?:' + _b('f3') + b')?' + _b('c3'), re.S)
NUL = bytes(1)

# 內嵌函式庫指紋：(名稱, regex on bytes)
FINGERPRINTS = [
    ('zlib-inflate', rb'inflate \d\.\d[\d.]*'),
    ('zlib-deflate', rb'deflate \d\.\d[\d.]*'),
    ('minizip', rb'minizip[ -~]{0,60}'),
    ('libjpeg', rb'Copyright \(C\) \d{4}(?:-\d{4})?, Thomas G\. Lane'),
    ('libpng', rb'libpng[ -~]{0,40}'),
    ('libtiff', rb'LIBTIFF[ -~]{0,40}'),
    ('giflib', rb'giflib[ -~]{0,20}'),
    ('libwebp', rb'libwebp[ -~]{0,30}'),
    ('ghostscript', rb'[Gg]hostscript[ -~]{0,40}'),
    ('gdiplus', rb'(?i:gdiplus\.dll)'),
    ('windowscodecs', rb'(?i:windowscodecs\.dll)'),
    ('7-zip', rb'7-Zip[ -~]{0,40}'),
    ('unrar', rb'(?i:unrar)[ -~]{0,30}'),
    ('msvc-crt', rb'(?i:msvc[pr]\d{2,3}d?\.dll)'),
    ('d3dx', rb'(?i:d3dx9_\d+\.dll)'),
]

WIDE = re.compile(rb'(?:[ -~]' + re.escape(NUL) + rb'){4,}')


def utf16_ascii(data):
    """抽出 UTF-16LE 的 ASCII 字串，讓 regex 也能比對寬字串。"""
    return NUL.join(m.group(0)[::2] for m in WIDE.finditer(data))


def audit(path, show_imports, root):
    shown = path.replace(root, '<H>') if root else path
    try:
        pe = pefile.PE(path, fast_load=True)
        pe.parse_data_directories(directories=[
            pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'],
            pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_LOAD_CONFIG'],
            pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_BASERELOC'],
        ])
    except pefile.PEFormatError as e:
        print(f'{shown}: not PE ({e})')
        return
    data = open(path, 'rb').read()
    oh = pe.OPTIONAL_HEADER
    dc = oh.DllCharacteristics
    flags = ' '.join(f'{k}={"Y" if dc & v else "n"}' for k, v in FLAGS.items())
    relocs = 'Y' if getattr(pe, 'DIRECTORY_ENTRY_BASERELOC', None) else 'n'
    gs = 'Y(chk)' if GS_CHECK.search(data) else 'n'
    safeseh = '-'
    lc = getattr(pe, 'DIRECTORY_ENTRY_LOAD_CONFIG', None)
    if lc:
        s = lc.struct
        if getattr(s, 'SecurityCookie', 0):
            gs = 'Y'
        cnt = getattr(s, 'SEHandlerCount', 0)
        safeseh = f'Y({cnt})' if getattr(s, 'SEHandlerTable', 0) else 'n'
    print(f'{shown}')
    print(f'  machine=0x{pe.FILE_HEADER.Machine:x} ts={pe.FILE_HEADER.TimeDateStamp:#x} '
          f'base=0x{oh.ImageBase:x} {flags} relocs={relocs} GS={gs} SafeSEH={safeseh}')

    blob = data + NUL + utf16_ascii(data)
    hits = {}
    for name, rx in FINGERPRINTS:
        for m in re.finditer(rx, blob):
            s = m.group(0).decode('ascii', 'replace').strip()
            hits.setdefault(name, set()).add(s[:60])
    for name, vals in hits.items():
        vs = sorted(vals)
        more = f' (+{len(vs) - 4})' if len(vs) > 4 else ''
        print(f'  lib {name}: {" | ".join(vs[:4])}{more}')

    if show_imports and hasattr(pe, 'DIRECTORY_ENTRY_IMPORT'):
        for imp in pe.DIRECTORY_ENTRY_IMPORT:
            names = [i.name.decode() if i.name else f'#{i.ordinal}' for i in imp.imports]
            print(f'  import {imp.dll.decode()}: {len(names)} '
                  f'[{", ".join(names[:12])}{" ..." if len(names) > 12 else ""}]')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--imports', action='store_true')
    ap.add_argument('--root', default='')
    a = ap.parse_args()
    for p in a.paths:
        if os.path.isdir(p):
            for dp, _, fs in os.walk(p):
                for f in sorted(fs):
                    if f.lower().endswith(('.exe', '.dll', '.spi')):
                        audit(os.path.join(dp, f), a.imports, a.root)
        else:
            audit(p, a.imports, a.root)


if __name__ == '__main__':
    sys.exit(main())
