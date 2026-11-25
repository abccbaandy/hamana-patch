# Hamana Patch

修正 [Hamana](https://www.vector.co.jp/soft/win95/art/se311115.html) v1.48（2006/06/19）三個會造成**閃退**與**讀圖失敗**的缺陷。

補丁檔案大小不變，可完整還原。zip 與 WebP 兩項只改字串常數；EXIF 一項改動 94 bytes 程式碼。

> **本專案不包含 Hamana 本體**，請自備。這裡提供的是 patcher 與研究文件。

<details>
<summary>English summary</summary>

Bug-fix patches for Hamana v1.48 (2006), a Direct3D-based image viewer for Windows.

- **Crash on opening archives**: Hamana's built-in zip handler (minizip 0.15 / zlib 1.1.4) crashes on zero-byte entries. The patch disables the built-in handler so archives are routed to `ax7z.spi` + a modern `7z.dll` instead. Encrypted zip support comes along for free.
- **Crash on JPEGs with corrupt EXIF**: Hamana's EXIF parser uses VS2005 checked iterators; an out-of-range IFD offset calls `_invalid_parameter_noinfo` and the process dies with `0xC000000D` — even when the bad JPEG is merely a neighbour being prefetched. The patch hooks the CRT `_invalid_parameter` and, when the innermost SEH frame belongs to one of the three EXIF parser functions, unwinds to that function's normal exit so only the EXIF is skipped. Other code paths behave exactly as before.
- **Images failing to load**: Hamana dispatches decoders by *file extension*, never by content. WebP files named `.jpg` are handed to libjpeg and fail. The patch makes `ifwebp.spi` also claim `*.jpg`, relying on Hamana's automatic fallback when a Susie plugin reports the format as unsupported.

The zip and WebP patches only rewrite string constants; the EXIF patch changes 94 bytes of code (a 9-byte hook plus an 85-byte stub placed in existing `int3` padding). File sizes are unchanged and `--revert` restores the originals. **Hamana itself is not distributed here.**

</details>

---

## 修正的問題

### 1. 開啟壓縮檔後數秒閃退

壓縮檔內只要有一個 **0 位元組的 entry**，Hamana 就會崩潰。

Hamana 對 `.zip` / `.cbz` 走的是自己內建的解壓常式（**minizip 0.15 / 1998 + zlib 1.1.4 / 2002**），不是 `ax7z.spi`，所以更新任何第三方 lib 都修不了。

影響範圍比想像中大——**光是「瀏覽」含問題壓縮檔的資料夾就會崩潰**（`0xC000000D`），不必真的開啟它。

### 2. 含損壞 EXIF 的 JPEG 造成閃退

開啟後畫面停住幾秒就閃退（`0xC000000D`）。原因是 JPEG 的 EXIF 內位移指向區段之外
（常見於轉檔工具沒更新 EXIF 指標），Hamana 的 EXIF 解析器越界存取時，
VS2005 的 checked iterator 直接終止行程。

**同資料夾或同壓縮檔裡只要有一張這種 JPEG，背景預讀就會讓 Hamana 崩潰**，即使你正在看的是別張。
這也是上一版（只修 zip）套用後仍會閃退的原因。

### 3. 部分圖檔讀取失敗（顯示 error）

**Hamana 依副檔名分派解碼器，完全不看檔案內容。** 副檔名為 `.jpg` 的檔案一律送進內建 libjpeg，內容若是 WebP 就會失敗：

```
Not a JPEG file: starts with 0x52 0x49
```

`0x52 0x49` 是 `RI`，也就是 WebP 的 `RIFF` 標頭。這類檔案本身完全正常，只是「穿錯衣服」。

---

## 快速開始

需求：Python 3.8+、Windows、Hamana v1.48，以及下面三個第三方元件。

### 1. 安裝第三方元件（Hamana 原版沒有）

zip 與 WebP 的修正要靠這三個元件接手。Hamana 原版的 `DefaultSPIs` 只有 2006 年的舊版 `ax7z.spi`，
沒有 `7z.dll` 也沒有 `ifwebp.spi`，請先補齊。**Hamana 是 32-bit 程式，三者都必須用 32-bit 版。**

| 元件 | 下載 | 放到 |
|---|---|---|
| `7z.dll` | 7-Zip 24.09 32-bit 安裝檔 [`7z2409.exe`](https://www.7-zip.org/a/7z2409.exe) | `DefaultSPIs\7z.dll` |
| `ax7z.spi` | [`ax7z-0.7-457y3b6.zip`](https://yakex.dev/junks/dist/ax7z-0.7-457y3b6.zip)（v0.7 y3b6） | `DefaultSPIs\ax7z.spi`（取代原本的舊版） |
| `ifwebp.spi` | [uyjulian/ifwebp Releases](https://github.com/uyjulian/ifwebp/releases) 的 **Win32 (Intel 32-bit GCC)**，即 `ifwebp.intel32.gcc.7z`（v2.0.6） | `DefaultSPIs\ifwebp.spi` |

- **7z.dll**：執行 `7z2409.exe` 安裝後，從安裝目錄（64-bit Windows 上是 `C:\Program Files (x86)\7-Zip`）
  複製 `7z.dll`。**不要用 `7z2409-x64.exe` 或 64-bit 7-Zip 的 `7z.dll`**，32-bit 的 Hamana 載入不了。
- **ax7z.spi**：解開 zip 取出 `ax7z.spi`。建議先把原本的 `DefaultSPIs\ax7z.spi` 改名備份（例如 `ax7z.spi_old`）再放入。
  ax7z 會優先使用同資料夾的 `7z.dll`。
- **ifwebp.spi**：`.7z` 檔可用剛裝好的 7-Zip 解開。不要選 `intel64` 或 `arm` 版。

`apply_patches.py` 會檢查 `DefaultSPIs\7z.dll` 是否存在且為 32-bit，不符合時會略過 zip 補丁。

### 2. 套用補丁

```bash
git clone https://github.com/abccbaandy/hamana-patch.git
cd hamana-patch
python apply_patches.py "C:\path\to\Hamana"
```

檢查狀態與還原：

```bash
python apply_patches.py "C:\path\to\Hamana" --check
python apply_patches.py "C:\path\to\Hamana" --revert
```

補丁套用後請**重新啟動 Hamana**。

---

## 補丁內容

| 對象 | 變更 | 效果 |
|---|---|---|
| `Hamana.exe` | UTF-16 字串 `"zip"` → `"zzq"`、`"cbz"` → `"czq"` | 停用內建解壓，交給 `ax7z.spi` + `7z.dll` |
| `Hamana.exe` | hook CRT `_invalid_parameter`（9 bytes）+ stub（85 bytes，放在既有 `int3` 填充區） | EXIF 越界時只放棄該段 EXIF，圖照常顯示 |
| `DefaultSPIs/ifwebp.spi` | 宣告 `*.webp` → `*.webp;*.jpg;*.jpeg` | 接手副檔名錯置的 WebP |
| `Hamana.ini` | `Respect_libjpeg=0` | 上一項的生效前提 |
| `ax7z.spi.ini` | `user_extensions` 追加 `cbz;cbr` | `.cbz` 的配套（7z.dll 清單沒有它） |

### 為什麼有效

Hamana 有三個可介入的接縫：

1. **找不到內建 handler 時，會把檔案交給 Susie SPI。** 內建壓縮格式是用字串常數 `"zip"` / `"cbz"` 認定的，改掉它們就等於停用內建解壓。
2. **SPI 的 `IsSupported` 回報不支援時，Hamana 會自動回退**到下一個解碼器。因此 SPI 可以安全地「多宣告」副檔名——真 JPEG 會被 `ifwebp.spi` 拒絕，然後回退給 libjpeg，不受影響。
3. **EXIF 解析器的三個函式都有標準 C++ EH frame。** 越界時 CRT 會先呼叫 `_invalid_parameter`；在這裡依最內層 SEH 框架判斷「是不是 EXIF 解析中」，是的話就把堆疊還原到該函式的正常出口。非 EXIF 路徑完全照舊。

詳細原理、重現方式與實測資料見 [`docs/`](docs/)。

---

## 實測結果

| 情境 | 補丁前 | 補丁後 |
|---|---|---|
| zip 含 0-byte entry | 崩潰 | 正常，空 entry 自動略過 |
| cbz 含 0-byte entry | 崩潰 | 正常 |
| 瀏覽含問題壓縮檔的資料夾 | 崩潰 `0xC000000D` | 正常 |
| **加密 zip** | **開不了** | **可開啟（ax7z 密碼對話框）** |
| JPEG 的 EXIF 位移越界（11 種致命變形） | 崩潰 `0xC000000D` | 正常，圖照常顯示 |
| 資料夾 / 壓縮檔內混有上述 JPEG | 預讀到就崩潰 | 正常 |
| WebP 內容 + `.jpg` 副檔名 | 讀取失敗 | 正常（VP8 與 VP8X 皆可） |
| 真 JPEG / 真 WebP / rar / 7z / 資料夾 / 單張圖 | 正常 | 正常（無退化） |

加密 zip 那列是意外收穫：`ax7z.txt` §8 把「Hamana 開不了加密 zip」列為 *Susie plugin 端無法對應*的限制，原因正是內建優先。補丁連帶解除了它。

---

## 相容性與注意事項

- 目前只在 **Hamana v1.48（2006/06/19）** 上驗證。patcher 會核對字串簽章，版本不符會拒絕執行，不會弄壞檔案。
- 補丁後 `DefaultSPIs/ax7z.spi` 與 `DefaultSPIs/7z.dll` 成為**必要元件**，請勿刪除 `DefaultSPIs` 資料夾。
- `Respect_libjpeg=0` 會改變真 JPEG 的解碼優先序（每張多一次 SPI 詢問）。實測沒有可見的效能或畫質差異，但未做大量圖的量測。
- 所有變更都會先備份成 `*.orig`，`--revert` 可完整還原。
- 日後解壓或 WebP 再出問題，可直接更換 `DefaultSPIs/` 底下的 `7z.dll` 或 `ifwebp.spi` 版本處理，不必再動 exe。

---

## 疑難排解

### 遇到奇怪的問題：先重建 Hamana.ini

設定錯亂、啟動或顯示異常、補丁後行為不如預期時，可以讓 Hamana 重建設定檔試試：

1. **先關閉 Hamana**（它結束時會寫回 ini，開著改沒用）
2. 把 Hamana 目錄下的 `Hamana.ini` **改名**（例如 `Hamana.ini.bak`），不要直接刪，沒改善還能改回來
3. 啟動 Hamana，再**正常關閉**一次 —— 新的 `Hamana.ini` 是在關閉時才寫出來的
4. 重跑 `python apply_patches.py "C:\path\to\Hamana"`
   新 ini 沒有 `Respect_libjpeg=0`，WebP 修正需要它；已套用的 exe / spi 補丁會自動略過

代價：視窗位置、開啟歷史與自訂設定（縮圖快取位置、同時顯示張數、MipMap 等）都會回到預設值。

ax7z 的設定檔在 `%APPDATA%\ax7z\ax7z.spi.ini`，也可用同樣方式改名重建；之後一樣重跑 `apply_patches.py`
補回 `cbz;cbr`（否則 `.cbz` / `.cbr` 開不了）。若 ax7z 還沒重新產生設定檔，工具會提示改在
ax7z 設定對話框的 User-defined 手動加入 `cbz;cbr`。

---

## 附帶工具

[`tools/hamana_fix.py`](tools/hamana_fix.py) 用於素材端的診斷與修復，適合不想修改 Hamana 安裝的情況：

```bash
python tools/hamana_fix.py diagnose "<壓縮檔或資料夾>"
python tools/hamana_fix.py fix "<壓縮檔>" -o "<輸出目錄>"
```

可偵測 0-byte entry、副檔名／內容不符、EXIF 位移越界，並輸出修正後的檔案（壞 EXIF 會被移除，像素不動）。

[`tools/context_menu.ps1`](tools/context_menu.ps1) 在檔案總管右鍵選單加入「使用 Hamana 開啟」（圖片與 zip/rar/7z 等壓縮檔）。只寫目前使用者的登錄檔，不需系統管理員權限，也不改預設開啟程式：

```powershell
.\tools\context_menu.ps1 -HamanaPath "<Hamana>\Hamana.exe"   # 安裝
.\tools\context_menu.ps1 -Uninstall                          # 移除
```

Hamana 搬家後重新執行安裝指令即可更新路徑；Windows 11 的選單需點「顯示其他選項」。

---

## 文件

| 文件 | 內容 |
|---|---|
| [`docs/bugs.md`](docs/bugs.md) | 各 bug 的根因與完整重現矩陣 |
| [`docs/solutions.md`](docs/solutions.md) | 修復方案分層（L0–L5），含未採用方案的理由 |
| [`docs/hamana-internals.md`](docs/hamana-internals.md) | 二進位事實、三層分派規則、閃退定位（debugger）與沙箱測試工法 |

---

## 授權

本專案**自身的程式碼與文件**採用 [MIT License](LICENSE)。

**Hamana 本體不包含在本專案中，也不由本專案散布。**
Hamana 的著作權屬於原作者 Miyano Makito，其說明檔聲明為免費軟體但保留著作權，
未授予再配布權。請自行從官方管道取得 Hamana。

同理，`ax7z.spi`、`ifwebp.spi`、`7z.dll` 等 Susie plugin 各有其授權與作者，
本專案僅提供修改它們的工具，不散布這些檔案本身。

## 免責

本工具會修改你的 Hamana 安裝檔案。雖然所有變更都有備份且可還原，
**使用風險由使用者自負**。建議先備份整個 Hamana 目錄。

本專案與 Hamana 原作者無關，也未經其背書。
