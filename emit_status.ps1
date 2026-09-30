# emit_status.ps1 — タスクスケジューラから emit_status.py を無人で回す
# ★2026-08-21 作成（人的介在W1 実装③）
# 既存2本（AI_ichiro_memory_backup / AI_ichiro_dashboards_weekly）と同じ形にそろえてある。
$ErrorActionPreference = "Continue"
$app = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $app
$log = Join-Path $app "data\emit_status_task.log"
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
try {
    $out = & python "$app\emit_status.py" 2>&1
    Add-Content -Path $log -Value "[$stamp] rc=$LASTEXITCODE`r`n$out" -Encoding UTF8
} catch {
    Add-Content -Path $log -Value "[$stamp] ERR $($_.Exception.Message)" -Encoding UTF8
}
exit 0