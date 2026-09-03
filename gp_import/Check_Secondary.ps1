# Check_Secondary.ps1 - READ ONLY. Answers one question: is secondary.accdb a
# mirror of primary.accdb, and how far apart are they now?
#
# It opens every database in READ MODE and only counts rows. It never writes,
# never backs up, never changes a file. Safe to run while the import is going.
#
# For each store it reports primary, secondary, and the newest pre-import
# backup the import made (primary.backup_*.accdb), so you can see whether
# secondary matches what primary looked like BEFORE the import.
#
# Same folder rules as the import: it looks only in its own folder, either at
# <store>\primary.accdb or at primary.accdb here with  -Store 1ORM

param([string]$Store)

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName System.Data

$TABLES = @('Customers','Customer_PastHistory','POS_SalesSlip','POS_Transactions')
$ALL_STORES = @('1ORM','2SDY','3MRY')

function Open-Read($dbPath) {
    foreach ($prov in 'Microsoft.ACE.OLEDB.16.0','Microsoft.ACE.OLEDB.12.0') {
        try {
            $c = New-Object System.Data.OleDb.OleDbConnection("Provider=$prov;Data Source=$dbPath;Mode=Read;")
            $c.Open(); return $c
        } catch { }
    }
    return $null
}
function Count-Rows($conn, $tbl) {
    try {
        $cmd = $conn.CreateCommand(); $cmd.CommandText = "SELECT COUNT(*) FROM [$tbl]"
        return [int]$cmd.ExecuteScalar()
    } catch { return $null }
}
function Show($label, $path) {
    if (-not $path -or -not (Test-Path $path)) {
        Write-Host ("  {0,-22} (not present)" -f $label) -ForegroundColor DarkGray
        return
    }
    $fi = Get-Item $path
    $mb = [math]::Round($fi.Length / 1MB, 1)
    $c = Open-Read $path
    if (-not $c) {
        Write-Host ("  {0,-22} {1,8} MB   {2}   COULD NOT OPEN" -f $label, $mb, $fi.LastWriteTime) -ForegroundColor Red
        return
    }
    $counts = @()
    foreach ($t in $TABLES) {
        $n = Count-Rows $c $t
        if ($null -eq $n) { $counts += "$t=-" } else { $counts += ("{0}={1:N0}" -f $t, $n) }
    }
    $c.Close()
    Write-Host ("  {0,-22} {1,8} MB   {2}" -f $label, $mb, $fi.LastWriteTime) -ForegroundColor White
    Write-Host ("  {0,-22} {1}" -f '', ($counts -join '   ')) -ForegroundColor Gray
}

Write-Host '=== primary vs secondary (read only - nothing is changed) ===' -ForegroundColor Cyan
Write-Host "Folder: $here"
Write-Host ''

$sets = @()
foreach ($s in $ALL_STORES) {
    $p = Join-Path $here "$s\primary.accdb"
    if (Test-Path $p) { $sets += @{ Store = $s; dir = (Join-Path $here $s) } }
}
if ($sets.Count -eq 0) {
    $p = Join-Path $here 'primary.accdb'
    if (Test-Path $p) {
        if (-not $Store) { $Store = '(this folder)' }
        $sets += @{ Store = $Store; dir = $here }
    }
}
if ($sets.Count -eq 0) {
    Write-Host 'No primary.accdb found in this folder.' -ForegroundColor Red
    Read-Host 'Enter to exit'; exit 1
}

foreach ($x in $sets) {
    Write-Host "--- $($x.Store) ---" -ForegroundColor Yellow
    Show 'primary'   (Join-Path $x.dir 'primary.accdb')
    Show 'secondary' (Join-Path $x.dir 'secondary.accdb')
    $bk = @(Get-ChildItem -Path $x.dir -Filter 'primary.backup_*.accdb' -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending)
    if ($bk.Count -gt 0) { Show 'pre-import backup' $bk[0].FullName }
    else { Show 'pre-import backup' $null }
    Write-Host ''
}

Write-Host 'HOW TO READ THIS:' -ForegroundColor Cyan
Write-Host '  secondary matches the PRE-IMPORT BACKUP  -> secondary is a mirror of primary'
Write-Host '                                              as it was before the import.'
Write-Host '  secondary matches PRIMARY                -> it is being kept in step already'
Write-Host '                                              (linked tables, or Clarity syncs it).'
Write-Host '  secondary matches neither                -> it is its own thing; do not overwrite'
Write-Host '                                              it until you know what writes it.'
Write-Host ''
Write-Host 'Also worth checking: if secondary LastWriteTime is recent and changes on its'
Write-Host 'own, Clarity is regenerating it and you should leave it alone.'
Read-Host 'Press Enter to close'
