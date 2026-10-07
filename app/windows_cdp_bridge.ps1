# WSL integration only. Chrome listens on Windows loopback; binary stdin/stdout
# carries its owned websocket. No LAN/NAT listener, firewall changes or URL proxy.
param([switch]$InspectOnly)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$outStream = [Console]::OpenStandardOutput()
$lock = $null
$client = $null
$stage = 'discover_chrome'
$readySent = $false
function Send-Status($value) {
    $bytes = [Text.Encoding]::UTF8.GetBytes(($value | ConvertTo-Json -Compress) + "`n")
    $outStream.Write($bytes, 0, $bytes.Length)
    $outStream.Flush()
}
try {
    if ($InspectOnly) {
        Send-Status @{ status = 'inspection_only'; transport = 'binary_stdio'; launches = 0 }
        exit 0
    }
    Add-Type -AssemblyName System.Net.Http
    $candidates = @(
        (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
        (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe')
    )
    $chrome = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $chrome) { throw 'chrome_not_found' }
    $profile = Join-Path $env:LOCALAPPDATA 'HuellaBrowser'
    [void][IO.Directory]::CreateDirectory($profile)
    $stage = 'lock_dedicated_profile'
    $lock = [IO.File]::Open((Join-Path $profile 'huella-launch.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    $markerPath = Join-Path $profile 'huella-owner.json'
    $portPath = Join-Path $profile 'DevToolsActivePort'
    $owner = $null
    if (Test-Path -LiteralPath $markerPath) {
        $owner = [IO.File]::ReadAllText($markerPath) | ConvertFrom-Json
    }
    $running = $null
    if ($owner -and $owner.pid) {
        $running = Get-Process -Id $owner.pid -ErrorAction SilentlyContinue
        if ($running -and ($running.Path -ne $chrome -or
            $running.StartTime.ToUniversalTime().ToString('o') -ne $owner.started)) { throw 'ownership_mismatch' }
    }
    if (-not $running) {
        $stage = 'launch_dedicated_chrome'
        $launched = Start-Process -FilePath $chrome -ArgumentList @(
            ('--user-data-dir="' + $profile + '"'),
            '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
            '--no-first-run', '--no-default-browser-check', '--new-window', 'about:blank'
        ) -PassThru
        $deadline = [DateTime]::UtcNow.AddSeconds(15)
        $lines = $null
        while ([DateTime]::UtcNow -lt $deadline) {
            $launched.Refresh()
            if ($launched.HasExited) { throw 'dedicated_profile_in_use' }
            if (Test-Path -LiteralPath $portPath) {
                $lines = [IO.File]::ReadAllLines($portPath)
                $fresh = (Get-Item -LiteralPath $portPath).LastWriteTimeUtc -ge $launched.StartTime.ToUniversalTime()
                if ($fresh -and $lines.Count -ge 2 -and (-not $owner -or $lines[1] -ne $owner.path)) { break }
            }
            Start-Sleep -Milliseconds 200
        }
        if (-not $fresh -or -not $lines -or $lines.Count -lt 2 -or ($owner -and $lines[1] -eq $owner.path)) { throw 'cdp_not_ready' }
        $owner = @{ pid = $launched.Id; started = $launched.StartTime.ToUniversalTime().ToString('o'); port = [int]$lines[0]; path = $lines[1]; version = 1 }
        [IO.File]::WriteAllText($markerPath, ($owner | ConvertTo-Json -Compress), [Text.Encoding]::UTF8)
    }
    # Profile port file and websocket identity must match our launch marker.
    $stage = 'verify_owned_endpoint'
    $current = [IO.File]::ReadAllLines($portPath)
    if ($owner.version -ne 1 -or $owner.port -lt 1024 -or $owner.port -gt 65535 -or
        $owner.path -notmatch '^/devtools/browser/[a-fA-F0-9-]{36}$' -or
        $current.Count -lt 2 -or [int]$current[0] -ne $owner.port -or $current[1] -ne $owner.path) {
        throw 'ownership_mismatch'
    }
    $handler = New-Object System.Net.Http.HttpClientHandler
    $handler.UseProxy = $false
    $handler.AllowAutoRedirect = $false
    $http = New-Object System.Net.Http.HttpClient($handler)
    $http.Timeout = [TimeSpan]::FromSeconds(3)
    try {
        $version = $http.GetStringAsync(('http://127.0.0.1:' + $owner.port + '/json/version')).GetAwaiter().GetResult() | ConvertFrom-Json
        $endpoint = [Uri]$version.webSocketDebuggerUrl
        if ($endpoint.Scheme -ne 'ws' -or $endpoint.Host -notin @('127.0.0.1', 'localhost') -or
            $endpoint.Port -ne $owner.port -or $endpoint.AbsolutePath -ne $owner.path) { throw 'ownership_mismatch' }
    } finally { $http.Dispose(); $handler.Dispose() }
    $client = New-Object Net.Sockets.TcpClient
    $client.Connect('127.0.0.1', [int]$owner.port)
    $lock.Dispose(); $lock = $null
    Send-Status @{ status = 'ready'; path = $owner.path }
    $readySent = $true
    $stage = 'relay_owned_websocket'
    $network = $client.GetStream()
    $toChrome = [Console]::OpenStandardInput().CopyToAsync($network)
    $toWsl = $network.CopyToAsync($outStream)
    [void][Threading.Tasks.Task]::WaitAny(@($toChrome, $toWsl))
} catch {
    # Never print host paths, exception content, metadata or websocket identity.
    $code = 'windows_bridge_failed'
    if ($_.Exception.Message -in @('chrome_not_found','ownership_mismatch','dedicated_profile_in_use','cdp_not_ready')) {
        $code = $_.Exception.Message
    }
    if (-not $readySent) {
        Send-Status @{ status = 'error'; code = $code; message = ('No se pudo conectar Chrome dedicado en Windows (' + $code + ', etapa ' + $stage + '). Revisa Chrome y WSL; no se usará otro navegador.') }
    }
    exit 1
} finally {
    if ($lock) { $lock.Dispose() }
    if ($client) { $client.Dispose() }
}
