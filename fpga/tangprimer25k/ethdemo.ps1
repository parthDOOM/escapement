# Ethernet demo for a recording: the chip (the FPGA build on the Dock, clocked by the Pico)
# sends 10BASE-T frames through the RC circuit and a cable to this PC's Ethernet port, and
# the PC's own counters show them arrive. Run as administrator to also capture the frames
# with Windows' pktmon and print the text inside each one.
#
#   .\ethdemo.ps1                  # 20 frames
#   .\ethdemo.ps1 -Frames 50
#   .\ethdemo.ps1 -ParsePcap file  # print the escapement frames in a pcapng file
#
# Every step prints what it checked; the last line says PASS or FAIL.
param(
    [int]$Frames = 20,
    [string]$Nic = "Ethernet",
    [switch]$NoDeploy,
    [string]$ParsePcap = ""
)
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Repo = (Resolve-Path (Join-Path $Here "..\..")).Path
$Bench = Join-Path $Here "bench.ps1"
if (-not $env:OSS_CAD_SUITE) { $env:OSS_CAD_SUITE = "/root/eda/oss-cad-suite" }

function Step([string]$n, [string]$t) { Write-Host ""; Write-Host "=== $n. $t" -ForegroundColor Cyan }
function Ok([string]$t) { Write-Host "    OK   $t" -ForegroundColor Green }
function Info([string]$t) { Write-Host "    $t" }
function Bad([string]$t) { Write-Host "    FAIL $t" -ForegroundColor Red; throw $t }
# pktmon through cmd, so its messages never count as PowerShell errors: the exit code decides
function Pk([string]$a) {
    $o = cmd /c "pktmon $a 2>&1"
    $script:pkOut = ($o | Out-String).Trim()
    return $LASTEXITCODE
}

# The escapement test frames in a pcapng file (Enhanced Packet Blocks): EtherType 0x88B5.
function Read-EscFrames([string]$path) {
    $b = [System.IO.File]::ReadAllBytes($path)
    $out = @()
    $off = 0
    while ($off + 12 -le $b.Length) {
        $type = [BitConverter]::ToUInt32($b, $off)
        $len = [BitConverter]::ToUInt32($b, $off + 4)
        if ($len -lt 12) { break }
        if ($type -eq 6) {
            $cap = [BitConverter]::ToUInt32($b, $off + 20)
            $d = $off + 28
            if ($cap -ge 14 -and $b[$d + 12] -eq 0x88 -and $b[$d + 13] -eq 0xB5) {
                $mac = { param($i) (($b[$i..($i + 5)] | ForEach-Object { $_.ToString("x2") }) -join ":") }
                $t = $d + 14; $e = $t
                while ($e -lt $d + $cap -and $b[$e] -ne 0) { $e++ }
                $out += [pscustomobject]@{
                    Dst = & $mac $d; Src = & $mac ($d + 6); Bytes = $cap
                    Text = [System.Text.Encoding]::ASCII.GetString($b, $t, $e - $t)
                }
            }
        }
        $off += $len
    }
    return $out
}

function Show-Frames($frames) {
    $seen = @{}
    foreach ($f in $frames) {
        if ($seen.ContainsKey($f.Text)) { continue }      # one per capture point
        $seen[$f.Text] = 1
        Info ("{0}  ->  {1}   {2} bytes   ""{3}""" -f $f.Src, $f.Dst, $f.Bytes, $f.Text)
    }
    return $seen.Count
}

if ($ParsePcap) { $n = Show-Frames (Read-EscFrames $ParsePcap); Info "$n distinct frames"; return }

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
$mp = (Get-Command mpremote -ErrorAction SilentlyContinue)
if (-not $mp) {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { $mp = Join-Path (& $py.Source -c "import sysconfig; print(sysconfig.get_path('scripts'))") "mpremote.exe" }
} else { $mp = $mp.Source }

Write-Host "Escapement: 10BASE-T from the chip to this laptop, $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Yellow

