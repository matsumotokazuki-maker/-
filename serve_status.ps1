# serve_status.ps1 - status配信サーバを常駐させる（タスクスケジューラから）
# 2026-08-21 作成（人的介在W1 実装2の経路・E案）
# ★アプリ（8501）には一切 触らない。別プロセス・別ポート。
$ErrorActionPreference = "Continue"
$app = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $app
$log = Join-Path $app "data\serve_status.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path $log -Value "[$stamp] start" -Encoding UTF8
try {
    & python "$app\serve_status.py" --port 8600 2>&1 | Add-Content -Path $log -Encoding UTF8
} catch {
    Add-Content -Path $log -Value "[$stamp] ERR $($_.Exception.Message)" -Encoding UTF8
}
exit 0