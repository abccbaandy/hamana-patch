# 攻擊面盤點（惡意檔案 → 記憶體破壞）

目的：決定哪些內建解析器要 patch 退場、改交外部 DLL。威脅模型 = 使用者開啟不可信的圖片 / 壓縮檔 / 資料夾。
工具：`tools/re/pe_audit.py`（安全旗標 + 內嵌函式庫指紋；`--root` 遮蔽安裝路徑）；分派順序與沙箱/除錯工具見 `hamana-internals.md`。
狀態旗標：CONFIRMED / guess / unknown。

## 緩解機制（CONFIRMED，`pe_audit.py` 對原版 exe）

| 模組 | ASLR | DEP 旗標 | SafeSEH | /GS | 備註 |
|---|---|---|---|---|---|
| `Hamana.exe` | n，**無 .reloc**（事後無法開 ASLR） | n | 無 LoadConfig → 無 | 有（`__security_check_cookie` 樣式） | base `0x400000` |
| `HamanaLang.dll` | n | n | - | n | 純資源 |
| `DefaultSPIs/7z.dll`（2024） | Y | Y | - | n | |
| `DefaultSPIs/ax7z.spi`（2015） | Y | Y | Y | Y | |
| `DefaultSPIs/ifwebp.spi`（2024） | Y | Y | - | n | |
| `DefaultSPIs/ifgdip.spi`（2005） | n（有 reloc） | n | Y | Y | 解碼交給系統 GDI+ |
| `Formats/*`、`Codecs/*`（7-zip 4.x，2006） | n | n | - | n | 見下「疑似未使用」 |

- DEP 由 exe 決定。系統政策 `DataExecutionPrevention_SupportPolicy=2`（OptIn），原版行程 `GetProcessMitigationPolicy(ProcessDEPPolicy)` = `0x0` → **原版沒有 DEP**（CONFIRMED）。
- **已修（CONFIRMED）**：`tools/patch_hamana_nx.py` 設 `NX_COMPAT`（DllCharacteristics bit 0x100，file 只差 1 byte），已併入 `apply_patches.py`。patch 後 DEP flags = `0x3`（Enable + DisableAtlThunkEmulation）、permanent=1。
  - 回歸（沙箱，原版 vs patch）：jpg、png24、png32、gif、bmp、webp、壞 EXIF jpg、zip、cbz、zip 改名 .7z、7 種格式混放資料夾（背景預讀）全部 ALIVE，標題 `[WxHxbpp]` 一致。
  - 未涵蓋：設定對話框、投影片、縮圖模式、library、Ghostscript、影片（ATL thunk 若在這些路徑會以 `0xC0000005` 閃退 → 用 `--revert` 還原並記錄）。
  - 已知、與 DEP 無關：ffmpeg 產生的 `.tif` 兩版都讀不出（標題無 `[WxH]`）。
- 強制 ASLR 做不到（exe 沒有 reloc）。另一個做法是 per-app 的惡意探索保護，需要系統管理員權限，未採用。

## 內建解析器（exe 內，不經 SPI）

| 格式 | 實作 | 版本 / 證據 | 開關 | 本機目前 | 風險 |
|---|---|---|---|---|---|
| JPEG | IJG libjpeg + x86 SIMD 1.0 | 1994-1998 版權字串；`Hamana.txt:83` | `Respect_libjpeg` 只改先後，無法關閉 → **已 patch 退場**（`tools/patch_hamana_jpeg.py`） | — | 原版高（所有 jpg 必進 libjpeg）。patch 後像素改由 GDI+ 解；libjpeg 殘留見下「JPEG 退場」 |
| EXIF | 自寫，3 個函式 | `bugs.md` BUG-3 | 無；`ShowExif*Info` 只管顯示 | 每張 JPEG 都會解析 | 中（已知會越界讀取，已由 L0b 攔截） |
| PNG | **無可達的內建解碼器**（CONFIRMED） | 兩組 PNG 字串都是 D3DX 孤兒資料（libpng 1.0.5 reader / writer，無 xref）；開 PNG 時 inflate 不命中；移除 `ifgdip.spi` 後任何設定都讀不出 PNG | `Respect_internalPNGDec` 只決定是否先問 GDI+ | 1 | 無（PNG 一律 GDI+）；不必改設定 |
| zip / cbz | minizip 0.15 + zlib 1.1.4 | `hamana-internals.md` | L0 patch | 已退場 | 已消除 |
| GIF | unknown；分派函式第一步的特殊路徑（`0x456ca0`），guess：動畫 GIF | 副檔名表 `gif`（offset 2236736） | unknown | — | unknown |
| D3DX | 靜態連結（shader 編譯器、HDR writer 字串） | PNG 字串為孤兒資料（見 PNG 列）；是否用 D3DX 載入使用者檔案：unknown（guess：只載 `BaseTextures/`） | — | — | 低，unknown |
| `.lnk`、library 檔 | 自寫 | `Hamana.txt:253` | — | — | 低到中，unknown |

## 外部解析器（經 SPI 或系統 DLL）

