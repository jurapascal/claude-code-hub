# Claude Code Hub — instalačka pro Windows.
#
# Repo je jen instalačka: obsah (projekty, paměť) má každý svůj na disku.
# Skript zjistí, kde ho má, uloží to do %USERPROFILE%\.claude\hub-config.json
# a podle toho vyrenderuje aplikaci i slash příkazy. Nic nepřepíše bez zálohy
# a do settings.json nesahá.
#
# Použití (PowerShell, BEZ práv správce):
#     powershell -ExecutionPolicy Bypass -File install.ps1
#     powershell -ExecutionPolicy Bypass -File install.ps1 -Yes   (bez otázek)
#
# Linux a macOS mají install.sh.

[CmdletBinding()]
param([switch]$Yes)

$ErrorActionPreference = 'Stop'
$Src        = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClaudeDir  = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR }
              else { Join-Path $env:USERPROFILE '.claude' }
$Config     = Join-Path $ClaudeDir 'hub-config.json'
$Stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
$Interactive = -not $Yes -and [Environment]::UserInteractive

function Write-Ok   ($m) { Write-Host "  ✓ $m" -ForegroundColor Green }
function Write-Info ($m) { Write-Host "  ▸ $m" -ForegroundColor DarkYellow }
function Write-Warn ($m) { Write-Host "  ⚠ $m" -ForegroundColor Yellow }
function Write-Dim  ($m) { Write-Host "    $m" -ForegroundColor DarkGray }

