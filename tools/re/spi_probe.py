#!/usr/bin/env python3
"""直接呼叫 Susie plugin（.spi）：列 GetPluginInfo，對檔案跑 IsSupported / GetPicture（只輸出尺寸與 bpp）。

必須用 32-bit Python 執行（SPI 是 32-bit DLL）：
    UV_PYTHON_INSTALL_DIR=<非 AppData 的工具目錄> uv python install cpython-3.12-windows-x86
    <32-bit python> -I tools/re/spi_probe.py <plugin.spi> [檔案 ...]
注意：plugin 會讀自己目錄下的設定（例如 ifgdip.ini），所以要指沙箱裡的那份。
輸出不含檔名以外的內容；檔名只取 basename。
"""
import ctypes as C
import ctypes.wintypes as W
import os
import struct
import sys

if struct.calcsize('P') != 4:
    sys.exit('需要 32-bit Python')

k32 = C.WinDLL('kernel32')
k32.LocalLock.restype = C.c_void_p
k32.LocalLock.argtypes = [W.HLOCAL]
k32.LocalUnlock.argtypes = [W.HLOCAL]
k32.LocalFree.argtypes = [W.HLOCAL]


def main():
    spi = C.WinDLL(os.path.abspath(sys.argv[1]))
    buf = C.create_string_buffer(512)
    print('GetPluginInfo:')
    for i in range(32):
        n = spi.GetPluginInfo(i, buf, 512)
        if n <= 0:
            break
        print('  [%d] %s' % (i, buf.value.decode('mbcs', 'replace')))
    for f in sys.argv[2:]:
        name = os.path.basename(f)
        hdr = open(f, 'rb').read(2048).ljust(2048, b'\0')
        hbuf = C.create_string_buffer(hdr, 2048)
        fa = os.path.abspath(f).encode('mbcs')
        sup = spi.IsSupported(C.c_char_p(fa), C.cast(hbuf, C.c_void_p))
        info, bm = W.HLOCAL(), W.HLOCAL()
        rc = spi.GetPicture(C.c_char_p(fa), C.c_long(0), C.c_uint(0), C.byref(info), C.byref(bm), None, C.c_long(0))
        dim = '-'
        if rc == 0 and info.value:
            p = k32.LocalLock(info)
            w, h, _, bpp = struct.unpack('<iiHH', C.string_at(p + 4, 12))
            dim = '%dx%dx%d' % (w, abs(h), bpp)
            k32.LocalUnlock(info)
        for hnd in (info, bm):
            if hnd.value:
                k32.LocalFree(hnd)
        print('%-24s IsSupported=%d GetPicture=%d %s' % (name, sup, rc, dim))


if __name__ == '__main__':
    main()
