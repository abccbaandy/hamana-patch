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

用法：
    python hamana_fix.py diagnose <path>...            # 只報告，不改檔
    python hamana_fix.py fix <path>... [-o OUTDIR]     # 產出修復後的檔案

    --zero-policy {drop,placeholder}  0-byte entry 處理方式
                                      （預設 placeholder，保留張數與順序）
    --rename-only                     只正名副檔名，不動 0-byte entry
"""
import argparse
import os
import zipfile

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


def scan_zip(path):
    entries, issues = [], []
    with zipfile.ZipFile(path) as z:
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            # 只串流讀前 16 bytes，避免對大型壓縮檔整包解壓
            head = b''
            if info.file_size:
                with z.open(info) as fh:
                    head = fh.read(16)
            entries.append((info.filename, info.file_size, head))
            if info.file_size == 0:
                issues.append(('ZIP_ZERO_ENTRY', info.filename,
                               'size=0 → Hamana 開 zip 時崩潰'))
                continue
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
            entries.append((rel, size, head))
            if size == 0:
                # 資料夾模式下 0-byte 不會崩潰，只是該張讀不出來
                issues.append(('ZERO_FILE', rel,
                               'size=0 → 該張讀取失敗（資料夾模式不崩潰）'))
                continue
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
    if not (kinds & {'ZIP_ZERO_ENTRY', 'EXT_MISMATCH'}):
        print('== %s: 無需修復' % path)
        return None
    stem, ext = os.path.splitext(os.path.basename(path))
    out = os.path.join(outdir, '%s.fixed%s' % (stem, ext))
    renamed = dropped = replaced = 0
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
            zout.writestr(name, data)
    print('== %s' % path)
    print('   → %s' % out)
    print('   正名 %d、0-byte 替換 %d、0-byte 移除 %d'
          % (renamed, replaced, dropped))
    return out


def fix_dir(path, zero_policy, rename_only):
    """資料夾就地正名（0-byte 在資料夾模式不崩潰，只是該張讀不出來）。"""
    _, issues = scan_dir(path)
    renamed = dropped = replaced = 0
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
    print('   正名 %d、0-byte 替換 %d、0-byte 移除 %d'
          % (renamed, replaced, dropped))
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