# Calling a program that is not installed raises a terminating CommandNotFoundException
# that `2>$null` does not swallow — with ErrorActionPreference=Stop it kills the whole
# install. Every external program therefore gets checked before it is called.
function Test-Cmd($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Ask-YesNo($question) {
    if (-not $Interactive) { return $false }
    $answer = Read-Host "     $question [a/N]"
    return $answer -match '^[aAyY]'
}

# Bash and Claude Code both take Windows paths, but the slash commands are bash
# snippets — forward slashes are the form that works in both worlds.
function To-Slash($path) { return ($path -replace '\\', '/') }

# PowerShell 5.1 writes a BOM with -Encoding UTF8. A BOM in hub-config.json makes
# Python's json.load fail, so everything we generate goes through this instead.
# Join-Path throws on a null base, and with ErrorActionPreference=Stop that would
# abort the whole install just because one environment variable is unset.
function Join-Safe($base, $leaf) {
    if (-not $base) { return $null }
    return (Join-Path $base $leaf)
}

function Write-Utf8($path, $text) {
    [System.IO.File]::WriteAllText(
        $path, $text, (New-Object System.Text.UTF8Encoding $false))
}

# winget zapíše nové PATH do registru, ale běžící PowerShell má svou kopii z chvíle,
# kdy se spustil. Bez tohohle skript čerstvě nainstalovaný gh/node/claude "nevidí"
# a hlásí, že chybí — přitom je na disku.
function Update-Path {
    $parts = @()
    foreach ($scope in 'Machine', 'User') {
        $value = [Environment]::GetEnvironmentVariable('Path', $scope)
        if ($value) { $parts += $value }
    }
    if ($parts) { $env:Path = ($parts -join ';') }
}

function Install-WithWinget($id) {
    winget install --id $id --source winget `
        --accept-package-agreements --accept-source-agreements
    Update-Path
}

$HasWinget = Test-Cmd winget

Write-Host ''
Write-Host '  ✦ Claude Code Hub — instalace' -ForegroundColor DarkYellow
Write-Host '  ────────────────────────────────────' -ForegroundColor DarkGray

if (-not $HasWinget) {
    Write-Warn 'winget na tomhle systému není — co bude chybět, si musíš doinstalovat ručně:'
    Write-Dim 'Python: python.org/downloads · Git: git-scm.com/downloads/win'
    Write-Dim 'Claude Code: irm https://claude.ai/install.ps1 | iex'
}

# ── 1. Python ────────────────────────────────────────────────────────────────
$Python = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    # The Microsoft Store stub in WindowsApps is not a real interpreter.
    if ($cmd.Source -like '*WindowsApps*') { continue }
    try {
        $version = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    } catch { continue }
    if ($version -and [version]$version -ge [version]'3.9') {
        $Python = $cmd.Source
        break
    }
}
if (-not $Python) {
    Write-Warn 'Chybí Python 3.9+'
    if ($HasWinget -and (Ask-YesNo 'Nainstalovat Python přes winget?')) {
        Install-WithWinget 'Python.Python.3.13'
        Write-Info 'Zavři a znovu otevři PowerShell, pak spusť install.ps1 znovu.'
    } else {
        Write-Dim 'winget install Python.Python.3.13   (nebo python.org)'
    }
    exit 1
}
$PythonW = Join-Path (Split-Path -Parent $Python) 'pythonw.exe'
if (-not (Test-Path $PythonW)) {
    # the py.exe launcher has a windowed twin under a different name
    $pyw = Join-Path (Split-Path -Parent $Python) 'pyw.exe'
    $PythonW = if (Test-Path $pyw) { $pyw } else { $Python }
}
Write-Ok "Python 3 ($Python)"

# ── 2. pywinpty — jediná externí závislost, dělá ConPTY terminál ─────────────
$hasPty = $false
try {
    & $Python -c "import winpty" 2>$null
    $hasPty = ($LASTEXITCODE -eq 0)
} catch { $hasPty = $false }

if (-not $hasPty) {
    Write-Info 'Instaluju pywinpty (terminál ve Windows)…'
    & $Python -m pip install --user --disable-pip-version-check --quiet pywinpty
    try {
        & $Python -c "import winpty" 2>$null
        $hasPty = ($LASTEXITCODE -eq 0)
    } catch { $hasPty = $false }
}
if ($hasPty) {
    Write-Ok 'pywinpty'
} else {
    Write-Warn 'pywinpty se nenainstaloval — hub se spustí, ale taby se neotevřou'
    Write-Dim "$Python -m pip install --user pywinpty"
}

# ── 3. Git for Windows — dodává bash, na kterém stojí wrapper i slash příkazy ─
$GitBash = $null
$bashCandidates = @(
    $env:CLAUDE_CODE_GIT_BASH_PATH,
    (Join-Safe $env:ProgramFiles 'Git\bin\bash.exe'),
    (Join-Safe ${env:ProgramFiles(x86)} 'Git\bin\bash.exe'),
    (Join-Safe $env:LOCALAPPDATA 'Programs\Git\bin\bash.exe')
) | Where-Object { $_ }
foreach ($candidate in $bashCandidates) {
    if ($candidate -and (Test-Path $candidate)) { $GitBash = $candidate; break }
}
if (-not $GitBash) {
    Write-Warn 'Nenašel jsem Git for Windows (bash.exe)'
    Write-Dim 'Bez něj hub neumí spustit tab — ani Claude Code nemá Bash tool.'
    if ($HasWinget -and (Ask-YesNo 'Nainstalovat Git for Windows přes winget?')) {
        Install-WithWinget 'Git.Git'
        foreach ($candidate in $bashCandidates) {
            if ($candidate -and (Test-Path $candidate)) { $GitBash = $candidate; break }
        }
    }
}
if ($GitBash) {
    Write-Ok "Git for Windows ($GitBash)"
} else {
    Write-Warn 'pokračuju bez bashe — doinstaluj: winget install Git.Git'
}

# ── 4. Claude Code CLI ───────────────────────────────────────────────────────
$ClaudeCli = (Get-Command claude -ErrorAction SilentlyContinue).Source
if ($ClaudeCli) {
    Write-Ok "Claude Code CLI ($ClaudeCli)"
} else {
    Write-Warn "Claude Code CLI ('claude') není v PATH — Hub se spustí, ale taby zůstanou v shellu."
    if ($HasWinget -and (Ask-YesNo 'Nainstalovat Claude Code přes winget?')) {
        Install-WithWinget 'Anthropic.ClaudeCode'
        $ClaudeCli = (Get-Command claude -ErrorAction SilentlyContinue).Source
    } else {
        Write-Dim 'irm https://claude.ai/install.ps1 | iex'
    }
}

# ── 5. Obsidian a GitHub CLI ─────────────────────────────────────────────────
# Hub běží i bez obojího, ale bez Obsidianu nemá paměť kde bydlet (/save, /learn,
# /project) a bez `gh` se z čerstvého stroje nedá klonovat ani pushovat.
# Nikdy neinstalujeme potichu — bez -Yes se na všechno ptáme.
function Detect-Vault {
    $bases = @((Join-Safe $env:USERPROFILE 'Obsidian'),
               (Join-Safe $env:USERPROFILE 'Documents\Obsidian'),
               (Join-Safe $env:USERPROFILE 'OneDrive\Obsidian'),
               (Join-Safe $env:USERPROFILE 'OneDrive\Dokumenty\Obsidian'))
    foreach ($base in $bases) {
        if (-not $base -or -not (Test-Path $base)) { continue }
        foreach ($dir in Get-ChildItem -Path $base -Directory -ErrorAction SilentlyContinue) {
            if ((Test-Path (Join-Path $dir.FullName '.obsidian')) -or
                (Test-Path (Join-Path $dir.FullName 'memory'))) {
                return $dir.FullName
            }
        }
    }
    return ''
}

function Find-Obsidian {
    foreach ($p in @((Join-Safe $env:LOCALAPPDATA 'Obsidian\Obsidian.exe'),
                     (Join-Safe $env:ProgramFiles 'Obsidian\Obsidian.exe'))) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    # nainstalovaný Obsidian si registruje obsidian:// handler
    if (Test-Path 'Registry::HKEY_CLASSES_ROOT\obsidian' -ErrorAction SilentlyContinue) { return 'obsidian://' }
    return $null
}

function Find-Gh {
    $cmd = (Get-Command gh -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1).Source
    if ($cmd) { return $cmd }
    $candidates = @()
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        $candidates += (Join-Safe $base 'GitHub CLI\gh.exe')
    }
    # winget --scope user, scoop a choco si sahají jinam
    $candidates += (Join-Safe $env:LOCALAPPDATA 'Programs\GitHub CLI\gh.exe')
    $candidates += (Join-Safe $env:LOCALAPPDATA 'Microsoft\WinGet\Links\gh.exe')
    $candidates += (Join-Safe $env:USERPROFILE 'scoop\shims\gh.exe')
    $candidates += 'C:\ProgramData\chocolatey\bin\gh.exe'
    foreach ($guess in $candidates) {
        if ($guess -and (Test-Path $guess)) { return $guess }
    }
    return $null
}

function New-Vault($path) {
    foreach ($sub in @('memory', 'skills', '.obsidian')) {
        New-Item -ItemType Directory -Force -Path (Join-Path $path $sub) | Out-Null
    }
    $index = Join-Path $path 'memory\MEMORY.md'
    $template = Join-Path $Src 'assets\vault\MEMORY.md'
    if (-not (Test-Path $index) -and (Test-Path $template)) {
        Copy-Item $template $index
    }
}

Write-Host ''
if (Find-Obsidian) {
    Write-Ok 'Obsidian'
} else {
    Write-Warn 'Obsidian není nainstalovaný — v něm žije paměť (/save, /learn, /project)'
    if ($HasWinget -and (Ask-YesNo 'Nainstalovat Obsidian přes winget?')) {
        Install-WithWinget 'Obsidian.Obsidian'
        if (Find-Obsidian) { Write-Ok 'Obsidian nainstalován' }
        else { Write-Info 'hotovo, ale zatím ho nevidím — po restartu PowerShellu bude v pořádku' }
    } else {
        Write-Dim 'Ručně: https://obsidian.md/download'
    }
}

$vaultFound = Detect-Vault
if ($vaultFound) {
    Write-Ok "vault: $vaultFound"
} elseif (Ask-YesNo "Založit prázdný vault $(Join-Path $env:USERPROFILE 'Obsidian\Claude-Brain') pro paměť?") {
    New-Vault (Join-Path $env:USERPROFILE 'Obsidian\Claude-Brain')
    Write-Ok "vault založen: $(Join-Path $env:USERPROFILE 'Obsidian\Claude-Brain')"
    Write-Dim 'V Obsidianu pak: Open folder as vault'
} else {
    Write-Info 'bez vaultu poběží hub taky, jen bez paměti'
}

$Gh = Find-Gh
if (-not $Gh) {
    Write-Warn "GitHub CLI ('gh') není — bez něj se z tohohle stroje nepushuje na GitHub"
    if ($HasWinget -and (Ask-YesNo 'Nainstalovat GitHub CLI přes winget?')) {
        Install-WithWinget 'GitHub.cli'
        $Gh = Find-Gh
        if (-not $Gh) { Write-Info 'gh nainstalován, ale chce nový PowerShell — pak: gh auth login' }
    } else {
        Write-Dim 'Ručně: https://github.com/cli/cli#installation'
    }
}
if ($Gh) {
    Write-Ok "GitHub CLI ($Gh)"
    $ghAuthed = $false
    try {
        & $Gh auth status *>$null
        $ghAuthed = ($LASTEXITCODE -eq 0)
    } catch { $ghAuthed = $false }
    if ($ghAuthed) {
        Write-Ok 'gh je přihlášený'
    } elseif (Ask-YesNo 'Přihlásit gh k GitHubu teď? (otevře prohlížeč)') {
        try {
            & $Gh auth login
            & $Gh auth setup-git *>$null
            Write-Ok 'gh přihlášen a napojený na git'
        } catch {
            Write-Warn "přihlášení nedoběhlo — kdykoli později: gh auth login"
        }
    } else {
        Write-Dim 'Později: gh auth login'
    }
}

# ── 6. Kde má tenhle počítač co ──────────────────────────────────────────────
function Detect-ProjectDirs {
    $found = @()
    $candidates = @(
        (Join-Safe $env:USERPROFILE 'Desktop'),
        (Join-Safe $env:USERPROFILE 'Documents'),
        (Join-Safe $env:USERPROFILE 'source\repos'),
        (Join-Safe $env:USERPROFILE 'projects'),
        (Join-Safe $env:USERPROFILE 'dev'),
        (Join-Safe $env:USERPROFILE 'code'),
        'C:\xampp\htdocs', 'C:\wamp64\www', 'C:\laragon\www', 'C:\projects'
    )
    foreach ($c in $candidates) { if ($c -and (Test-Path $c)) { $found += $c } }
    return ($found -join ', ')
}

New-Item -ItemType Directory -Force -Path $ClaudeDir | Out-Null

if (Test-Path $Config) {
    Write-Ok "konfig už existuje — nechávám ho být ($Config)"
    $cfg = Get-Content $Config -Raw -Encoding UTF8 | ConvertFrom-Json
    # a hub upgraded from an older version may still be missing the bash path
    if ($GitBash -and -not $cfg.bash) {
        $cfg | Add-Member -NotePropertyName bash -NotePropertyValue (To-Slash $GitBash) -Force
        Write-Utf8 $Config ($cfg | ConvertTo-Json -Depth 5)
        Write-Info 'do konfigu doplněna cesta k bash.exe'
    }
} else {
    $projectDirs = Detect-ProjectDirs
    $vault = Detect-Vault
    if ($Interactive) {
        Write-Host ''
        Write-Info 'Kde máš projekty? (čárkou oddělený seznam)'
        $answer = Read-Host "     [$projectDirs]"
        if ($answer) { $projectDirs = $answer }
        Write-Info 'Obsidian vault s pamětí? (Enter = nechat prázdné, paměť se vypne)'
        $answer = Read-Host "     [$vault]"
        if ($answer) { $vault = $answer }
        Write-Host ''
    }
    $dirs = @($projectDirs -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if (-not $dirs) { $dirs = @((Join-Path $env:USERPROFILE 'Desktop')) }
    [ordered]@{
        project_dirs      = $dirs
        brain_dir         = $(if ($vault) { $vault } else { Join-Path $env:USERPROFILE 'Obsidian\Claude-Brain' })
        icon              = (Join-Path $ClaudeDir 'claude-code.ico')
        ftp_deploy_script = (Join-Path $ClaudeDir 'ftp-deploy.sh')
        bash              = $(if ($GitBash) { To-Slash $GitBash } else { '' })
    } | ConvertTo-Json -Depth 5 | ForEach-Object { Write-Utf8 $Config $_ }
    Write-Ok "konfig zapsán: $Config"
}

$cfg = Get-Content $Config -Raw -Encoding UTF8 | ConvertFrom-Json
$Vault = $cfg.brain_dir
$MemoryDir = Join-Path $Vault 'memory'
$BrainSkills = Join-Path $Vault 'skills'
$HasVault = (Test-Path $MemoryDir)
if ($HasVault) { Write-Ok "paměť: $MemoryDir" }
else { Write-Info 'bez Obsidian vaultu — paměťové příkazy a panel paměti se přeskočí' }

# Co skutečně stojí v konfigu (i když ho instalačka teď nepsala) — šablony
# skillů to potřebují, aby /newsletter a spol. hledaly projekty na správném místě.
$ProjectDirsList = (@($cfg.project_dirs) | ForEach-Object { To-Slash $_ }) -join ', '

# ── Skilly do vaultu ─────────────────────────────────────────────────────────
# Hotové postupy, ze kterých si Claude sám vybírá. Existující složku nikdy
# nepřepisujeme — kdo si tam něco napsal, o to nesmí přijít.
$SkillsRepo = if ($env:HUB_SKILLS_REPO) { $env:HUB_SKILLS_REPO }
              else { 'jurapascal/claude-brain-skills' }

function Count-Skills($path) {
    if (-not (Test-Path $path)) { return 0 }
    return @(Get-ChildItem $path -Recurse -Filter 'SKILL.md' -ErrorAction SilentlyContinue).Count
}

if (-not $HasVault) {
    # bez vaultu nemají kam
} elseif ((Test-Path $BrainSkills) -and
          @(Get-ChildItem $BrainSkills -Force -ErrorAction SilentlyContinue).Count -gt 0) {
    if (Test-Path (Join-Path $BrainSkills '.git')) {
        Write-Info 'aktualizuju skilly'
        if (Test-Cmd git) { git -C $BrainSkills pull --ff-only --quiet 2>$null | Out-Null }
        Write-Ok "skilly aktuální ($(Count-Skills $BrainSkills))"
    } else {
        Write-Ok "skilly už máš ($(Count-Skills $BrainSkills)) — nechávám je být"
    }
} elseif (-not (Test-Cmd git)) {
    Write-Info 'skilly přeskočeny — chybí git'
} else {
    Write-Info 'stahuju skilly (hotové postupy, ze kterých si Claude vybírá)'
    try {
        git clone --quiet --depth 1 "https://github.com/$SkillsRepo.git" $BrainSkills 2>$null | Out-Null
        if (Test-Path $BrainSkills) { Write-Ok "skilly: $(Count-Skills $BrainSkills) v $BrainSkills" }
        else { Write-Warn 'stažení nevyšlo' }
    } catch {
        Write-Warn "stažení nevyšlo — ručně: git clone https://github.com/$SkillsRepo.git `"$BrainSkills`""
    }
}

