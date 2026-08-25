# Claude Code Hub — kontrola na Windows.
#
# Odpovídá na to, co se z Linuxu ověřit nedá a co bylo do teď domněnkou:
# jestli půjde odkaz na složku (symlink chce práva správce, křižovatka ne),
# jestli naběhne ConPTY terminál, jak se doopravdy jmenuje složka, do které si
# Claude Code ukládá paměť, a jestli schránka veze češtinu.
#
#     powershell -ExecutionPolicy Bypass -File windows-check.ps1
#
# Nic nemění — jen zkouší a vypisuje. Výstup se dá poslat zpátky tak, jak je.

$ErrorActionPreference = 'Continue'
$results = [ordered]@{}

function Say($label, $value, $ok) {
    $color = if ($ok -eq $true) { 'Green' } elseif ($ok -eq $false) { 'Yellow' } else { 'Gray' }
    Write-Host ("  {0,-24} " -f $label) -NoNewline
    Write-Host $value -ForegroundColor $color
}

Write-Host ''
Write-Host '  Claude Code Hub — kontrola na Windows' -ForegroundColor DarkYellow
Write-Host ('  ' + ('-' * 44)) -ForegroundColor DarkGray

# ── 1. Co na stroji je ───────────────────────────────────────────────────────
$osName = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption
Say 'systém' $osName $null
Say 'PowerShell' $PSVersionTable.PSVersion.ToString() $null
$results.os = $osName

foreach ($tool in 'python', 'git', 'bash', 'claude', 'winget', 'gh') {
    $found = (Get-Command $tool -ErrorAction SilentlyContinue |
              Select-Object -First 1).Source
    Say $tool ($(if ($found) { $found } else { 'CHYBÍ' })) ([bool]$found)
    $results[$tool] = $found
}

# Git for Windows nese bash, na kterém stojí každý tab
$gitBash = @(
    "$env:ProgramFiles\Git\bin\bash.exe",
    "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
    "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
Say 'git bash' ($(if ($gitBash) { $gitBash } else { 'CHYBÍ — tab se neotevře' })) ([bool]$gitBash)
$results.gitBash = $gitBash

# ── 2. Odkaz na složku: symlink vs. křižovatka ───────────────────────────────
# Tohle je ta hlavní otázka. Symlink na Windows chce práva správce nebo zapnutý
# vývojářský režim; křižovatka nechce nic a pro složku dělá totéž.
$tmp = Join-Path $env:TEMP ("hubcheck-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force -Path "$tmp\cil" | Out-Null
$linkWay = 'NEJDE'
try {
    New-Item -ItemType SymbolicLink -Path "$tmp\odkaz" -Target "$tmp\cil" -ErrorAction Stop | Out-Null
    $linkWay = 'symlink'
} catch {
    $out = cmd /c mklink /J "$tmp\krizovatka" "$tmp\cil" 2>&1
    if (Test-Path "$tmp\krizovatka") { $linkWay = 'křižovatka (mklink /J)' }
    else { $linkWay = "NEJDE — $out" }
}
Say 'odkaz na složku' $linkWay ($linkWay -ne 'NEJDE')
$results.link = $linkWay

$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
Say 'běží jako správce' $(if ($admin) { 'ano' } else { 'ne' }) $null
$results.admin = $admin
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue

# ── 3. Jak si Claude Code jmenuje složku pro paměť ───────────────────────────
# Hub tam vede odkaz, ale jméno si Claude Code tvoří sám a my ho můžeme leda
# najít. Tady je vidět, jestli náš odhad sedí.
$projects = Join-Path $env:USERPROFILE '.claude\projects'
if (Test-Path $projects) {
    $dirs = @(Get-ChildItem $projects -Directory -ErrorAction SilentlyContinue |
              Select-Object -ExpandProperty Name)
    Say 'složek sezení' ("{0}" -f $dirs.Count) $null
    $homeSlug = $env:USERPROFILE -replace '\\', '-' -replace ':', ''
    $match = $dirs | Where-Object { ($_ -replace '-', '') -eq ($homeSlug -replace '-', '') }
    Say 'pro domovskou složku' ($(if ($match) { $match } else { 'zatím žádná' })) ([bool]$match)
    $results.homeSlug = $match
    $results.someSlugs = ($dirs | Select-Object -First 5) -join ', '
    if ($dirs.Count) { Say 'ukázka jmen' ((($dirs | Select-Object -First 3) -join ', ')) $null }
} else {
    Say 'složky sezení' 'Claude Code tu ještě neběžel' $false
}

# ── 4. Schránka ──────────────────────────────────────────────────────────────
try {
    $probe = "příliš žluťoučký kůň ĚŠČŘŽ"
    $probe | clip
    Start-Sleep -Milliseconds 300
    $back = Get-Clipboard -Raw
    $same = ($back -replace "`r`n$", '' -replace "`n$", '') -eq $probe
    Say 'schránka (čeština)' ($(if ($same) { 'projde tam i zpět' } else { "ROZBITÁ: '$back'" })) $same
    $results.clipboard = $same
} catch {
    Say 'schránka' "chyba: $($_.Exception.Message)" $false
}

# ── 5. Terminál pod ConPTY ───────────────────────────────────────────────────
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($py) {
    $hubPty = Join-Path $env:USERPROFILE '.claude\hub\pty_backend.py'
    if (Test-Path $hubPty) {
        $code = @"
import sys; sys.path.insert(0, r'$env:USERPROFILE\.claude')
from hub import pty_backend
ok, detail = pty_backend.selftest()
print('OK' if ok else 'SELHALO: ' + detail[:120])
"@
        $out = $code | & $py - 2>&1 | Select-Object -Last 1
        Say 'pty (ConPTY)' $out ($out -like 'OK*')
        $results.pty = $out
    } else {
        Say 'pty (ConPTY)' 'hub není nainstalovaný' $false
    }
    $wp = & $py -c "import winpty, sys; print(getattr(winpty,'__version__','?'))" 2>&1
    Say 'pywinpty' $wp ($LASTEXITCODE -eq 0)
}

# ── 6. Vlastní kontrola hubu ─────────────────────────────────────────────────
$hub = Join-Path $env:USERPROFILE '.claude\claude-hub.py'
if ((Test-Path $hub) -and $py) {
    Write-Host ''
    Write-Host '  --- claude-hub.py --doctor ---' -ForegroundColor DarkGray
    & $py $hub --doctor 2>&1 | ForEach-Object { Write-Host "  $_" }
} else {
    Write-Host ''
    Write-Host '  Hub ještě není nainstalovaný. Nainstaluj ho:' -ForegroundColor DarkGray
    Write-Host '    irm https://raw.githubusercontent.com/jurapascal/claude-code-hub/main/get.ps1 | iex' -ForegroundColor DarkGray
}

Write-Host ''
Write-Host '  Tenhle výpis pošli zpátky celý.' -ForegroundColor DarkYellow
Write-Host ''
