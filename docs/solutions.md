# 修復方案光譜

針對「下次的閃退／讀不了，未必是同一個模式」這個問題：與其逐個 bug 寫腳本，
更有效的是**換掉出問題的整條程式碼路徑**。Hamana 雖閉源，但有兩個可介入的接縫。

## 介入接縫（CONFIRMED）

| 接縫 | 可否接管 | 依據 |
|---|---|---|
| 壓縮檔解壓 | **可**，改掉 exe 內的 `zip`/`cbz` 字串即可整條交給 ax7z.spi | 實測（L0） |
| 影像解碼分派 | **可**，透過 Susie SPI 宣告副檔名 + `Respect_*=0` | 實測（L2） |

兩個關鍵機制：

1. **Hamana 會在 SPI 的 `IsSupported` 回報不支援時自動回退**到下一個解碼器。
   因此 SPI 可以安全地「多宣告」副檔名而不破壞既有格式（L2 的基礎）。
2. **Hamana 找不到內建 handler 時會把檔案交給 SPI。**
   內建壓縮格式是用 UTF-16 字串常數 `"zip"` / `"cbz"` 認定的，
   改掉它們就等於停用內建解壓（L0 的基礎）。

---

## L0 — 停用內建 zip/cbz，整條交給 SPI ★ 最根本

**涵蓋：內建解壓路徑的所有缺陷，且日後可藉由更換 `7z.dll` 版本繼續修。**

狀態：**CONFIRMED**，工具：`tools/patch_hamana_zip.py`

Hamana.exe 的 `.rdata` 內有兩個相鄰的 UTF-16 字串常數（本版本 offset 2236744 / 2236752）：

```
'gif\0'  'cbz\0'  'zip\0'  'IgnoreExtensions\0'
```

把 `cbz` / `zip` 改成不可能出現的副檔名（`czq` / `zzq`，同長度），
Hamana 就認不得這兩種副檔名是「自己該處理的壓縮檔」，轉而交給 Susie SPI。
於是 2006 年的 minizip 0.15 + zlib 1.1.4 整條退場，換成
ax7z.spi（2015）+ `DefaultSPIs/7z.dll`（2024-11-30）。

```bash
python tools/patch_hamana_zip.py "<Hamana>\Hamana.exe"
python tools/patch_hamana_zip.py "<Hamana>\Hamana.exe" --check
python tools/patch_hamana_zip.py "<Hamana>\Hamana.exe" --revert
```

**必要配套**：`cbz` / `cbr` 不在 7z.dll 的內建副檔名清單中，
需加進 ax7z 的 `user_extensions`（工具會自動處理
`%APPDATA%\ax7z\ax7z.spi.ini`）。`zip` 本身在 7z.dll 清單內，不必加。

### 實測效果

| 情境 | patch 前 | patch 後 |
|---|---|---|
| zip 含 0-byte entry | WerFault 崩潰 | **正常，空 entry 被自動略過** |
| cbz 含 0-byte entry | WerFault 崩潰 | **正常** |
| **瀏覽含問題壓縮檔的資料夾** | **崩潰 `0xC000000D`** | **正常** |
| 正常 zip | 正常 | 正常 |
| **加密 zip** | **開不了** | **彈出 ax7z 密碼對話框** |
| rar / 7z / 真資料夾 / 單張圖 | 正常 | 正常（不受影響） |
| 瀏覽乾淨壓縮檔資料夾 | 顯示資料夾 | 顯示資料夾（行為一致） |

加密 zip 那列特別有意義：`ax7z.txt` §8 把「Hamana 開不了加密 zip」列為
**Susie plugin 端無法對應的限制**，原因正是內建優先。L0 連帶解除了它。

倒數第三列也重要：**光是瀏覽資料夾就會崩潰**，不需真的開啟該壓縮檔，
代表 BUG-1 的影響面比「開啟特定檔案」更廣。

### 代價與未驗證項

- 這是對 exe 的二進位修改，但**只動字串常數、不動任何程式碼**，
  檔案大小不變，`--revert` 可完整還原
- patch 後 `DefaultSPIs/ax7z.spi` 與 `7z.dll` 成為必要元件，不可刪除
- 解壓效能未量測；7z.dll 較新但多一層 SPI 呼叫
- **未驗證**：檔案清單 UI 內 `.zip` 的顯示與進入。自動化測不到，
  但 patched / unpatched 瀏覽資料夾的行為一致，風險應該很低 —— 請實際操作確認

---

## L1 — 不改 exe 的替代：讓素材離開內建 zip 路徑

適用於不想修改 `Hamana.exe` 的情況。**若已套用 L0，本節不需要。**

狀態：**CONFIRMED**。

### L1a：只改副檔名（零成本，不必重壓）

**內容仍是 zip，只把副檔名改成 `.rar` / `.7z` / `.arj` 即可。**

關鍵機制：Hamana 依副檔名決定交給誰，而 ax7z.spi 背後的 `7z.dll`
**是依內容偵測實際格式的**。所以 zip 內容配 `.rar` 副檔名照樣解得開，
且整條內建 zip 常式被跳過。

| 容器（內容皆為同一個含 0-byte entry 的 zip） | 結果 |
|---|---|
| `.zip` | WerFault 崩潰 |
| `.cbz` | 仍崩潰（cbz 同樣走內建碼） |
| **`.rar`** | **正常 `(1/9)`，空 entry 被自動略過** |
| **`.7z`** | **正常 `(1/9)`** |
| **`.arj`** | **正常 `(1/9)`** |
| `.cbr` | **開不起來**（應是導向 `Formats/rar.dll`，2006 版嚴格解析 rar 格式） |

