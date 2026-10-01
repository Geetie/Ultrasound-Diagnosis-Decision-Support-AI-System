$ErrorActionPreference = "Continue"
$r = Invoke-WebRequest -Uri "https://gitcode.com/api/v5/repos/open-source-toolkit/44edd/contents" -TimeoutSec 30 -UseBasicParsing
$j = $r.Content | ConvertFrom-Json
foreach ($f in $j) {
    Write-Host ("{0}  {1}B" -f $f.name, $f.size)
    Write-Host ("  dl: " + $f.download_url)
}
