param([switch]$NoBrowser, [ValidateRange(1,65535)][int]$Port = 18789)
$ErrorActionPreference = 'Stop'
$siteRoot = $PSScriptRoot
$siteUrl = 'http://127.0.0.1:' + $Port
$siteReady = $false
$siteHealth = $null
try { $siteHealth = Invoke-RestMethod -Uri ($siteUrl + '/api/health') -TimeoutSec 2 } catch { }
if ($siteHealth) {
    if ($siteHealth.app -ne 'position-guide' -or $siteHealth.project_root -ne $siteRoot) {
        throw "Port $Port belongs to another checkout or application. Use -Port 18790. No process was stopped."
    }
    $siteReady = $true
}
if (-not $siteReady) {
    if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
        throw "Port $Port is occupied. Use -Port 18790. No process was stopped."
    }
    $sitePython = $null
    foreach ($candidate in @('python.exe', 'py.exe')) {
        $launcher = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $launcher -or $launcher.Source -like '*\WindowsApps\*') { continue }
        try {
            $probe = & $launcher.Source -c "import json,sys; assert sys.version_info >= (3, 10); print(json.dumps(sys.executable))" 2>$null
            if ($LASTEXITCODE -eq 0 -and $probe) {
                $resolved = $probe | ConvertFrom-Json
                if (Test-Path -LiteralPath $resolved) {
                    $sitePython = $resolved
                    break
                }
            }
        } catch { }
    }
    if (-not $sitePython) {
        throw 'Python 3.10+ is required. Install it from https://www.python.org/downloads/ and enable Add Python to PATH, then reopen this launcher.'
    }
    $siteData = Join-Path $siteRoot 'data'
    New-Item -ItemType Directory -Path $siteData -Force | Out-Null
    $siteScript = Join-Path $siteRoot 'server.py'
    $siteProcess = Start-Process -FilePath $sitePython -ArgumentList ('"' + $siteScript + '" --port ' + $Port) -WorkingDirectory $siteRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $siteData 'server.stdout.log') -RedirectStandardError (Join-Path $siteData 'server.stderr.log')
    for ($siteAttempt = 0; $siteAttempt -lt 40; $siteAttempt++) {
        if ($siteProcess.HasExited) { break }
        Start-Sleep -Milliseconds 250
        try {
            $siteHealth = Invoke-RestMethod -Uri ($siteUrl + '/api/health') -TimeoutSec 2
            if ($siteHealth.app -eq 'position-guide' -and $siteHealth.project_root -eq $siteRoot) { $siteReady = $true; break }
        } catch { }
    }
    if (-not $siteReady) { throw 'Server startup failed. Read data/server.stderr.log or run python server.py to inspect the error.' }
    Set-Content -LiteralPath (Join-Path $siteRoot 'data\server.pid') -Value $siteProcess.Id -Encoding Ascii
}
if (-not $NoBrowser) { Start-Process $siteUrl }
Write-Output ('Xianjue 1.0 is ready: ' + $siteUrl)