# ── 7. Aplikace do ~\.claude ─────────────────────────────────────────────────
New-Item -ItemType Directory -Force -Path (Join-Path $ClaudeDir 'hooks') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $ClaudeDir 'skills') | Out-Null

function Copy-Tracked($from, $to) {
    if ((Test-Path $to) -and
        ((Get-FileHash $from).Hash -ne (Get-FileHash $to).Hash)) {
        Copy-Item $to "$to.backup-$Stamp"
        Write-Info "záloha: $(Split-Path -Leaf $to).backup-$Stamp"
    }
    Copy-Item $from $to -Force
}

Copy-Tracked (Join-Path $Src 'claude-hub.py')         (Join-Path $ClaudeDir 'claude-hub.py')
Copy-Tracked (Join-Path $Src 'claude-wrapper.sh')     (Join-Path $ClaudeDir 'claude-wrapper.sh')
Copy-Tracked (Join-Path $Src 'hooks\save-session.py')  (Join-Path $ClaudeDir 'hooks\save-session.py')
Copy-Tracked (Join-Path $Src 'hooks\session-start.py') (Join-Path $ClaudeDir 'hooks\session-start.py')
# tools\ potřebuje sekce 10 (merge settings.json) i pozdější spuštění ručně
$toolsDest = Join-Path $ClaudeDir 'tools'
if (Test-Path $toolsDest) { Remove-Item $toolsDest -Recurse -Force }
Copy-Item (Join-Path $Src 'tools') $toolsDest -Recurse -Force
# hub\ je celý náš — nahrazuje se vcelku, aby po updatu nezůstaly staré soubory
$hubDest = Join-Path $ClaudeDir 'hub'
if (Test-Path $hubDest) { Remove-Item $hubDest -Recurse -Force }
Copy-Item (Join-Path $Src 'hub') $hubDest -Recurse -Force
Get-ChildItem $hubDest -Recurse -Directory -Filter '__pycache__' -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force
Copy-Item (Join-Path $Src 'assets\claude-code.ico') (Join-Path $ClaudeDir 'claude-code.ico') -Force
Write-Ok "aplikace v $ClaudeDir"

