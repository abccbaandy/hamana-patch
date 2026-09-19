#!/usr/bin/env python3
"""把 DefaultSPIs/ifwebp.spi 的副檔名宣告從 `*.webp` 擴充為
`*.webp;*.jpg;*.jpeg`，讓 Hamana 把 .jpg 也先交給它試。

用途：解決 BUG-2（WebP 內容卻用 .jpg 副檔名 → 讀圖失敗）。
Hamana 依副檔名分派解碼器，不看內容；讓 SPI 多宣告 .jpg 後，
ifwebp 的 IsSupported 會用 magic bytes 判斷：
  - 內容是 WebP → 由它解碼（實測成功，標題顯示 x32）
  - 內容是真 JPEG → 回報不支援，Hamana **自動回退**到內建 libjpeg
                    （實測無退化，標題仍為 x24）

**必要前提**：Hamana.ini 需設 `Respect_libjpeg=0`。
否則內建 libjpeg 優先，SPI 根本不會被詢問，patch 不會生效。

作法：原字串區為
    '*.webp\\0' + 'WebP file (*.webp)\\0' + padding      (共 30 bytes)
在同一塊區域內重排為
    '*.webp;*.jpg;*.jpeg\\0' + 'WebP/JPG\\0'             (共 29 bytes)
並改寫 GetPluginInfo 字串表中 desc 的指標。檔案大小不變，
不動任何程式碼，可用 --revert 還原。

用法：
    python patch_ifwebp.py <ifwebp.spi 路徑>
    python patch_ifwebp.py <ifwebp.spi 路徑> --revert
    python patch_ifwebp.py <ifwebp.spi 路徑> --check
"""
import argparse
import os
import shutil
import struct

OLD_EXT = b'*.webp\x00'
OLD_DESC = b'WebP file (*.webp)\x00'
NEW_EXT = b'*.webp;*.jpg;*.jpeg\x00'
NEW_DESC = b'WebP/JPG\x00'


def parse_sections(d):
    pe = struct.unpack('<I', d[0x3c:0x40])[0]
    if d[pe:pe + 4] != b'PE\x00\x00':
        raise SystemExit('不是有效的 PE 檔')
    nsec = struct.unpack('<H', d[pe + 6:pe + 8])[0]
    optsz = struct.unpack('<H', d[pe + 20:pe + 22])[0]
    magic = struct.unpack('<H', d[pe + 24:pe + 26])[0]
    if magic != 0x10b:
        raise SystemExit('預期 PE32（32-bit）SPI')
    imgbase = struct.unpack('<I', d[pe + 52:pe + 56])[0]
    secs = []
    off = pe + 24 + optsz
    for i in range(nsec):
        e = d[off + i * 40: off + i * 40 + 40]
        name = e[:8].rstrip(b'\x00').decode('latin1')
        vsize, va, rawsz, rawptr = struct.unpack('<IIII', e[8:24])
        secs.append((name, va, vsize, rawptr, rawsz))
    return imgbase, secs


def file_to_va(imgbase, secs, fo):
    for _, va, _, rawptr, rawsz in secs:
        if rawptr <= fo < rawptr + rawsz:
            return imgbase + va + (fo - rawptr)
    raise SystemExit('offset %d 不在任何 section 內' % fo)


def locate(d):
    """回傳 (ext_off, desc_off, region_end)。"""
    pat = OLD_EXT + OLD_DESC
    i = d.find(pat)
    if i < 0:
        return None
    ext_off = i
    desc_off = i + len(OLD_EXT)
    end = desc_off + len(OLD_DESC)
    # 吃掉字串後方的 null padding，擴大可用區
    while end < len(d) and d[end] == 0:
        end += 1
    return ext_off, desc_off, end


def find_ptr(d, va):
    needle = struct.pack('<I', va)
    hits = []
    start = 0
    while True:
        j = d.find(needle, start)
        if j < 0:
            break
        hits.append(j)
        start = j + 1
    return hits


def do_check(path):
    d = open(path, 'rb').read()
    if d.find(NEW_EXT) >= 0:
        print('%s：已套用 patch（宣告 %s）'
              % (path, NEW_EXT.rstrip(b'\x00').decode()))
        return 'patched'
    if locate(d):
        print('%s：原始狀態（僅宣告 *.webp）' % path)
        return 'original'
    print('%s：無法辨識，版本可能不同' % path)
    return 'unknown'


def do_patch(path):
    d = bytearray(open(path, 'rb').read())
    if d.find(NEW_EXT) >= 0:
        print('已經是 patch 過的版本，不需重複執行')
        return
    loc = locate(d)
    if not loc:
        raise SystemExit('找不到預期的字串佈局；此 ifwebp.spi 版本不受支援')
    ext_off, desc_off, region_end = loc
    imgbase, secs = parse_sections(bytes(d))

    old_desc_va = file_to_va(imgbase, secs, desc_off)
    ptrs = find_ptr(bytes(d), old_desc_va)
    if len(ptrs) != 1:
        raise SystemExit('desc 指標應恰好出現 1 次，實際 %d 次' % len(ptrs))
    ptr_off = ptrs[0]

    avail = region_end - ext_off
    need = len(NEW_EXT) + len(NEW_DESC)
    if need > avail:
        raise SystemExit('字串區空間不足：需要 %d，可用 %d' % (need, avail))

    backup = path + '.orig'
    if not os.path.exists(backup):
        shutil.copy(path, backup)
        print('已備份原檔 → %s' % backup)

    new_desc_off = ext_off + len(NEW_EXT)
    d[ext_off:region_end] = NEW_EXT + NEW_DESC + b'\x00' * (avail - need)
    d[ptr_off:ptr_off + 4] = struct.pack(
        '<I', file_to_va(imgbase, secs, new_desc_off))

    open(path, 'wb').write(bytes(d))
    print('patch 完成（檔案大小不變）')
    print('  副檔名宣告 → %s' % NEW_EXT.rstrip(b'\x00').decode())
    print('  desc 指標 %#x → %#x'
          % (old_desc_va, file_to_va(imgbase, secs, new_desc_off)))
    print()
    print('請記得在 Hamana.ini 設定 Respect_libjpeg=0，否則 patch 不生效。')


def do_revert(path):
    backup = path + '.orig'
    if not os.path.exists(backup):
        raise SystemExit('找不到備份檔 %s' % backup)
    shutil.copy(backup, path)
    print('已還原 %s' % path)


def main():
    ap = argparse.ArgumentParser(description='擴充 ifwebp.spi 的副檔名宣告')
    ap.add_argument('spi', help='ifwebp.spi 路徑')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--revert', action='store_true', help='由 .orig 備份還原')
    g.add_argument('--check', action='store_true', help='只檢查目前狀態')
    args = ap.parse_args()

    if not os.path.isfile(args.spi):
        raise SystemExit('檔案不存在：%s' % args.spi)
    if args.check:
        do_check(args.spi)
    elif args.revert:
        do_revert(args.spi)
    else:
        do_patch(args.spi)


if __name__ == '__main__':
    main()
