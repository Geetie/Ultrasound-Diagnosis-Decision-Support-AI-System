$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$r = Invoke-WebRequest -Uri "https://raw.githubusercontent.com/Qingyanyichen/TN5000-2025/main/README.md" -TimeoutSec 30 -UseBasicParsing
$c = $r.Content
# 截取数据下载相关段落
$idx = $c.ToLower().IndexOf("download")
if ($idx -lt 0) { $idx = $c.ToLower().IndexOf("dataset") }
Write-Host $c.Substring([Math]::Max(0, $idx - 100), [Math]::Min(1500, $c.Length - [Math]::Max(0, $idx - 100)))