Step 1 "The setup"
$commit = (& git -C $Repo rev-parse --short HEAD).Trim()
$dirty = if (& git -C $Repo status --porcelain) { " (with uncommitted changes)" } else { "" }
Info "repository   $commit$dirty"
$fs = Join-Path $Here "build\chip\chip.fs"
if (-not (Test-Path $fs)) { Bad "no bitstream at $fs" }
Info ("bitstream    build\chip\chip.fs  sha256 {0}" -f (Get-FileHash $fs -Algorithm SHA256).Hash.ToLower().Substring(0, 16))
$man = Join-Path $Here "build\chip\MANIFEST"
if (Test-Path $man) { Info ("             {0}" -f (Get-Content $man -TotalCount 2 | Select-Object -Last 1)) }
$bun = Join-Path $Here "build\bundles\eth_tx.json"
Info ("firmware     firmware\eth_tx.asm, bundle sha256 {0}" -f (Get-FileHash $bun -Algorithm SHA256).Hash.ToLower().Substring(0, 16))
$na = Get-NetAdapter -Name $Nic
Info ("receiver     this PC's port '{0}': {1}" -f $Nic, $na.InterfaceDescription)
Write-Host @"

    Pico W  --SPI (CS_n, SCK, MOSI, MISO)-->  Tang Primer 25K Dock: the chip, eth_tx at 40 MHz
    Pico GP0 (PWM, 160 MHz / 4) --> clk 40 MHz

    Dock J6 top col 6 (uio[0], TD+) --[47 ohm]--[100 nF]-- green-white --> RJ45 pin 3 (laptop RX+)
    Dock J6 top col 5 (uio[1], TD-) --[47 ohm]--[100 nF]-- green -------> RJ45 pin 6 (laptop RX-)
    (a twisted pair; no ground wire; the capacitors block DC, the laptop's transformer isolates)
"@

Step 2 "The two boards"
$picoPort = (& $mp devs | Where-Object { $_ -match "2e8a:0005" } | Select-Object -First 1)
if (-not $picoPort) { Bad "no Pico (USB 2E8A:0005)" }
Ok ("Pico W on {0}" -f ($picoPort -split "\s+")[0])
$dock = usbipd list | Select-String "0403:6010" | Select-Object -First 1
if (-not $dock) { Bad "no Dock debugger (USB 0403:6010)" }
$busid = ($dock.ToString().Trim() -split "\s+")[0]
if ($dock.ToString() -notmatch "Attached") {
    Start-Process wsl.exe -ArgumentList "-e", "sleep", "7200" -WindowStyle Hidden
    Start-Sleep -Seconds 4
    usbipd attach --wsl --busid $busid | Out-Null
    Start-Sleep -Seconds 3
}
if (-not (usbipd list | Select-String $busid | Select-String "Attached")) { Bad "the Dock (bus $busid) is not attached to WSL" }
Ok "Tang Primer 25K Dock debugger on bus $busid, attached to WSL"

if (-not $NoDeploy) {
    Step 3 "Files onto the Pico (each checked by sha256)"
    $r = & $Bench deploy 2>&1 | Select-Object -Last 1
    if ("$r" -notmatch "deployed and verified") { Bad "$r" }
    Ok "$r"
}

Step 4 "The chip's bitstream into the FPGA"
$r = & $Bench load chip 2>&1 | Out-String
if ($r -notmatch "DONE") { Bad "openFPGALoader: $r" }
Ok "loaded"

Step 5 "The chip answers the Pico (bring-up stages 0 to 3)"
$r = & $Bench bringup -Opts "stages=(0,1,2,3)" 2>&1
$r | Where-Object { "$_" -match "PASS|FAIL" } | ForEach-Object { Info "$_" }
if (-not ($r | Select-String "bring-up PASSED")) { Bad "bring-up failed" }

Step 6 "The laptop's port before"
$s0 = Get-NetAdapterStatistics -Name $Nic
Info ("link {0}; broadcast frames received so far {1}; receive errors {2}" -f (Get-NetAdapter -Name $Nic).Status, $s0.ReceivedBroadcastPackets, $s0.ReceivedPacketErrors)

$etl = $null
if ($admin) {
    $dir = Join-Path $env:TEMP "escdemo"; New-Item -ItemType Directory -Force $dir | Out-Null
    $etl = Join-Path $dir "capture.etl"
    Remove-Item $etl, ([IO.Path]::ChangeExtension($etl, ".pcapng")) -ErrorAction SilentlyContinue
    Pk "stop" | Out-Null                                           # "not running" is fine
    Pk "filter remove" | Out-Null
    if ((Pk "filter add EscapementEth -d 34997") -ne 0) { Bad "pktmon filter add: $script:pkOut" }   # EtherType 0x88B5
    if ((Pk "start --capture --comp nics --pkt-size 0 -f ""$etl""") -ne 0) { Bad "pktmon start: $script:pkOut" }
    Ok "pktmon capturing EtherType 0x88B5 on the network ports"
} else {
    Info "(not administrator: no packet capture; the port's counters are the evidence)"
}

Step 7 "The chip sends $Frames frames, one a second"
$job = Start-Job -ScriptBlock { param($b, $n) & $b eth -Opts "count=$n, settle_ms=8000" 2>&1 } -ArgumentList $Bench, $Frames
$sent = 0
while ($true) {
    $done = $job.State -ne "Running"
    foreach ($l in (Receive-Job $job)) {
        $l = "$l"
        if ($l -match "^frame (\d+):") { $sent = [int]$Matches[1] }
        if ($l.Trim()) { Info "chip:   $l" }
    }
    $a = Get-NetAdapter -Name $Nic; $s = Get-NetAdapterStatistics -Name $Nic
    Info ("laptop: link {0,-12} {1}   test frames received {2}" -f $a.Status, $a.LinkSpeed, ($s.ReceivedBroadcastPackets - $s0.ReceivedBroadcastPackets))
    if ($done) { break }
    Start-Sleep -Seconds 2
}
Remove-Job $job -Force
Start-Sleep -Seconds 2
$s1 = Get-NetAdapterStatistics -Name $Nic
$got = $s1.ReceivedBroadcastPackets - $s0.ReceivedBroadcastPackets
$err = $s1.ReceivedPacketErrors - $s0.ReceivedPacketErrors

$seenN = -1
if ($etl) {
    Step 8 "What the laptop captured (pktmon)"
    if ((Pk "stop") -ne 0) { Bad "pktmon stop: $script:pkOut" }
    $pcap = [IO.Path]::ChangeExtension($etl, ".pcapng")
    if ((Pk "etl2pcap ""$etl"" -o ""$pcap""") -ne 0) { Bad "pktmon etl2pcap: $script:pkOut" }
    Pk "filter remove" | Out-Null
    $seenN = Show-Frames (Read-EscFrames $pcap)
    Info "$seenN distinct escapement frames in the capture ($pcap)"
}

Step 9 "Result"
Info "chip: $sent frames sent, each reported sent with no error by its line unit"
Info "laptop: $got test frames received by its own Ethernet port, $err receive errors (the port checks every frame's CRC-32)"
$pass = ($sent -eq $Frames) -and ($got -eq $Frames) -and ($err -eq 0) -and ($seenN -lt 0 -or $seenN -eq $Frames)
if ($pass) { Write-Host "    PASS: $Frames of $Frames frames from the chip arrived intact" -ForegroundColor Green }
else { Write-Host "    FAIL: $got of $Frames frames arrived" -ForegroundColor Red; exit 1 }