# ── 8. Slash příkazy ─────────────────────────────────────────────────────────
# Claude Code ≥ 2.1 čte vlastní příkazy z ~\.claude\skills\<jméno>\SKILL.md.
$stateFile = if ($HasVault) { Join-Path $MemoryDir 'session-state.md' }
             else { Join-Path $ClaudeDir 'session-state.md' }
$vaultOnly = @('save', 'learn', 'project', 'skill')   # bez vaultu nedávají smysl
$installed = @()

foreach ($dir in Get-ChildItem -Path (Join-Path $Src 'skills') -Directory) {
    $skillFile = Join-Path $dir.FullName 'SKILL.md'
    if (-not (Test-Path $skillFile)) { continue }
    if (-not $HasVault -and $vaultOnly -contains $dir.Name) { continue }
    $target = Join-Path $ClaudeDir "skills\$($dir.Name)"
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    (Get-Content $skillFile -Raw -Encoding UTF8).
        Replace('{{MEMORY_DIR}}',   (To-Slash $MemoryDir)).
        Replace('{{PROJECT_DIRS}}', $ProjectDirsList).
        Replace('{{SKILLS_DIR}}',  (To-Slash $BrainSkills)).
        Replace('{{CLAUDE_DIR}}',  (To-Slash $ClaudeDir)).
        Replace('{{FTP_DEPLOY}}',  (To-Slash (Join-Path $ClaudeDir 'ftp-deploy.sh'))).
        Replace('{{STATE_FILE}}',  (To-Slash $stateFile)).
        Replace('{{PYTHON}}',      (To-Slash $Python)) |
        ForEach-Object { Write-Utf8 (Join-Path $target 'SKILL.md') $_ }
    $installed += "/$($dir.Name)"
}
Write-Ok "slash příkazy: $($installed -join ' ')"

