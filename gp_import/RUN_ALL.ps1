# RUN_ALL.ps1 - the whole job, one confirmation.
#
#   Stage 1  import the Great Plains history into primary.accdb
#   Stage 2  copy those same rows into secondary.accdb
#   Stage 3  audit both and write GP_Verify_Report.txt
#
# Every stage is safe to repeat: work already done is skipped, so if an earlier
# run got part way this finishes the rest instead of duplicating it. Stopping
# after any stage leaves the databases consistent.
#
# It looks ONLY in its own folder. Put the databases here as either
#   <this folder>\1ORM\primary.accdb + secondary.accdb   (and 2SDY, 3MRY)
# or, for one store at a time,
#   <this folder>\primary.accdb + secondary.accdb        and pass  -Store 1ORM
#
# -SkipSecondary   do stages 1 and 3 only
# -WhatIfSecondary stage 2 reports what it would copy and writes nothing
# -Auto            no prompts at all (for a scheduled run)

param([string]$Store, [switch]$Auto, [switch]$SkipSecondary, [switch]$WhatIfSecondary)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log  = Join-Path $here 'GP_RunAll_log.txt'
function L($m, $c='Gray') {
    $line = "$(Get-Date -Format 'MM/dd HH:mm:ss')  $m"
    Write-Host $line -ForegroundColor $c
    Add-Content -Path $log -Value $line
}
function Stop-Now($m) {
    L "STOPPED: $m" 'Red'
    if (-not $Auto) { Read-Host 'Press Enter to exit' }
    exit 1
}

# run each stage in a fresh process of THIS SAME powershell, so the Access
# engine bitness always matches and one stage cannot kill the whole run
$psExe = $null
try { $psExe = [System.Diagnostics.Process]::GetCurrentProcess().Path } catch { }
if (-not $psExe -or -not (Test-Path $psExe)) { $psExe = Join-Path $PSHOME 'powershell.exe' }
if (-not (Test-Path $psExe)) { $psExe = Join-Path $PSHOME 'pwsh.exe' }

function Run-Stage($num, $name, $script, $extraArgs) {
    $path = Join-Path $here $script
    if (-not (Test-Path $path)) { Stop-Now "$script is missing from this folder." }
    L ''
    L ("################  STAGE $num : $name") 'Cyan'
    $argList = @('-NoProfile','-ExecutionPolicy','Bypass','-File', $path, '-Auto')
    if ($Store) { $argList += @('-Store', $Store) }
    foreach ($a in $extraArgs) { $argList += $a }
    $p = Start-Process -FilePath $psExe -ArgumentList $argList -NoNewWindow -Wait -PassThru
    if ($p.ExitCode -ne 0) {
        L "stage $num ($name) ended with exit code $($p.ExitCode)" 'Red'
        return $false
    }
    L "stage $num ($name) finished" 'Green'
    return $true
}

Write-Host ''
Write-Host '===========================================================' -ForegroundColor Cyan
Write-Host '  Great Plains -> Clarity : import, mirror, verify' -ForegroundColor Cyan
Write-Host '===========================================================' -ForegroundColor Cyan
Write-Host "Folder: $here"
Write-Host ''

# ---------------- preflight ----------------
$ALL = @('1ORM','2SDY','3MRY')
$found = @()
foreach ($s in $ALL) { if (Test-Path (Join-Path $here "$s\primary.accdb")) { $found += $s } }
$flat = $false
if ($found.Count -eq 0 -and (Test-Path (Join-Path $here 'primary.accdb'))) {
    $flat = $true
    if (-not $Store) { Stop-Now 'primary.accdb is sitting in this folder but the store was not named. Re-run as:  RUN_ALL_32bit.bat -Store 1ORM' }
    if ($ALL -notcontains $Store) { Stop-Now "-Store must be one of: $($ALL -join ', ')" }
    $found = @($Store)
}
if ($found.Count -eq 0) { Stop-Now 'no primary.accdb found in this folder. Copy the store databases in first.' }

$need = @('GP_ImportAll.ps1','Verify_Import.ps1','import_append_history.csv','import_new_customers.csv')
if (-not $SkipSecondary) { $need += 'GP_SyncSecondary.ps1' }
foreach ($s in $found) { $need += "slips_$s.csv" }
$missing = @($need | Where-Object { -not (Test-Path (Join-Path $here $_)) })
if ($missing.Count -gt 0) { Stop-Now "these files are missing from this folder: $($missing -join ', ')" }

