<#
.SYNOPSIS
  在檔案總管右鍵選單加入／移除「使用 Hamana 開啟」。

.DESCRIPTION
  寫入 HKCU\Software\Classes\SystemFileAssociations\<ext>\shell\OpenWithHamana，
  只影響目前使用者、不需系統管理員權限，也不會改變各副檔名的預設開啟程式。
  SystemFileAssociations 依副檔名掛載，與該副檔名目前綁定哪個 ProgID 無關。

.EXAMPLE
  .\context_menu.ps1 -HamanaPath "D:\Hamana\Hamana.exe"
  .\context_menu.ps1 -Uninstall
#>
param(
    [string]$HamanaPath,
    [switch]$Uninstall,
    [string]$Label = '使用 Hamana 開啟',
    [string[]]$Extensions = @(
        # 圖片
        '.jpg', '.jpeg', '.jpe', '.jfif', '.png', '.gif', '.bmp', '.dib',
        '.tif', '.tiff', '.webp', '.ico', '.wmf', '.emf',
        # 壓縮檔
        '.zip', '.cbz', '.rar', '.cbr', '.7z', '.cb7', '.lzh', '.lha', '.arj', '.tar', '.gz'
    )
)

$ErrorActionPreference = 'Stop'
$KeyName = 'OpenWithHamana'
$Base = 'HKCU:\Software\Classes\SystemFileAssociations'

if ($Uninstall) {
    foreach ($ext in $Extensions) {
        $key = "$Base\$ext\shell\$KeyName"
        if (Test-Path -LiteralPath $key) {
            Remove-Item -LiteralPath $key -Recurse -Force
            Write-Host "removed $ext"
        }
    }
    return
}

if (-not $HamanaPath) { throw '請用 -HamanaPath 指定 Hamana.exe 的完整路徑' }
$exe = (Resolve-Path -LiteralPath $HamanaPath).Path
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "找不到 $exe" }

foreach ($ext in $Extensions) {
    $key = "$Base\$ext\shell\$KeyName"
    New-Item -Path $key -Force | Out-Null
    Set-ItemProperty -LiteralPath $key -Name '(default)' -Value $Label
    Set-ItemProperty -LiteralPath $key -Name 'Icon' -Value "`"$exe`",0"
    New-Item -Path "$key\command" -Force | Out-Null
    Set-ItemProperty -LiteralPath "$key\command" -Name '(default)' -Value "`"$exe`" `"%1`""
    Write-Host "added $ext"
}
Write-Host '完成。若選單沒立即出現，重開檔案總管即可。'