# ── 9. Zástupci (nabídka Start + plocha) ─────────────────────────────────────
# pythonw.exe = žádné černé okno konzole vedle hubu.
function New-Shortcut($path) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($path)
    $link.TargetPath = $PythonW
    $link.Arguments = '"' + (Join-Path $ClaudeDir 'claude-hub.py') + '"'
    $link.WorkingDirectory = $ClaudeDir
    $link.IconLocation = (Join-Path $ClaudeDir 'claude-code.ico')
    $link.Description = 'Claude Code Hub — projekty a paměť v jednom okně s taby'
    $link.Save()
}

# Do 1.3.2 se zástupce jmenoval „Claude Code" — tedy stejně jako CLI, se kterým
# se v nabídce Start pletl. Starý smažeme, ale jen když je doopravdy náš (míří na
# claude-hub.py), ať nesáhneme na zástupce, kterého si udělal někdo sám.
function Remove-OurOldShortcut($path) {
    if (-not (Test-Path $path)) { return $false }
    try {
        $shell = New-Object -ComObject WScript.Shell
        if ($shell.CreateShortcut($path).Arguments -notlike '*claude-hub.py*') { return $false }
        Remove-Item $path -Force
        return $true
    } catch { return $false }
}

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
if (Remove-OurOldShortcut (Join-Path $startMenu 'Claude Code.lnk')) {
    Write-Dim 'starý zástupce „Claude Code" v nabídce Start nahrazen'
}
New-Shortcut (Join-Path $startMenu 'Claude Code Hub.lnk')
Write-Ok 'zástupce v nabídce Start: Claude Code Hub'

