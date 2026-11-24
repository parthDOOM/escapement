# Bench helper on Windows: Pico W host (MicroPython, over USB serial) + Tang Primer 25K Dock (its
# USB debugger passed to WSL with usbipd-win). See README.md.
#
#   .\bench.ps1 bundles               # firmware bundles from this checkout (build.sh bundles, in WSL)
#   .\bench.ps1 deploy                # bring-up files + bundles onto the Pico, verified by sha256
#   .\bench.ps1 load pinid|chip       # bitstream into the Dock's SRAM (openFPGALoader in WSL)
#   .\bench.ps1 pinid                 # wiring check (pinid.fs must be loaded)
#   .\bench.ps1 bringup [-Opts "keep_going=True"]   # bring-up stages 0-5 (chip.fs loaded)
#   .\bench.ps1 proof                 # copy the Pico's proof.json (stage 5 telemetry) into build\
#   .\bench.ps1 eth [-Opts "count=30"]   # 10BASE-T frames from eth_tx at 40 MHz (README.md, Ethernet)
#   .\bench.ps1 ethrx [-Opts "seconds=60"]   # 10BASE-T frames into eth_rx (the receive bench)
#   .\bench.ps1 ethedges              # receive line check: high and low times on RXP
#   .\bench.ps1 swd                   # swd_idcode debugs a second Pico (README.md, SWD)
#   .\bench.ps1 late                  # programs that ask for the impossible are refused (README.md, The chip refuses)
#   .\bench.ps1 learn [REC.sr]        # learn a transmitter from the chip's own capture, then run it (README.md, Learned on the bench)
#   .\bench.ps1 usb ["more text"]     # the chip as a USB keyboard on this laptop types a line (README.md, USB keyboard)
#   .\bench.ps1 repl                  # MicroPython REPL (Ctrl-] to leave)
#
# Environment (all optional): MPREMOTE (default: mpremote on PATH, else the one pip put in
# Python's Scripts folder: python -m pip install mpremote), ESC_WSL_DISTRO (default: the
# default distro), OSS_CAD_SUITE (an OSS CAD Suite folder inside WSL, else the tools on its PATH).
# The Pico is found by its USB ID (2E8A:0005); -Port COMx overrides, and is needed when a
# second MicroPython Pico is plugged in.
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
if (-not $env:MPREMOTE -and -not (Get-Command $Mp -ErrorAction SilentlyContinue)) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) {
        $s = Join-Path (& $py.Source -c "import sysconfig; print(sysconfig.get_path('scripts'))") "mpremote.exe"
        if (Test-Path $s) { $Mp = $s }
    }
}
$Build = Join-Path $Here "build"
$EthImport = "import gc; import bringup; gc.collect(); import ethbench; gc.collect()"

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
    $hits = @($devs | Where-Object { $_ -match "2e8a:0005" })
    if (-not $hits) { throw "no MicroPython Pico (USB 2E8A:0005) found; mpremote devs:`n$($devs -join "`n")" }
    # a second MicroPython Pico (an SWD target, a logic analyser) must never be taken for
    # the bench host by its place in the list
    if ($hits.Count -gt 1) { throw "$($hits.Count) MicroPython Picos ($(($hits | ForEach-Object { ($_ -split '\s+')[0] }) -join ', ')): name the bench host with -Port COMx" }
    return ($hits[0] -split "\s+")[0]
}

function Invoke-Mp { param([string[]]$A) & $Mp connect (Find-Pico) @A; if ($LASTEXITCODE) { throw "mpremote $A failed ($LASTEXITCODE)" } }

function Deploy-Files {
    $files = @(
        @{ src = "$Repo\sw\rp2350\bringup.py"; dst = "bringup.py" },
        @{ src = "$Repo\sw\eschost\protocol.py"; dst = "protocol.py" },
        @{ src = "$Here\barepico.py"; dst = "barepico.py" },
        @{ src = "$Here\pinid.py"; dst = "pinid.py" },
        @{ src = "$Here\ethbench.py"; dst = "ethbench.py" },
        @{ src = "$Here\swdbench.py"; dst = "swdbench.py" },
        @{ src = "$Here\usbbench.py"; dst = "usbbench.py" },
        @{ src = "$Here\latebench.py"; dst = "latebench.py" },
        @{ src = "$Here\learnbench.py"; dst = "learnbench.py" },
        @{ src = "$Repo\sw\rp2350\escapement_demo.py"; dst = "escapement_demo.py" }
    )
    $bdir = Join-Path $Build "bundles"
    if (-not (Test-Path $bdir)) { throw "no ${bdir}: run .\bench.ps1 bundles first" }
    # a bundle's long phase and fold lists sit beside it in <name>.phases and .folds (make_bundle.py)
    foreach ($b in Get-ChildItem $bdir | Where-Object { $_.Extension -in ".json", ".phases", ".folds" }) {
        $files += @{ src = $b.FullName; dst = "bundles/" + $b.Name }
    }
    # the refusal bench's programs, kept apart from the bundles stage 5 runs
    $rdir = Join-Path $Build "refused"
    if (Test-Path $rdir) {
        foreach ($b in Get-ChildItem $rdir -Filter *.json) { $files += @{ src = $b.FullName; dst = "refused/" + $b.Name } }
    }
    $port = Find-Pico
    & $Mp connect $port exec "import os`nfor d in ('bundles', 'refused'):`n try:`n  os.mkdir(d)`n except OSError:`n  pass"
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
        # the MANIFEST's commit from Windows' git: WSL's git cannot follow a worktree made here
        $c = git -C $Repo rev-parse HEAD; $br = git -C $Repo rev-parse --abbrev-ref HEAD
        git -C $Repo diff --quiet HEAD -- sw firmware
        $m = if ($LASTEXITCODE) { ", sw/firmware modified" } else { "" }
        Invoke-Wsl ("ESC_REPO='$c ($br)$m' bash '" + (To-Wsl "$Here\build.sh") + "' bundles")
        if ($LASTEXITCODE) { throw "bundle build failed" }
    }
    "deploy" { Deploy-Files }
    "pinid" { Invoke-Mp @("exec", "import pinid; pinid.run()") }
    "bringup" {
        # the RP's default 125 MHz, which the PWM divides exactly to 25 MHz: an earlier eth
        # run leaves the system clock at 160 MHz until the Pico resets
        $o = "io=barepico.open_io(sys_hz=125000000)"; if ($Opts) { $o += ", " + $Opts }
        Invoke-Mp @("exec", "import barepico, bringup; bringup.run($o)")
    }
    # bringup.py is compiled on the Pico; imported first, with a collection after it and after
    # ethbench, the heap keeps a block large enough for ethbench's GPIO tables (22 Nov: an
    # 8,000-byte table failed with a plain "import ethbench", 15 KB largest block against 44 KB)
    "eth" { Invoke-Mp @("exec", "$EthImport; ethbench.run($Opts)") }
    "ethrx" { Invoke-Mp @("exec", "$EthImport; ethbench.listen($Opts)") }
    "ethedges" { Invoke-Mp @("exec", "$EthImport; ethbench.edges($Opts)") }
    "ethping" { Invoke-Mp @("exec", "$EthImport; ethbench.ping($Opts)") }
    "swd" { Invoke-Mp @("exec", "import swdbench; swdbench.run($Opts)") }
    "late" { Invoke-Mp @("exec", "import latebench; latebench.run($Opts)") }
    "learn" {
        # README.md, "Learned on the bench": a real transmitter's recording (default: the
        # PT2262 socket remote from sigrok-dumps) played onto uio[2]; the chip's capture of
        # it learned here; the generated receiver and transmitter run on the chip
        $py = (Get-Command python -ErrorAction Stop).Source
        $L = Join-Path $Build "learn"
        New-Item -ItemType Directory -Force $L | Out-Null
        $rec = if ($Arg) { $Arg } else { Join-Path $Repo "sw\tests\data\sigrok\pt2262_abcd.sr" }
        $word = "0a8888"                     # an address the recording never has
        & $py "$Here\learnhost.py" source $rec "$L\source.json"
        if ($LASTEXITCODE) { throw "learnhost source failed" }
        $port = Find-Pico
        & $Mp connect $port exec "import os`ntry:`n os.mkdir('learn')`nexcept OSError:`n pass"
        & $Mp connect $port fs --no-verbose cp "$L\source.json" ":learn/source.json"
        Invoke-Mp @("exec", "import gc; import bringup; gc.collect(); import learnbench; learnbench.capture()")
        & $Mp connect $port fs --no-verbose cp ":learn/capture.json" "$L\capture.json"
        & $py "$Here\learnhost.py" learn "$L\capture.json" $L --source-doc "$L\source.json"
        if ($LASTEXITCODE) { throw "learnhost learn failed" }
        foreach ($f in "learned_tx.json", "learned_rx.json") {
            & $Mp connect $port fs --no-verbose cp "$L\$f" ":learn/$f"
        }
        Invoke-Mp @("exec", "import gc; import bringup; gc.collect(); import learnbench; learnbench.receive()")
        Invoke-Mp @("exec", "import gc; import bringup; gc.collect(); import learnbench; learnbench.transmit(0x$word)")
        & $Mp connect $port fs --no-verbose cp ":learn/sent.json" "$L\sent.json"
        & $py "$Here\learnhost.py" judge "$L\capture.json" "$L\sent.json" $word
        if ($LASTEXITCODE) { throw "learnhost judge failed" }
    }
    "usb" {
        # The chip types into the focused window, which must be this one; a job watches
        # Windows' device list for the keyboard and its product string.
        $line = "escapement usb_hid " + (Get-Random -Minimum 100000 -Maximum 999999)
        if ($Arg) { $line += " " + $Arg }        # more text to type (letters, digits, space, .,-=;/)
        $typed = Join-Path $env:TEMP "escapement-usb-typed.txt"
        Remove-Item $typed -ErrorAction SilentlyContinue
        $rd = "`$Host.UI.RawUI.WindowTitle = 'escapement keyboard'; " +
              "`$l = Read-Host 'the chip types here'; Set-Content -Encoding ascii -Path '$typed' -Value `$l"
        $win = Start-Process powershell -PassThru -ArgumentList @("-NoProfile", "-Command", $rd)
        $watch = Start-Job {
            $t0 = Get-Date; $seen = $null
            while (((Get-Date) - $t0).TotalSeconds -lt 40 -and -not $seen) {
                $d = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
                    Where-Object { $_.InstanceId -match "^USB\\VID_1209&PID_0001" } | Select-Object -First 1
                if ($d) {
                    $desc = (Get-PnpDeviceProperty -InstanceId $d.InstanceId -KeyName DEVPKEY_Device_BusReportedDeviceDesc -ErrorAction SilentlyContinue).Data
                    $seen = "after {0:N1} s: {1} ({2}), status {3}, product string '{4}'" -f ((Get-Date) - $t0).TotalSeconds, $d.FriendlyName, $d.InstanceId, $d.Status, $desc
                }
                Start-Sleep -Milliseconds 500
            }
            if ($seen) { "windows: keyboard enumerated $seen" } else { "windows: no USB\VID_1209&PID_0001 device appeared in 40 s" }
        }
        $o = "'" + $line + "\n'"; if ($Opts) { $o += ", " + $Opts }
        try { Invoke-Mp @("exec", "import gc; import bringup; gc.collect(); import usbbench; gc.collect(); usbbench.run($o)") }
        finally {
            Write-Output (Receive-Job $watch -Wait -AutoRemoveJob)
            for ($i = 0; $i -lt 20 -and -not (Test-Path $typed); $i++) { Start-Sleep -Milliseconds 250 }
            if (-not $win.HasExited) { Stop-Process -Id $win.Id -ErrorAction SilentlyContinue }
        }
        $got = if (Test-Path $typed) { (Get-Content $typed -Raw).Trim() } else { $null }
        if ($got -eq $line) { Write-Output "USB: PASS  the window received '$got', the line the chip was given" }
        else { Write-Output "USB: FAIL  expected '$line', the window received '$got'" }
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
    default { Get-Content $PSCommandPath -TotalCount 20 | Select-Object -Skip 1 }
}