| 格式 | 元件 | 狀態 |
|---|---|---|
| bmp/jpg/png/gif/tif/ico/wmf/emf | `ifgdip.spi` → 系統 GDI+ | GDI+ 隨 Windows Update 更新；`Respect_Gdiplus=1` |
| webp（+ L2 patch 後的 jpg） | `ifwebp.spi` 2024 | 新 |
| 7z/rar/lzh/arj/cab/zip（L0 後） | `ax7z.spi` 2015 + `DefaultSPIs/7z.dll` 2024 | 新 |
| pdf/ai/eps/ps | Ghostscript `gsdll32.dll`（`GSDllPath`） | 未安裝、`GSDllPath` 空 → 未啟用 |
| 影片（`[Movie] Extensions`，含 `.gif`） | DirectShow + 系統 codec | `EnablePlayMovie=0` → 關閉 |

## 未使用的元件（CONFIRMED）

- `Formats/`、`Codecs/`（7-zip 4.x，2006）從未載入：`minidbg.py --loads` 開真 7z、zip 改名 rar/cbr/lzh/arj/cab，只載入 `DefaultSPIs/7z.dll`。不在攻擊面上，刪不刪都一樣；`ax7z.spi_old`、`ifwebp.spi_` 也不會被載入（副檔名不符）。

## DLL 搜尋路徑（CONFIRMED：開檔不會從圖片資料夾載入 DLL）

- 所有 `LoadLibrary*` 呼叫點（`0x4075e6` 起，共 13 處）：
  - `HamanaLang.dll`：相對名稱，但 exe 目錄優先 → 安全
  - migemo：`GetModuleFileNameW` 取 exe 目錄 + `tmigemo\migemo.dll`（`0x4b41f8`→`0x4b3af2`），絕對路徑
  - Ghostscript：`GSDllPath` 原值（`[0x67d918]`，`0x4a0d7e`）；預設空 → 不載入。使用者若只填檔名就會走搜尋路徑
  - SPI：完整路徑；`7z.dll` 由 ax7z 從自身目錄載入
  - `mfcm80u.dll` 只用 `GetModuleHandle`；`unicows.dll`、`d3d9.dll` 走系統目錄
- 實測：圖片資料夾預放 `tmigemo\migemo.dll`、`gsdll32.dll`、`d3d9d.dll`、`mfcm80u.dll`、`unicows.dll`、`mscoree.dll`、`HamanaLang.dll`、`7z.dll`（誘餌 = 純資源 DLL），開 png/pdf/ps/7z，工作目錄設為 exe 目錄或圖片資料夾 → 都沒有載入。
- 正控制組：刪掉安裝目錄的 `HamanaLang.dll` 且工作目錄 = 圖片資料夾 → 會載入 `<target>\hamanalang.dll`。**安裝目錄缺檔時會被劫持**，不要刪 `HamanaLang.dll`。
- 未涵蓋：migemo 搜尋功能實際觸發時（路徑已靜態確認為絕對路徑）。

## JPEG 退場（CONFIRMED，`tools/patch_hamana_jpeg.py`，已併入 `apply_patches.py` 第 2c 步）

- 改法：分派函式 3 個條件跳躍（`0x45a905`、`0x45a96e`、`0x45aadc`）→ jmp；加上 `DefaultSPIs/ifgdip.spiifgdip.ini` 的 `[ifgdip] jpg=1`（ifgdip 預設不宣告 jpg，且 ini 路徑有 bug，見 `hamana-internals.md`）。兩者缺一 jpg 就開不了。
- libjpeg 的所有入口（`jpeg_CreateDecompress` 只有 `0x452848` 一處，核心 `0x452720`）：

| 呼叫者 | 用途 | patch 後 |
|---|---|---|
| `0x45a450`（分派第 2、5 步） | 解像素 | 不再到達 |
| `0x456fc0`（SPI 解 jpg 成功後） | `jpeg_read_header` + `save_markers(APP1)` → 自寫 EXIF 解析 | 仍會跑，只讀檔頭（`[ebp+0x70]!=0` 直接返回，`jpeg_start_decompress` `0x4bc850` 不命中） |
| `0x4659e0`（另一執行緒） | 解 ThumbDB（`HamanaThumb\*.mcf`，Hamana 自己用 `0x452c80` 編碼的） | 仍會跑；輸入非外部檔（guess：DB 只由 `0x465de0` 寫入） |

- 殘留攻擊面：libjpeg marker 解析（jdmarker）+ 自寫 EXIF 解析（已有 L0b 越界防護）。要完全去掉需讓 `0x456fc0` 不跑（失去 EXIF 顯示），未做。
- 回歸（沙箱，原版 vs patch）：4:2:0/4:2:2/4:4:4、漸進式、灰階、restart marker、截斷、1x1、4001x3001、`.jpeg`、`.JPG`、7z 內 jpg、WebP 改名 `.jpg`、EXIF fuzz 19 種（逐檔 + 同資料夾預讀）、PNG 8 種、壓縮檔 8 種 → 全部 ALIVE、尺寸一致。CMYK JPEG：原版讀不出，patch 後正常；BMP/PNG 改名 `.jpg` 也變得讀得出。`apply_patches --revert` 後 exe/ifwebp 與原檔 hash 相同。
- 未測：GDI+ 對大量圖的解碼速度；EXIF 資訊視窗、投影片、縮圖模式。

## 建議順序

1. ~~DEP~~ 已完成。
2. ~~PNG~~ 不需處理：沒有內建 PNG 解碼器可達。
3. ~~Formats/Codecs~~ 從未載入，不在攻擊面上。
4. ~~DLL 搜尋路徑~~ 開檔不會被劫持。
5. ~~JPEG~~ 已退場（見上）。剩：EXIF 讀取時的 libjpeg 檔頭解析 + 自寫 EXIF 解析；GIF 特殊路徑（unknown）。
