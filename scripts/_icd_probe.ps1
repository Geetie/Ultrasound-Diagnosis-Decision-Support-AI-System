$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$r = Invoke-WebRequest -Uri "https://gitcode.com/api/v5/repos/open-source-toolkit/44edd/contents" -TimeoutSec 30 -UseBasicParsing
Write-Host $r.Content.Substring(0, [Math]::Min(800, $r.Content.Length))
