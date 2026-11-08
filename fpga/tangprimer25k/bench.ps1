# Bench helper on Windows: Pico W host (MicroPython, over USB serial) + Tang Primer 25K Dock (its
# USB debugger passed to WSL with usbipd-win). See README.md.
#
#   .\bench.ps1 bundles               # firmware bundles from this checkout (build.sh bundles, in WSL)
#   .\bench.ps1 deploy                # bring-up files + bundles onto the Pico, verified by sha256
#   .\bench.ps1 load pinid|chip       # bitstream into the Dock's SRAM (openFPGALoader in WSL)
#   .\bench.ps1 pinid                 # wiring check (pinid.fs must be loaded)
#   .\bench.ps1 bringup [-Opts "keep_going=True"]   # bring-up stages 0-5 (chip.fs loaded)
#   .\bench.ps1 proof                 # copy the Pico's proof.json (stage 5 telemetry) into build\
#   .\bench.ps1 repl                  # MicroPython REPL (Ctrl-] to leave)
#
# Environment (all optional): MPREMOTE (default: mpremote on PATH), ESC_WSL_DISTRO (default: the
# default distro), OSS_CAD_SUITE (an OSS CAD Suite folder inside WSL, else the tools on its PATH).
# The Pico is found by its USB ID (2E8A:0005); -Port COMx overrides.
param(
    [Parameter(Position = 0)][string]$Cmd = "help",
    [Parameter(Position = 1)][string]$Arg = "",
    [string]$Port = "",
    [string]$Opts = ""
)
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Repo = (Resolve-Path (Join-Path $Here "..\..")).Path
$Mp = if ($env:MPREMOTE) { $env:MPREMOTE } else { "mpremote" }
$Build = Join-Path $Here "build"

function Invoke-Wsl([string]$script) {
    $env_ = if ($env:OSS_CAD_SUITE) { "source '$($env:OSS_CAD_SUITE)/environment' >/dev/null 2>&1; " } else { "" }
    $a = @()
    if ($env:ESC_WSL_DISTRO) { $a += @("-d", $env:ESC_WSL_DISTRO) }
    & wsl.exe @a bash -c ($env_ + $script)
}
function To-Wsl([string]$p) { (& wsl.exe wslpath -a ($p -replace '\\', '/')).Trim() }

function Find-Pico {
    if ($Port) { return $Port }
    $devs = & $Mp devs
    $hit = $devs | Where-Object { $_ -match "2e8a:0005" } | Select-Object -First 1
    if (-not $hit) { throw "no MicroPython Pico (USB 2E8A:0005) found; mpremote devs:`n$($devs -join "`n")" }
    return ($hit -split "\s+")[0]
}

function Invoke-Mp { param([string[]]$A) & $Mp connect (Find-Pico) @A; if ($LASTEXITCODE) { throw "mpremote $A failed ($LASTEXITCODE)" } }

function Deploy-Files {
    $files = @(
        @{ src = "$Repo\sw\rp2350\bringup.py"; dst = "bringup.py" },
        @{ src = "$Repo\sw\eschost\protocol.py"; dst = "protocol.py" },
        @{ src = "$Here\barepico.py"; dst = "barepico.py" },
        @{ src = "$Here\pinid.py"; dst = "pinid.py" }
    )
    $bdir = Join-Path $Build "bundles"
    if (-not (Test-Path $bdir)) { throw "no ${bdir}: run .\bench.ps1 bundles first" }
    foreach ($b in Get-ChildItem $bdir -Filter *.json) { $files += @{ src = $b.FullName; dst = "bundles/" + $b.Name } }
    $port = Find-Pico
    & $Mp connect $port exec "import os`ntry:`n os.mkdir('bundles')`nexcept OSError:`n pass"
    foreach ($f in $files) {
        & $Mp connect $port fs --no-verbose cp $f.src (":" + $f.dst)
        if ($LASTEXITCODE) { throw "copy of $($f.dst) failed" }
    }
    # verify every file by sha256, in one session
    # mpremote prints "sha256sum :<path>" and then the hex digest on the next line
    $remote = @(& $Mp connect $port fs sha256sum ($files | ForEach-Object { ":" + $_.dst }))
    $got = @{}
    for ($i = 0; $i -lt $remote.Count - 1; $i++) {
        if ($remote[$i] -match "^sha256sum :(.+)$") { $got[$Matches[1].Trim()] = $remote[$i + 1].Trim().ToLower() }
    }
    $bad = 0
    foreach ($f in $files) {
        $h = (Get-FileHash $f.src -Algorithm SHA256).Hash.ToLower()
        if ($got[$f.dst] -ne $h) { Write-Output "MISMATCH $($f.dst): local $h, Pico $($got[$f.dst])"; $bad++ }
    }
    if ($bad) { throw "$bad file(s) differ on the Pico" }
    Write-Output "deployed and verified $($files.Count) files on $port"
}

switch ($Cmd) {
    "bundles" {
        Invoke-Wsl ("bash '" + (To-Wsl "$Here\build.sh") + "' bundles")
        if ($LASTEXITCODE) { throw "bundle build failed" }
    }
    "deploy" { Deploy-Files }
    "pinid" { Invoke-Mp @("exec", "import pinid; pinid.run()") }
    "bringup" {
        $o = "io=barepico.open_io()"; if ($Opts) { $o += ", " + $Opts }
        Invoke-Mp @("exec", "import barepico, bringup; bringup.run($o)")
    }
    "proof" {
        New-Item -ItemType Directory -Force $Build | Out-Null
        $dst = Join-Path $Build ("proof-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
        Invoke-Mp @("fs", "cp", ":proof.json", $dst)
        Write-Output "saved $dst"
    }
    "load" {
        if ($Arg -notin @("pinid", "chip")) { throw "load pinid|chip" }
        Invoke-Wsl ("openFPGALoader -b tangprimer25k '" + (To-Wsl "$Build\$Arg\$Arg.fs") + "'")
        if ($LASTEXITCODE) { throw "openFPGALoader failed: is the Dock attached to WSL (usbipd attach --wsl --busid <id>)?" }
    }
    "repl" { & $Mp connect (Find-Pico) repl }
    default { Get-Content $PSCommandPath -TotalCount 15 | Select-Object -Skip 1 }
}