# Na ploše se neptáme znovu toho, kdo zástupce už má — jen ho přejmenujeme.
$desktopOld = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Claude Code.lnk'
$desktopNew = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Claude Code Hub.lnk'
if ((Remove-OurOldShortcut $desktopOld) -or (Test-Path $desktopNew)) {
    New-Shortcut $desktopNew
    Write-Ok 'zástupce na ploše: Claude Code Hub'
} elseif (Ask-YesNo 'Přidat zástupce i na plochu?') {
    New-Shortcut $desktopNew
    Write-Ok 'zástupce na ploše'
}

# ── 10. Hooky a režim oprávnění v settings.json ──────────────────────────────
# settings.json je uživatelův (klíče, model, vlastní hooky), takže se do něj
# nesází šablona — tools\settings_merge.py přidá jen to, co chybí, a předtím
# udělá zálohu. Stejný skript používá i linuxová instalačka.
$mergeArgs = @((Join-Path $ClaudeDir 'tools\settings_merge.py'),
               '--claude-dir', $ClaudeDir, '--python', $Python, '--hooks')

if ($Interactive) {
    Write-Host ''
    Write-Info 'Zapnout bypass režim? Claude pak nebude ptát na potvrzení u každého'
    Write-Dim  'příkazu a úpravy souboru — rychlejší práce, ale běží bez brzdy.'
    Write-Dim  'Zapni jen na vlastním stroji, kde víš, co ti Claude spouští.'
    Write-Dim  'Kdykoli později: /permissions v Claude Code.'
    if (Ask-YesNo 'Zapnout bypass režim?') { $mergeArgs += '--bypass' }
}

