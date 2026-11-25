"""產生 EXIF 結構損壞的 JPEG 測試樣本（BUG-3 迴歸測試用）。

用法: make_exif_fuzz.py <base.jpg> <thumb.jpg> <輸出目錄>
  base.jpg  建議用 Hamana 自帶的 BaseTextures/marble.jpg（非使用者素材）
  thumb.jpg 小 JPEG，作為 IFD1 內嵌縮圖（ffmpeg -i marble.jpg -vf scale=64:64 thumb.jpg）
輸出 vNN_<名稱>.jpg，v00 為結構正確的對照組。
"""
import os
import struct
import sys

# 固定佈局（相對 TIFF header）
IFD0, EXIF, INTEROP, GPS, IFD1 = 8, 100, 160, 200, 260
D_MAKE, D_DATE, D_EXPO, D_GPSLAT, D_THUMB = 320, 336, 360, 370, 400
HUGE = 0x02110F14          # 實際案例中 ExifIFD 指標的量級（約 34MB）


def build(e='<', thumb=b'', **m):
    """m 為各欄位覆寫值；回傳 TIFF bytes。"""
    g = lambda k, d: m.get(k, d)
    tl = len(thumb)
    out = bytearray(D_THUMB + tl)
    bo = b'II' if e == '<' else b'MM'
    out[0:8] = g('byteorder', bo) + struct.pack(e + 'HI', 42, g('ifd0', IFD0))

    def ifd(at, entries, nxt=0, count=None):
        struct.pack_into(e + 'H', out, at, len(entries) if count is None else count)
        for i, (tag, typ, cnt, val) in enumerate(entries):
            o = at + 2 + 12 * i
            struct.pack_into(e + 'HHI', out, o, tag, typ, cnt)
            if isinstance(val, bytes):
                out[o + 8:o + 12] = val.ljust(4, b'\0')
            elif typ == 3 and cnt == 1:
                struct.pack_into(e + 'HH', out, o + 8, val, 0)
            else:
                struct.pack_into(e + 'I', out, o + 8, val)
        struct.pack_into(e + 'I', out, at + 2 + 12 * len(entries), nxt)

    ifd(IFD0, [(0x010F, 2, g('make_cnt', 6), g('make_off', D_MAKE)),
               (0x0112, 3, 1, 1),
               (0x8769, 4, 1, g('exif', EXIF)),
               (0x8825, 4, 1, g('gps', GPS))],
        nxt=g('ifd1', IFD1), count=g('ifd0_count', None))
    ifd(EXIF, [(0x829A, 5, 1, g('expo_off', D_EXPO)),
               (0x9003, 2, 20, D_DATE),
               (0xA005, 4, 1, g('interop', INTEROP))],
        count=g('exif_count', None))
    ifd(INTEROP, [(0x0001, 2, 4, b'R98\0')])
    ifd(GPS, [(0x0000, 1, 4, b'\x02\x02\x00\x00'),
              (0x0001, 2, 2, b'N\0'),
              (0x0002, 5, 3, g('gpslat_off', D_GPSLAT))])
    ifd(IFD1, [(0x0103, 3, 1, 6),
               (0x0201, 4, 1, g('thumb_off', D_THUMB)),
               (0x0202, 4, 1, g('thumb_len', tl))])
    out[D_MAKE:D_MAKE + 6] = b'Maker\0'
    out[D_DATE:D_DATE + 20] = b'2020:01:01 00:00:00\0'
    struct.pack_into(e + 'II', out, D_EXPO, 1, 100)
    struct.pack_into(e + '6I', out, D_GPSLAT, 35, 1, 0, 1, 0, 1)
    out[D_THUMB:] = thumb
    if 'truncate' in m:
        del out[m['truncate']:]
    return bytes(out)


def strip_app1(jpg):
    assert jpg[:2] == b'\xff\xd8'
    i, out = 2, bytearray(b'\xff\xd8')
    while jpg[i] == 0xFF and jpg[i + 1] != 0xDA:
        L = struct.unpack('>H', jpg[i + 2:i + 4])[0]
        if jpg[i + 1] != 0xE1:
            out += jpg[i:i + 2 + L]
        i += 2 + L
    return bytes(out), jpg[i:]


def main():
    base, thumb_path, outdir = sys.argv[1:4]
    os.makedirs(outdir, exist_ok=True)
    head, body = strip_app1(open(base, 'rb').read())
    th = open(thumb_path, 'rb').read()
    full = len(build(thumb=th))
    cases = [
        ('valid', {}),
        ('exififd_oob', dict(exif=HUGE)),                 # 實際案例
        ('exififd_oob_be', dict(e='>', exif=HUGE)),
        ('exififd_edge', dict(exif=full - 1)),
        ('exififd_count_huge', dict(exif_count=0x7FFF)),
        ('ifd0_oob', dict(ifd0=0x7FFFFFF0)),
        ('ifd0_count_huge', dict(ifd0_count=0x7FFF)),
        ('gps_oob', dict(gps=HUGE)),
        ('gpsval_oob', dict(gpslat_off=HUGE)),
        ('interop_oob', dict(interop=HUGE)),
        ('make_off_oob', dict(make_off=HUGE)),
        ('make_cnt_huge', dict(make_cnt=0x7FFFFFFF)),
        ('expo_off_oob', dict(expo_off=HUGE)),
        ('ifd1_oob', dict(ifd1=HUGE)),
        ('thumb_off_oob', dict(thumb_off=HUGE)),
        ('thumb_len_huge', dict(thumb_len=0x7FFFFFF0)),
        ('exififd_is_ifd0', dict(exif=IFD0)),
        ('truncated_hdr', dict(truncate=12)),
        ('bad_byteorder', dict(byteorder=b'XX')),
    ]
    for i, (name, m) in enumerate(cases):
        tiff = build(thumb=th, **m)
        app1 = b'Exif\0\0' + tiff
        seg = b'\xff\xe1' + struct.pack('>H', len(app1) + 2) + app1
        open(os.path.join(outdir, 'v%02d_%s.jpg' % (i, name)), 'wb').write(head[:2] + seg + head[2:] + body)
    print('wrote %d cases → %s' % (len(cases), outdir))


if __name__ == '__main__':
    main()
