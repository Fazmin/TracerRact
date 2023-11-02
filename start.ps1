# Build and start Asset Tracer in Docker, then open it in your browser.
#
#   .\start.cmd              start (builds on first run) and open http://localhost:8080
#   .\start.cmd --port 9090  use another port
#   .\start.cmd --lan        let other computers on your network open it too
#   .\start.cmd --reset      delete all uploaded data and start again empty
#   .\start.cmd --no-open    don't open a browser
#   .\start.cmd stop         stop it (your data is kept)
#
# Windows version of start.sh. start.cmd runs this script without needing a
# PowerShell execution policy change; .\start.ps1 takes the same options.
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'

$Port = if ($env:PORT) { $env:PORT } else { '8080' }
$Bind = '127.0.0.1'
$Reset = $false
$Open = $true
$Cmd = 'start'
$argv = @($args)
for ($i = 0; $i -lt $argv.Count; $i++) {
    $a = "$($argv[$i])"
    switch -Wildcard ($a) {
        '--port' {
            if ($i + 1 -ge $argv.Count) { [Console]::Error.WriteLine('--port needs a number'); exit 2 }
            $i++; $Port = "$($argv[$i])"
        }
        '--port=*' { $Port = $a.Substring(7) }
        '--lan' { $Bind = '0.0.0.0' }
        '--reset' { $Reset = $true }
        '--no-open' { $Open = $false }
        'stop' { $Cmd = 'stop' }
        { $_ -in '-h', '--help', '/?' } {
            Get-Content -LiteralPath $PSCommandPath -TotalCount 8 | ForEach-Object { $_ -replace '^# ?', '' }
            exit 0
        }
        default { [Console]::Error.WriteLine("Unknown option: $a (see .\start.cmd --help)"); exit 2 }
    }
}
if ($Port -notmatch '^\d+$') { [Console]::Error.WriteLine('--port must be a number'); exit 2 }

function say([string]$Message) { Write-Host '==> ' -NoNewline -ForegroundColor White; Write-Host $Message }
function die([string]$Message) { Write-Host 'Error: ' -NoNewline -ForegroundColor Red; Write-Host $Message; exit 1 }
# True if the command exits with 0. Its output is discarded.
function succeeds([scriptblock]$Command) { & $Command *> $null; $LASTEXITCODE -eq 0 }

# Run from this folder and pass PORT/BIND to docker compose, then put the caller's
# folder and environment back, since .\start.ps1 runs inside their PowerShell session.
$savedEnv = @{ PORT = $env:PORT; BIND = $env:BIND }
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        die 'Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this again.'
    }
    if (-not (succeeds { docker compose version })) {
        die 'Docker Compose is missing. Update Docker Desktop, then run this again.'
    }

    if (-not (succeeds { docker info })) {
        say 'Starting Docker Desktop...'
        # docker.exe lives in <Docker Desktop folder>\resources\bin.
        $desktop = @(
            (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'),
            (Join-Path (Split-Path (Get-Command docker).Source) '..\..\Docker Desktop.exe')
        ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if (-not $desktop) { die 'Could not start Docker Desktop. Start it yourself, then run this again.' }
        Start-Process -FilePath $desktop
        for ($n = 0; $n -lt 120; $n++) {
            if (succeeds { docker info }) { break }
            Start-Sleep -Seconds 1
        }
        if (-not (succeeds { docker info })) { die 'Docker did not start within 2 minutes.' }
    }

    $env:PORT = $Port
    $env:BIND = $Bind
    if ($Cmd -eq 'stop') {
        say 'Stopping Asset Tracer...'
        docker compose down
        exit $LASTEXITCODE
    }
    if ($Reset) {
        say 'Removing the container and all uploaded data...'
        docker compose down -v
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    say 'Building and starting (the first run downloads Python and takes a minute or two)...'
    docker compose up -d --build
    if ($LASTEXITCODE -ne 0) {
        die "Could not start the container. If port $Port is already in use, try: .\start.cmd --port 9090"
    }

    say 'Waiting for the app to be ready...'
    $Url = "http://localhost:$Port"
    $cid = "$(docker compose ps -q explorer)".Trim()
    $ready = $false
    for ($n = 0; $n -lt 120; $n++) {
        # 127.0.0.1 rather than localhost: Windows tries ::1 first, and the port is only published on IPv4.
        try {
            Invoke-WebRequest -Uri "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 5 | Out-Null
            $ready = $true
            break
        } catch { }
        if ("$(docker inspect -f '{{.State.Running}}' $cid 2>$null)".Trim() -ne 'true') { break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        docker compose logs --tail 50 explorer
        die 'The app did not become ready. The log above should say why.'
    }

    Write-Host ''
    Write-Host '  Asset Tracer is running.' -ForegroundColor White
    Write-Host ''
    Write-Host '  Open:  ' -NoNewline; Write-Host $Url -ForegroundColor Cyan
    if ($Bind -eq '0.0.0.0') {
        # The address on the interface that has the default route.
        $ip = try {
            $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction Stop |
                Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1
            Get-NetIPAddress -InterfaceIndex $route.ifIndex -AddressFamily IPv4 -ErrorAction Stop |
                Select-Object -First 1 -ExpandProperty IPAddress
        } catch { $null }
        if ($ip) {
            Write-Host '  From other computers on your network:  ' -NoNewline
            Write-Host "http://${ip}:$Port" -ForegroundColor Cyan
        }
    }
    Write-Host ''
    Write-Host '  Stop it with .\start.cmd stop. Your data is kept between runs.'
    Write-Host ''
    if ($Open) {
        try { Start-Process $Url } catch { }
    }
} finally {
    Pop-Location
    $env:PORT = $savedEnv.PORT
    $env:BIND = $savedEnv.BIND
}