try {
    & $Python @mergeArgs 2>&1 | ForEach-Object {
        if     ($_ -match '^chyba:')            { Write-Warn ($_ -replace '^chyba: ', '') }
        elseif ($_ -match 'vlastní hook')       { Write-Warn $_ }
        elseif ($_ -match 'nechávám být|beze změny|^už ') { Write-Ok $_ }
        else                                    { Write-Info $_ }
    }
} catch {
    Write-Warn "settings.json se nepodařilo upravit: $($_.Exception.Message)"
}

# ── 11. Playwright MCP ───────────────────────────────────────────────────────
# Prohlížeč pro Claude Code. Poprvé stahuje ~115 MB, ale patří k výbavě, takže
# se nasazuje sám — jen se to nahlásí.
# Everything here is optional, and nothing in it may colour the install red.
# `node` a `npx` hledáme přes Get-Command -CommandType Application (skutečné .exe,
# ne alias ani funkce z profilu) a voláme je plnou cestou; bez nich se celá sekce
# přeskočí — včetně dotazu na `claude`, protože npm shim `claude.ps1` si sám sahá
# po `node` a bez něj vypíše CommandNotFoundException do průběhu instalace.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'SilentlyContinue'
try {
function Find-Node {
    # winget nepromítne PATH do už běžícího PowerShellu, takže po instalaci
    # hledáme i na obvyklém místě, ať se nemusí zavírat okno.
    $cmd = (Get-Command node -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1).Source
    if ($cmd) { return $cmd }
    foreach ($guess in @((Join-Safe $env:ProgramFiles 'nodejs\node.exe'),
                         (Join-Safe ${env:ProgramFiles(x86)} 'nodejs\node.exe'),
                         (Join-Safe $env:LOCALAPPDATA 'Programs\nodejs\node.exe'),
                         (Join-Safe $env:APPDATA 'npm\node.exe'))) {
        if ($guess -and (Test-Path $guess)) { return $guess }
    }
    return $null
}

function Find-Npx {
    $cmd = (Get-Command npx -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1).Source
    if ($cmd) { return $cmd }
    foreach ($guess in @((Join-Safe $env:ProgramFiles 'nodejs\npx.cmd'),
                         (Join-Safe ${env:ProgramFiles(x86)} 'nodejs\npx.cmd'),
                         (Join-Safe $env:LOCALAPPDATA 'Programs\nodejs\npx.cmd'),
                         (Join-Safe $env:APPDATA 'npm\npx.cmd'))) {
        if ($guess -and (Test-Path $guess)) { return $guess }
    }
    return $null
}

$NodeExe = Find-Node
if (-not $NodeExe -and $ClaudeCli -and $HasWinget -and
    (Ask-YesNo 'Node.js není nainstalovaný. Nainstalovat LTS přes winget? (kvůli Playwright MCP)')) {
    Install-WithWinget 'OpenJS.NodeJS.LTS'
    $NodeExe = Find-Node
    if (-not $NodeExe) { Write-Info 'Node se nainstaloval, ale je potřeba nový PowerShell — pak spusť install.ps1 znovu.' }
}
$NpxExe = if ($NodeExe) { Find-Npx } else { $null }
$nodeMajor = 0
if ($NodeExe) {
    try {
        if ((& $NodeExe -v) -match '^v(\d+)') { $nodeMajor = [int]$Matches[1] }
    } catch { $nodeMajor = 0 }
}

$mcpRegistered = $false
if ($ClaudeCli -and $NodeExe) {
    try {
        & $ClaudeCli mcp get playwright *>$null
        $mcpRegistered = ($LASTEXITCODE -eq 0)
    } catch { $mcpRegistered = $false }
}

if (-not $ClaudeCli) {
    Write-Info 'Playwright MCP přeskočen — chybí Claude Code CLI'
} elseif (-not $NodeExe -or -not $NpxExe) {
    Write-Info 'Playwright MCP přeskočen — bez Node.js/npx ho není čím spustit'
} elseif ($mcpRegistered) {
    Write-Ok 'playwright MCP už je zaregistrovaný'
} elseif ($nodeMajor -lt 20) {
    Write-Warn "Playwright MCP přeskočen — chce Node.js 20+ (teď: $nodeMajor)"
} else {
    Write-Info 'přidávám Playwright MCP (prohlížeč pro Claude Code, stáhne ~115 MB)'
    & $ClaudeCli mcp add playwright -s user -- npx '@playwright/mcp@latest' --browser chromium
    Write-Info 'stahuju prohlížeč (~115 MB, stahuje se jen co chybí)…'
    & $NpxExe -y '@playwright/mcp@latest' install-browser chrome-for-testing
    Write-Ok 'playwright MCP připraven'
} else {
    Write-Info 'přeskočeno — kdykoli později: claude mcp add playwright -s user -- npx @playwright/mcp@latest --browser chromium'
}
} catch {
    # nothing here is required for the hub to work — never let it fail the install
    Write-Warn "Playwright MCP se nepovedlo přidat: $($_.Exception.Message)"
} finally {
    $ErrorActionPreference = $prevEap
}