Write-Host 'Databases found:' -ForegroundColor Yellow
$noSecondary = @()
foreach ($s in $found) {
    $dir = if ($flat) { $here } else { Join-Path $here $s }
    $sec = Join-Path $dir 'secondary.accdb'
    Write-Host ("  {0}  primary   {1:N2} GB" -f $s, ((Get-Item (Join-Path $dir 'primary.accdb')).Length/1GB))
    if (Test-Path $sec) { Write-Host ("  {0}  secondary {1:N2} GB" -f $s, ((Get-Item $sec).Length/1GB)) }
    else { Write-Host ("  {0}  secondary MISSING" -f $s) -ForegroundColor Yellow; $noSecondary += $s }
}
if ($noSecondary.Count -gt 0 -and -not $SkipSecondary) {
    Write-Host ''
    Write-Host "No secondary.accdb for: $($noSecondary -join ', '). Stage 2 needs it." -ForegroundColor Yellow
    Write-Host 'Either copy those files in, or re-run with -SkipSecondary.' -ForegroundColor Yellow
    Stop-Now 'secondary.accdb missing'
}
if ($found.Count -lt 3) {
    Write-Host ''
    Write-Host "NOTE: only $($found -join ', ') present. Run the other stores later - gp_assigned_ids.csv keeps their customer IDs matching." -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'This will:' -ForegroundColor Yellow
Write-Host '  1. import the GP history into primary   (backs it up first)'
if ($SkipSecondary)        { Write-Host '  2. SKIPPED - secondary will not be touched' }
elseif ($WhatIfSecondary)  { Write-Host '  2. DRY RUN - report what secondary needs, write nothing' }
else                       { Write-Host '  2. copy those rows into secondary       (backs it up first)' }
Write-Host '  3. audit everything and write GP_Verify_Report.txt (read only)'
Write-Host ''
Write-Host 'Close Clarity and Access on this PC before continuing.' -ForegroundColor Yellow
Write-Host ''
if (-not $Auto) {
    $go = Read-Host 'Type GO to start (anything else quits)'
    if ($go -ne 'GO') { exit 0 }
}

L '=== RUN ALL START ===' 'Cyan'
L "folder: $here   stores: $($found -join ', ')"

$okImport = Run-Stage 1 'import into primary' 'GP_ImportAll.ps1' @()
if (-not $okImport) {
    L ''
    L 'The import did not finish cleanly. Secondary was NOT touched, so the two are' 'Red'
    L 'still in a known state. Read GP_Import_log.txt, fix the cause, and run this' 'Red'
    L 'again - it resumes and never duplicates.' 'Red'
    if (-not $Auto) { Read-Host 'Press Enter to exit' }
    exit 1
}

$okSync = $true
if ($SkipSecondary) { L ''; L 'STAGE 2 skipped (-SkipSecondary)' 'Yellow' }
else {
    $extra = @()
    if ($WhatIfSecondary) { $extra += '-WhatIf' }
    $okSync = Run-Stage 2 'copy into secondary' 'GP_SyncSecondary.ps1' $extra
    if (-not $okSync) {
        L 'Secondary did not finish. Primary is complete and correct; secondary is' 'Yellow'
        L 'behind. Read GP_SyncSecondary_log.txt - re-running resumes it.' 'Yellow'
    }
}

$okVerify = Run-Stage 3 'audit' 'Verify_Import.ps1' @()

L ''
L '=== RUN ALL COMPLETE ===' 'Cyan'
L "  stage 1 import    : $(if ($okImport) {'ok'} else {'FAILED'})"
L "  stage 2 secondary : $(if ($SkipSecondary) {'skipped'} elseif ($WhatIfSecondary) {'dry run'} elseif ($okSync) {'ok'} else {'FAILED'})"
L "  stage 3 audit     : $(if ($okVerify) {'ok'} else {'FAILED'})"
L ''
L 'READ GP_Verify_Report.txt BEFORE putting these files back on the server.' 'Cyan'
L 'If its SUMMARY says "No problems found", you are good to copy them up.' 'Cyan'
L 'Do NOT copy the *backup_*.accdb files back.' 'Cyan'
if (-not $Auto) { Read-Host 'Press Enter to close' }
if ($okImport -and $okSync -and $okVerify) { exit 0 } else { exit 1 }