注意 `.cbz` 與 `.cbr` 都**不可用**：前者崩潰，後者開不了。

代價：副檔名與實際格式不符，分享給別人或用其他軟體開會造成混淆。
自用可接受；要外流通則用 L1b。

### L1b：真的轉成 7z（誠實，需重壓）

```bash
7z a -t7z -ms=off out.7z <來源資料夾>\*
```

`-ms=off` 關閉固實壓縮，否則隨機翻頁會明顯變慢。
本機無 7z CLI 時可用 `uv run --with py7zr` 以 Python 打包。

為什麼有效：zip 走 Hamana 內建常式（zlib 1.1.4，2002 年）；其餘格式走
`DefaultSPIs/ax7z.spi`（2015）+ `7z.dll`（2024-11-30）。等於把 2006 年的
解壓實作整個換成近代且仍有人維護的版本。

## L2 — 讓 SPI 接管副檔名分派

**涵蓋：所有「副檔名與實際內容不符」的情況。**

狀態：**CONFIRMED**，已做出可用 patch：`tools/patch_ifwebp.py`。

把 `ifwebp.spi` 的宣告由 `*.webp` 擴充為 `*.webp;*.jpg;*.jpeg`
（同長度字串區重排 + 改 desc 指標，檔案大小不變、程式碼不動、可 revert），
並設 `Respect_libjpeg=0`。

| 測項 | patch 前 | patch 後 |
|---|---|---|
| WebP 內容 + `.jpg` | 讀取失敗 | **成功 `x32`** |
| 真 JPEG + `.jpg` | 成功 `x24` | 成功 `x24`（無退化） |
| 真 WebP + `.webp` | 成功 `x32` | 成功 `x32`（無退化） |

```bash
python tools/patch_ifwebp.py "<Hamana>\DefaultSPIs\ifwebp.spi"
python tools/patch_ifwebp.py "<Hamana>\DefaultSPIs\ifwebp.spi" --check
python tools/patch_ifwebp.py "<Hamana>\DefaultSPIs\ifwebp.spi" --revert
```

前提與代價：
- **必須** `Respect_libjpeg=0`，否則內建 libjpeg 優先，SPI 不會被詢問
- 真 JPEG 會多一次 SPI 詢問（可忽略），但解碼路徑可能由內建 libjpeg
  改為其他解碼器，色彩管理細節或有差異
- patch 綁定特定版本的字串佈局；換 `ifwebp.spi` 版本需重跑（工具會自行偵測並拒絕）

## L3 — 資料面正規化（`tools/hamana_fix.py`）

**涵蓋：已知模式**（0-byte entry、副檔名不符）。治標，但零風險、不動 Hamana。

適合：不想改 Hamana 安裝、或素材要分享給別人時。
`diagnose` 也可當成「開啟前健檢」使用。

## L4 — 自製 shim SPI（未實作）

L2 的通用化版本：寫一個 SPI，宣告所有常見副檔名，
`IsSupported` 依 magic bytes 判斷，內部委派給現代解碼器
（libwebp / libjpeg-turbo / libpng / libavif / libjxl…）。

L2 的實測已證明**機制成立**（宣告可接管、不支援會回退），所以這條路線是通的。
效益：一次解決所有副檔名錯置問題，**並讓 2006 年的 Hamana 支援 AVIF / JPEG XL / HEIC**。

成本：需要 32-bit C/C++ 編譯環境（MinGW-w64 i686 或 MSVC）。
`DefaultSPIs/src.zip` 有 2011 版 ifwebp 原始碼可當骨架；
2024 版原始碼在 `github.com/uyjulian/ifwebp`（binary 內字串已標示）。

## L5 — 修補內建解壓常式本身（未實作，已無必要）

針對 BUG-1 在內建 zip 常式加上 `size > 0` 防呆。
需要先用 debugger 抓崩潰點 —— **本機沒有安裝 WinDbg/cdb**，
可透過 WER LocalDumps 取得 dump（需管理員權限改 HKLM 登錄檔）。

**L0 已讓整條內建常式退場，這條路線不再有意義。** 保留記錄僅供參考。

---

## 建議組合

| 情境 | 作法 |
|---|---|
| 一勞永逸 | **L0 + L2**：各 patch 一次，之後素材完全不用預處理 |
| 不想改 exe | **L1a**（改副檔名）+ **L2** |
| 完全不想動 Hamana 安裝 | **L3**（資料面修復） |
| 遇到新的未知問題 | 先 `hamana_fix.py diagnose`；不是已知模式就走 `docs/hamana-internals.md` 的沙箱工法二分定位 |

套用 L0 + L2 後，壓縮與影像解碼**兩條主要路徑都已交給可更換的外部元件**：

| 路徑 | 接手元件 | 日後出問題怎麼修 |
|---|---|---|
| 壓縮檔解壓 | `ax7z.spi` + `DefaultSPIs/7z.dll` | 換新版 7z.dll |
| WebP 解碼 | `DefaultSPIs/ifwebp.spi` | 換新版 ifwebp.spi |
| 其他影像格式 | `ifgdip.spi`（GDI+）或內建 | 見 L4 |

剩下真正綁死在 2006 年 exe 裡、只能逐案處理的，是 **D3D 貼圖、UI、縮圖 DB**
這幾塊 —— 它們沒有 plugin 接縫。
