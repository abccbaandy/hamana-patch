#!/usr/bin/env python3
"""Hamana 2006.06.19 素材診斷 / 修復工具（純標準庫，無外部依賴）。

處理兩個已驗證的 Hamana bug（根因與實測見 docs/bugs.md）：

  BUG-1 ZIP_ZERO_ENTRY
    Hamana 的 zip 走內建 zlib 1.1.4 常式（非 ax7z.spi）。壓縮檔內若含
    size==0 的 entry，開啟後數秒背景預讀觸及該 entry 即 WerFault 崩潰。
    同一個 0-byte 檔案解壓成資料夾則不崩潰 —— 崩潰路徑是 zip 專屬。
    改名 .cbz 無效（cbz 同樣走內建 routine）。

  BUG-2 EXT_MISMATCH
    Hamana 依「副檔名」而非檔案內容分派解碼器。副檔名 .jpg 一律送進內建
    libjpeg，內容若是 WebP 則 libjpeg 回報
    "Not a JPEG file: starts with 0x52 0x49" → 讀圖失敗。
    Respect_libjpeg=0 不能解決（已實測）。唯一解是正名成 .webp，
    交給 DefaultSPIs/ifwebp.spi 處理。

  BUG-3 EXIF_OOB
    JPEG 的 EXIF（APP1）內 IFD / 值的位移超出 EXIF 區段長度。Hamana 的 EXIF
    解析器以 VS2005 checked iterator 存取 → 0xC000000D 立即終止。
    同資料夾 / 壓縮檔內只要有一張，背景預讀就會讓 Hamana 崩潰。
    fix 會移除該張的 EXIF 區段（像素資料不動）。根治請用 patch_hamana_exif.py。

用法：
    python hamana_fix.py diagnose <path>...            # 只報告，不改檔
    python hamana_fix.py fix <path>... [-o OUTDIR]     # 產出修復後的檔案

    --zero-policy {drop,placeholder}  0-byte entry 處理方式
                                      （預設 placeholder，保留張數與順序）
    --rename-only                     只正名副檔名，不動 0-byte entry 與 EXIF
"""
import argparse
import os
import struct
import zipfile

# EXIF 位於檔頭附近（APP1 ≤ 64KB，前面可能有 APP0 / ICC APP2）；只讀這麼多來檢查
EXIF_SCAN_BYTES = 512 * 1024

# 1x1 baseline JPEG，用來替換 0-byte entry 以保留原本的張數與順序。
# 由 ffmpeg 產生後實機驗證可正常解碼；手寫 Huffman 表容易出錯，勿自行改寫。
PLACEHOLDER_JPEG = bytes.fromhex(
    'ffd8ffe000104a46494600010200000100010000fffe00104c61766336312e31392e3130'
    '3000ffdb0043000810101310131616161616161a181a1b1b1b1a1a1a1a1b1b1b1d1d1d22'
    '22221d1d1d1b1b1d1d2020222225262523232223262628282830302e2e38383a454553ff'
    'c4004a000100000000000000000000000000000000010100000000000000000000000000'
    '000000100100000000000000000000000000000000110100000000000000000000000000'
    '000000ffc00011080001000103012200021100031100ffda000c03010002110311003f00'
    '000fffd9'
)

# magic bytes → 正確副檔名
MAGIC = [
    (b'\xff\xd8\xff', '.jpg'),
    (b'\x89PNG\r\n\x1a\n', '.png'),
    (b'GIF87a', '.gif'),
    (b'GIF89a', '.gif'),
    (b'BM', '.bmp'),
    (b'II*\x00', '.tif'),
    (b'MM\x00*', '.tif'),
]

# 副檔名等價群組：同群組內不算「不符」
EQUIV = [{'.jpg', '.jpeg', '.jpe'}, {'.tif', '.tiff'}]


def sniff(head):
    """回傳 head 對應的正確副檔名，無法辨識則回傳 None。"""
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp'
    for magic, ext in MAGIC:
        if head.startswith(magic):
            return ext
    return None


def ext_ok(name, real_ext):
    cur = os.path.splitext(name)[1].lower()
    if cur == real_ext:
        return True
    for group in EQUIV:
        if cur in group and real_ext in group:
            return True
    return False


def retarget(name, real_ext):
    return os.path.splitext(name)[0] + real_ext


def jpeg_segments(data):
    """產生 (marker, start, end)，涵蓋 SOS 之前的所有 marker segment。"""
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        m = data[i + 1]
        if m in (0xD9, 0xDA):
            return
        seg_len = struct.unpack('>H', data[i + 2:i + 4])[0]
        yield m, i, i + 2 + seg_len
        i += 2 + seg_len


TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}
SUB_IFD = {0x8769: 'ExifIFD', 0x8825: 'GPS', 0xA005: 'Interop'}


