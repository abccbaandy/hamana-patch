#!/usr/bin/env python3
"""為 Hamana.exe 開啟 DEP：設 PE optional header DllCharacteristics 的 NX_COMPAT (0x0100)。

只改 1 個 bit（檔案大小不變），--revert 清回。原理與驗證見 docs/attack-surface.md。
Windows 用戶端預設 DEP 政策為 OptIn：exe 沒有 NX_COMPAT 時行程不啟用 DEP。

用法：
    python tools/patch_hamana_nx.py "<Hamana>\\Hamana.exe" [--check | --revert]
"""
import argparse
import struct
import sys

NX_COMPAT = 0x0100
DLLCHAR_OFF = 70          # PE32 optional header 內 DllCharacteristics 位移


def locate(data):
    if data[:2] != b'MZ':
        raise ValueError('not MZ')
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE' + bytes(2):
        raise ValueError('not PE')
    opt = pe + 4 + 20
    if struct.unpack_from('<H', data, opt)[0] != 0x10B:
        raise ValueError('not PE32')
    return opt + DLLCHAR_OFF


def status(path):
    data = open(path, 'rb').read()
    off = locate(data)
    return bool(struct.unpack_from('<H', data, off)[0] & NX_COMPAT)


def apply(path, enable=True):
    data = bytearray(open(path, 'rb').read())
    off = locate(data)
    v = struct.unpack_from('<H', data, off)[0]
    nv = (v | NX_COMPAT) if enable else (v & ~NX_COMPAT)
    if nv == v:
        return False
    struct.pack_into('<H', data, off, nv)
    open(path, 'wb').write(data)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('exe')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--check', action='store_true')
    g.add_argument('--revert', action='store_true')
    a = ap.parse_args()
    if a.check:
        print('NX_COMPAT:', 'ON' if status(a.exe) else 'OFF')
        return 0
    changed = apply(a.exe, enable=not a.revert)
    state = 'OFF' if a.revert else 'ON'
    print(f'NX_COMPAT {state}' + ('' if changed else '（原本就是，未變更）'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
