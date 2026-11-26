# 攻擊面盤點（惡意檔案 → 記憶體破壞）

目的：決定哪些內建解析器要 patch 退場、改交外部 DLL。威脅模型 = 使用者開啟不可信的圖片 / 壓縮檔 / 資料夾。
工具：`tools/re/pe_audit.py`（安全旗標 + 內嵌函式庫指紋；`--root` 遮蔽安裝路徑）。
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
| JPEG | IJG libjpeg + x86 SIMD 1.0 | 1994-1998 版權字串；`Hamana.txt:83` | `Respect_libjpeg` | 0 | 高（舊版，靜態連結） |
| EXIF | 自寫，3 個函式 | `bugs.md` BUG-3 | 無；`ShowExif*Info` 只管顯示 | 每張 JPEG 都會解析 | 中（已知會越界讀取，已由 L0b 攔截） |
| PNG | 內建解碼器 + zlib 1.1.4 | `IHDR`/`IDAT`/`PLTE`/`tRNS` 字串有兩組 | `Respect_internalPNGDec` | **1** | 高（zlib 1.1.4 有已知 inflate 漏洞） |
| zip / cbz | minizip 0.15 + zlib 1.1.4 | `hamana-internals.md` | L0 patch | 已退場 | 已消除 |
| GIF | unknown | 副檔名表 `gif`（offset 2236736） | unknown | — | unknown |
| D3DX | 靜態連結（`File created by Microsoft D3DX library`、shader 編譯器字串） | guess：其中一組 PNG 字串來自 D3DX 的 texture loader | — | — | unknown |
| `.lnk`、library 檔 | 自寫 | `Hamana.txt:253` | — | — | 低到中，unknown |

## 外部解析器（經 SPI 或系統 DLL）

| 格式 | 元件 | 狀態 |
|---|---|---|
| bmp/jpg/png/gif/tif/ico/wmf/emf | `ifgdip.spi` → 系統 GDI+ | GDI+ 隨 Windows Update 更新；`Respect_Gdiplus=1` |
| webp（+ L2 patch 後的 jpg） | `ifwebp.spi` 2024 | 新 |
| 7z/rar/lzh/arj/cab/zip（L0 後） | `ax7z.spi` 2015 + `DefaultSPIs/7z.dll` 2024 | 新 |
| pdf/ai/eps/ps | Ghostscript `gsdll32.dll`（`GSDllPath`） | 未安裝、`GSDllPath` 空 → 未啟用 |
| 影片（`[Movie] Extensions`，含 `.gif`） | DirectShow + 系統 codec | `EnablePlayMovie=0` → 關閉 |

## 疑似未使用（guess）

- `Formats/`、`Codecs/`（7-zip 4.x，2006）：exe 內沒有 `Formats` / `Codecs` 字串。`ax7z.spi`（2015）用的是同目錄的 `DefaultSPIs/7z.dll`（`ax7z.txt:48`）。可能是 `ax7z.spi_old` 的遺留。要在沙箱開 rar/7z 後列出載入的模組確認，確認未載入就可以刪除。

## DLL 搜尋路徑（guess，待驗證）

- exe 含 `tmigemo\migemo.dll`（`Migemo=1`）與 `gsdll32.dll` 字串，本機兩者都不存在。若是以相對路徑 `LoadLibrary`，搜尋範圍可能包含目前工作目錄 → 資料夾內預放同名 DLL 會被載入。
- 驗證法（無害）：沙箱放一個只寫 log 的測試 DLL，開該資料夾後檢查有沒有被載入；同時記錄 Hamana 開檔後的工作目錄。

## 待確認（unknown）

- `Respect_*=0` 且 SPI 回報不支援時，回退到哪個內建解碼器？順序為何？`solutions.md` 只確認「會回退」。
- 決定內建解析器能否完全退場：需知道各格式在目前 ini 下實際由誰解碼。判定法：沙箱 + 視窗標題（`hamana-internals.md`）、模組載入清單。

## 建議順序

1. ~~DEP~~ 已完成（見上）。
2. `Respect_internalPNGDec=0`，讓 PNG 交給 GDI+（改設定即可）；回歸測試透明 PNG。
3. 驗證 `Formats/`、`Codecs/` 是否未載入 → 刪除。
4. 驗證 DLL 搜尋路徑。
5. 確認回退路徑後，再決定 JPEG / EXIF 要不要 patch 退場。