def tiff_problem(t):
    """檢查 TIFF(EXIF) 結構；回傳第一個越界描述，正常則 None。
    比 Hamana 實際會崩潰的條件略寬（例如 IFD1 / Interop 越界原版不崩），寧可多報。"""
    if t[:2] == b'II':
        e = '<'
    elif t[:2] == b'MM':
        e = '>'
    else:
        return None                    # 非 TIFF，Hamana 不會往下解析
    n_all = len(t)
    if n_all < 8:
        return 'TIFF header 截斷（%d bytes）' % n_all
    seen = set()
    todo = [('IFD0', struct.unpack(e + 'I', t[4:8])[0])]
    while todo:
        name, off = todo.pop()
        if off in seen:
            continue
        seen.add(off)
        if off + 2 > n_all:
            return '%s 位移 %d 超出 EXIF 長度 %d' % (name, off, n_all)
        cnt = struct.unpack(e + 'H', t[off:off + 2])[0]
        if off + 2 + 12 * cnt > n_all:
            return '%s 有 %d 個 entry，超出 EXIF 長度 %d' % (name, cnt, n_all)
        for k in range(cnt):
            eo = off + 2 + 12 * k
            tag, typ, c = struct.unpack(e + 'HHI', t[eo:eo + 8])
            val = struct.unpack(e + 'I', t[eo + 8:eo + 12])[0]
            size = TYPE_SIZE.get(typ, 0) * c
            if size > 4 and val + size > n_all:
                return '%s tag 0x%04x 的值位於 %d+%d，超出 EXIF 長度 %d' % (name, tag, val, size, n_all)
            if tag in SUB_IFD:
                todo.append((SUB_IFD[tag], val))
        nxt_at = off + 2 + 12 * cnt
        if nxt_at + 4 <= n_all:
            nxt = struct.unpack(e + 'I', t[nxt_at:nxt_at + 4])[0]
            if nxt:
                todo.append(('IFD1', nxt))
    return None


def exif_problem(data):
    """JPEG 的 EXIF 若會讓 Hamana 越界，回傳描述；否則 None。"""
    if not data.startswith(b'\xff\xd8'):
        return None
    for m, s, e in jpeg_segments(data):
        if m == 0xE1 and data[s + 4:s + 10] == b'Exif\x00\x00':
            if e > len(data):
                return 'APP1 區段被截斷'
            return tiff_problem(data[s + 10:e])
    return None


def strip_exif(data):
    """移除 JPEG 的 EXIF APP1 區段（其餘 bytes 原封不動）。"""
    for m, s, e in jpeg_segments(data):
        if m == 0xE1 and data[s + 4:s + 10] == b'Exif\x00\x00':
            return data[:s] + data[e:]
    return data


def scan_zip(path):
    entries, issues = [], []
    with zipfile.ZipFile(path) as z:
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            # 只串流讀檔頭（JPEG 多讀到涵蓋 EXIF），避免對大型壓縮檔整包解壓
            head = b''
            if info.file_size:
                with z.open(info) as fh:
                    head = fh.read(16)
                    if head.startswith(b'\xff\xd8'):
                        head += fh.read(EXIF_SCAN_BYTES)
            entries.append((info.filename, info.file_size, head[:16]))
            if info.file_size == 0:
                issues.append(('ZIP_ZERO_ENTRY', info.filename,
                               'size=0 → Hamana 開 zip 時崩潰'))
                continue
            bad = exif_problem(head)
            if bad:
                issues.append(('EXIF_OOB', info.filename, bad + ' → Hamana 崩潰'))
            real = sniff(head)
            if real is None:
                issues.append(('UNKNOWN_FORMAT', info.filename,
                               '無法辨識 magic %r' % head[:8]))
            elif not ext_ok(info.filename, real):
                cur = os.path.splitext(info.filename)[1].lower() or '(無)'
                issues.append(('EXT_MISMATCH', info.filename,
                               '副檔名 %s 但內容是 %s' % (cur, real)))
    return entries, issues


def scan_dir(path):
    entries, issues = [], []
    for root, _, files in os.walk(path):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, path)
            size = os.path.getsize(full)
            head = b''
            if size:
                with open(full, 'rb') as fh:
                    head = fh.read(16)
                    if head.startswith(b'\xff\xd8'):
                        head += fh.read(EXIF_SCAN_BYTES)
            entries.append((rel, size, head[:16]))
            if size == 0:
                # 資料夾模式下 0-byte 不會崩潰，只是該張讀不出來
                issues.append(('ZERO_FILE', rel,
                               'size=0 → 該張讀取失敗（資料夾模式不崩潰）'))
                continue
            bad = exif_problem(head)
            if bad:
                issues.append(('EXIF_OOB', rel, bad + ' → Hamana 崩潰'))
            real = sniff(head)
            if real is None:
                issues.append(('UNKNOWN_FORMAT', rel,
                               '無法辨識 magic %r' % head[:8]))
            elif not ext_ok(rel, real):
                cur = os.path.splitext(rel)[1].lower() or '(無)'
                issues.append(('EXT_MISMATCH', rel,
                               '副檔名 %s 但內容是 %s' % (cur, real)))
    return entries, issues