# ── 12. Přihlášení do Claude Code ────────────────────────────────────────────
# Bez přihlášení uvítá každý tab login obrazovka. Spustit `claude` rovnou tady je
# nejrychlejší: uživatel projde /login a dá /exit.
if ($ClaudeCli) {
    $credFile = Join-Path $ClaudeDir '.credentials.json'
    if ((Test-Path $credFile) -or $env:ANTHROPIC_API_KEY) {
        Write-Ok 'Claude Code je přihlášený'
    } elseif (Ask-YesNo 'Přihlásit se teď do Claude Code? (projdi /login a dej /exit)') {
        try { & $ClaudeCli } catch { Write-Warn 'claude se nepodařilo spustit — zkus ho z tabu v hubu' }
    } else {
        Write-Dim 'Později: spusť claude a napiš /login'
    }
}

# Závěrečná kontrola: jeden výpis, ze kterého je vidět, co na stroji opravdu je.
try { & $Python (Join-Path $ClaudeDir 'claude-hub.py') --doctor } catch { }

Write-Host ''
Write-Host '  ✦ Hotovo. Spusť zástupce Claude Code Hub v nabídce Start.' -ForegroundColor DarkYellow
Write-Dim  "Kontrola prostředí:  `"$Python`" `"$(Join-Path $ClaudeDir 'claude-hub.py')`" --doctor"
Write-Dim  'První spuštění Claude Code: v tabu napiš /login a přihlas se svým účtem.'
Write-Dim  "Kdyby okno zůstalo prázdné, důvod je v $(Join-Path $ClaudeDir 'hub.log')"
Write-Host ''
