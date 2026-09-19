# Hamana Patch

修正 [Hamana](https://www.vector.co.jp/soft/win95/art/se311115.html) v1.48（2006/06/19）兩個會造成**閃退**與**讀圖失敗**的缺陷。

補丁只改寫 binary 內的字串常數，**不修改任何程式碼**，檔案大小不變，可完整還原。

> **本專案不包含 Hamana 本體**，請自備。這裡提供的是 patcher 與研究文件。

<details>
<summary>English summary</summary>

Bug-fix patches for Hamana v1.48 (2006), a Direct3D-based image viewer for Windows.

- **Crash on opening archives**: Hamana's built-in zip handler (minizip 0.15 / zlib 1.1.4) crashes on zero-byte entries. The patch disables the built-in handler so archives are routed to `ax7z.spi` + a modern `7z.dll` instead. Encrypted zip support comes along for free.
- **Images failing to load**: Hamana dispatches decoders by *file extension*, never by content. WebP files named `.jpg` are handed to libjpeg and fail. The patch makes `ifwebp.spi` also claim `*.jpg`, relying on Hamana's automatic fallback when a Susie plugin reports the format as unsupported.

Both patches only rewrite string constants — no code is modified, file size is unchanged, and `--revert` restores the originals. **Hamana itself is not distributed here.**

</details>

---

## 修正的問題

### 1. 開啟壓縮檔後數秒閃退

壓縮檔內只要有一個 **0 位元組的 entry**，Hamana 就會崩潰。

Hamana 對 `.zip` / `.cbz` 走的是自己內建的解壓常式（**minizip 0.15 / 1998 + zlib 1.1.4 / 2002**），不是 `ax7z.spi`，所以更新任何第三方 lib 都修不了。

影響範圍比想像中大——**光是「瀏覽」含問題壓縮檔的資料夾就會崩潰**（`0xC000000D`），不必真的開啟它。

### 2. 部分圖檔讀取失敗（顯示 error）

**Hamana 依副檔名分派解碼器，完全不看檔案內容。** 副檔名為 `.jpg` 的檔案一律送進內建 libjpeg，內容若是 WebP 就會失敗：

```
Not a JPEG file: starts with 0x52 0x49
```

`0x52 0x49` 是 `RI`，也就是 WebP 的 `RIFF` 標頭。這類檔案本身完全正常，只是「穿錯衣服」。

---

## 快速開始

需求：Python 3.8+、Windows、Hamana v1.48。

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
| `DefaultSPIs/ifwebp.spi` | 宣告 `*.webp` → `*.webp;*.jpg;*.jpeg` | 接手副檔名錯置的 WebP |
| `Hamana.ini` | `Respect_libjpeg=0` | 上一項的生效前提 |
| `ax7z.spi.ini` | `user_extensions` 追加 `cbz;cbr` | `.cbz` 的配套（7z.dll 清單沒有它） |

### 為什麼有效

Hamana 有兩個可介入的接縫：

1. **找不到內建 handler 時，會把檔案交給 Susie SPI。** 內建壓縮格式是用字串常數 `"zip"` / `"cbz"` 認定的，改掉它們就等於停用內建解壓。
2. **SPI 的 `IsSupported` 回報不支援時，Hamana 會自動回退**到下一個解碼器。因此 SPI 可以安全地「多宣告」副檔名——真 JPEG 會被 `ifwebp.spi` 拒絕，然後回退給 libjpeg，不受影響。

詳細原理、重現方式與實測資料見 [`docs/`](docs/)。

---

## 實測結果

| 情境 | 補丁前 | 補丁後 |
|---|---|---|
| zip 含 0-byte entry | 崩潰 | 正常，空 entry 自動略過 |
| cbz 含 0-byte entry | 崩潰 | 正常 |
| 瀏覽含問題壓縮檔的資料夾 | 崩潰 `0xC000000D` | 正常 |
| **加密 zip** | **開不了** | **可開啟（ax7z 密碼對話框）** |
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

## 附帶工具

[`tools/hamana_fix.py`](tools/hamana_fix.py) 用於素材端的診斷與修復，適合不想修改 Hamana 安裝的情況：

```bash
python tools/hamana_fix.py diagnose "<壓縮檔或資料夾>"
python tools/hamana_fix.py fix "<壓縮檔>" -o "<輸出目錄>"
```

可偵測 0-byte entry 與副檔名／內容不符，並輸出修正後的檔案。

---

## 文件

| 文件 | 內容 |
|---|---|
| [`docs/bugs.md`](docs/bugs.md) | 兩個 bug 的根因與完整重現矩陣 |
| [`docs/solutions.md`](docs/solutions.md) | 修復方案分層（L0–L5），含未採用方案的理由 |
| [`docs/hamana-internals.md`](docs/hamana-internals.md) | 二進位事實、三層分派規則、沙箱測試工法 |

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