def scan(path):
    if os.path.isdir(path):
        return scan_dir(path)
    if zipfile.is_zipfile(path):
        return scan_zip(path)
    raise SystemExit('不支援的目標（非資料夾也非 zip）：%s' % path)


def report(path, entries, issues):
    print('== %s' % path)
    print('   項目數 %d，問題 %d' % (len(entries), len(issues)))
    by_kind = {}
    for kind, name, why in issues:
        by_kind.setdefault(kind, []).append((name, why))
    for kind, rows in by_kind.items():
        print('   [%s] x%d' % (kind, len(rows)))
        for name, why in rows[:8]:
            print('      - %s: %s' % (name, why))
        if len(rows) > 8:
            print('      ... 另外 %d 筆' % (len(rows) - 8))
    if not issues:
        print('   無已知問題')
    print()


def fix_zip(path, outdir, zero_policy, rename_only):
    _, issues = scan_zip(path)
    kinds = set(k for k, _, _ in issues)
    if not (kinds & {'ZIP_ZERO_ENTRY', 'EXT_MISMATCH', 'EXIF_OOB'}):
        print('== %s: 無需修復' % path)
        return None
    stem, ext = os.path.splitext(os.path.basename(path))
    out = os.path.join(outdir, '%s.fixed%s' % (stem, ext))
    renamed = dropped = replaced = stripped = 0
    with zipfile.ZipFile(path) as zin, \
            zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zout:
        for info in sorted(zin.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            data = zin.read(info)
            name = info.filename
            if not data:
                if rename_only:
                    zout.writestr(name, data)
                elif zero_policy == 'drop':
                    dropped += 1
                else:
                    zout.writestr(retarget(name, '.jpg'), PLACEHOLDER_JPEG)
                    replaced += 1
                continue
            real = sniff(data[:16])
            if real and not ext_ok(name, real):
                name = retarget(name, real)
                renamed += 1
            if not rename_only and exif_problem(data):
                data = strip_exif(data)
                stripped += 1
            zout.writestr(name, data)
    print('== %s' % path)
    print('   → %s' % out)
    print('   正名 %d、0-byte 替換 %d、0-byte 移除 %d、壞 EXIF 移除 %d'
          % (renamed, replaced, dropped, stripped))
    return out


def fix_dir(path, zero_policy, rename_only):
    """資料夾就地正名（0-byte 在資料夾模式不崩潰，只是該張讀不出來）。"""
    _, issues = scan_dir(path)
    renamed = dropped = replaced = stripped = 0
    # 先處理 EXIF（就地改內容），再正名（會改路徑）
    for kind, rel, _ in issues:
        if kind == 'EXIF_OOB' and not rename_only:
            full = os.path.join(path, rel)
            with open(full, 'rb') as fh:
                data = fh.read()
            with open(full, 'wb') as fh:
                fh.write(strip_exif(data))
            stripped += 1
    for kind, rel, _ in issues:
        full = os.path.join(path, rel)
        if kind == 'EXT_MISMATCH':
            with open(full, 'rb') as fh:
                real = sniff(fh.read(16))
            target = retarget(full, real)
            if os.path.exists(target):
                print('   略過（目標已存在）：%s' % rel)
                continue
            os.rename(full, target)
            renamed += 1
        elif kind == 'ZERO_FILE' and not rename_only:
            if zero_policy == 'drop':
                os.remove(full)
                dropped += 1
            else:
                with open(full, 'wb') as fh:
                    fh.write(PLACEHOLDER_JPEG)
                replaced += 1
    print('== %s（就地修改）' % path)
    print('   正名 %d、0-byte 替換 %d、0-byte 移除 %d、壞 EXIF 移除 %d'
          % (renamed, replaced, dropped, stripped))
    return path


def main():
    ap = argparse.ArgumentParser(description='Hamana 素材診斷 / 修復')
    ap.add_argument('mode', choices=['diagnose', 'fix'])
    ap.add_argument('paths', nargs='+', help='zip 檔或資料夾')
    ap.add_argument('-o', '--outdir', default='.',
                    help='fix 模式的輸出目錄（僅 zip 使用）')
    ap.add_argument('--zero-policy', choices=['drop', 'placeholder'],
                    default='placeholder',
                    help='0-byte entry：移除，或換成 1x1 佔位圖保留張數（預設）')
    ap.add_argument('--rename-only', action='store_true',
                    help='只正名副檔名，不動 0-byte entry')
    args = ap.parse_args()

    if args.mode == 'fix':
        os.makedirs(args.outdir, exist_ok=True)
    for path in args.paths:
        if args.mode == 'diagnose':
            entries, issues = scan(path)
            report(path, entries, issues)
        elif os.path.isdir(path):
            fix_dir(path, args.zero_policy, args.rename_only)
        else:
            fix_zip(path, args.outdir, args.zero_policy, args.rename_only)


if __name__ == '__main__':
    main()
