# GP_SyncSecondary.ps1 - copy the imported GP rows from primary.accdb into
# secondary.accdb, so secondary carries exactly what primary received.
#
# Reads primary (Mode=Read - primary is never modified) and writes only the GP
# rows into secondary. It copies whole rows column-for-column, so secondary ends
# up with the same values, not a re-derivation from the CSVs.
#
# Safe to re-run: rows already in secondary are skipped by key, so an interrupted
# run resumes and never duplicates. Backs up secondary before writing.
#
# It REFUSES to write if the two schemas differ, since a mismatched INSERT would
# put values in the wrong columns.
#
# Same folder rules as the import: <store>\primary.accdb, or primary.accdb here
# with  -Store 1ORM.   Add -WhatIf to see what it would copy and change nothing.

param([switch]$Auto, [switch]$WhatIf, [switch]$NoBackup, [string]$Store)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName System.Data
Add-Type -AssemblyName System.Core

$BASE = 900000
$ALL_STORES = @('1ORM','2SDY','3MRY')
$logFile = Join-Path $here 'GP_SyncSecondary_log.txt'
function Log($msg, $color='Gray') {
    $line = "$(Get-Date -Format 'MM/dd HH:mm:ss')  $msg"
    Write-Host $line -ForegroundColor $color
    Add-Content -Path $logFile -Value $line
}
function Die($m) { Log "FATAL: $m  Nothing was changed." 'Red'; if (-not $Auto) { Read-Host 'Enter to exit' }; exit 1 }

function Open-Db($p, $readOnly) {
    $mode = if ($readOnly) { 'Mode=Read;' } else { '' }
    foreach ($prov in 'Microsoft.ACE.OLEDB.16.0','Microsoft.ACE.OLEDB.12.0') {
        try {
            $c = New-Object System.Data.OleDb.OleDbConnection("Provider=$prov;Data Source=$p;$mode")
            $c.Open(); return $c
        } catch { }
    }
    return $null
}
function Fill($conn, $sql) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $sql
    $da = New-Object System.Data.OleDb.OleDbDataAdapter($cmd)
    $tb = New-Object System.Data.DataTable
    [void]$da.Fill($tb)
    return ,$tb
}
function Scalar($conn, $sql) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $sql
    $v = $cmd.ExecuteScalar()
    if ($v -is [System.DBNull]) { return $null }
    return $v
}
function Cols($conn, $tbl) {
    $out = @()
    try {
        $t = $conn.GetOleDbSchemaTable([System.Data.OleDb.OleDbSchemaGuid]::Columns, @($null,$null,$tbl,$null))
        foreach ($r in (@($t.Rows) | Sort-Object { [int]$_['ORDINAL_POSITION'] })) {
            $out += ("$($r['COLUMN_NAME'])" + '|' + "$($r['DATA_TYPE'])")
        }
    } catch { }
    return $out
}
function Insert-Row($conn, $sql, $row, $cols) {
    $cmd = $conn.CreateCommand(); $cmd.CommandText = $sql
    foreach ($c in $cols) {
        $v = $row[$c]
        if ($null -eq $v -or $v -is [System.DBNull]) {
            $p = $cmd.Parameters.Add('?', [System.Data.OleDb.OleDbType]::VarWChar)
            $p.Value = [System.DBNull]::Value
        } elseif ($v -is [datetime]) {
            $p = $cmd.Parameters.Add('?', [System.Data.OleDb.OleDbType]::Date)
            $p.Value = $v.AddMilliseconds(-$v.Millisecond)
        } else {
            [void]$cmd.Parameters.AddWithValue('?', $v)
        }
    }
    return $cmd.ExecuteNonQuery()
}

# copy rows of one table from src to dst, skipping keys already present
function Sync-Table($cSrc, $cDst, $tbl, $where, $keyCol) {
    $have = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($r in (Fill $cDst "SELECT [$keyCol] FROM [$tbl] WHERE $where").Rows) { [void]$have.Add("$($r[0])") }
    $src = Fill $cSrc "SELECT * FROM [$tbl] WHERE $where"
    $cols = @($src.Columns | ForEach-Object { $_.ColumnName })
    $sql  = "INSERT INTO [$tbl] (" + (($cols | ForEach-Object { "[$_]" }) -join ',') + ") VALUES (" +
            (($cols | ForEach-Object { '?' }) -join ',') + ")"
    $ins=0; $skip=0; $errs=0; $i=0
    foreach ($row in $src.Rows) {
        $i++
        if ($have.Contains("$($row[$keyCol])")) { $skip++; continue }
        if ($WhatIf) { $ins++; continue }
        try { [void](Insert-Row $cDst $sql $row $cols); $ins++ }
        catch {
            if ($_.Exception.Message -like '*duplicate*') { $skip++ }
            else { $errs++; if ($errs -le 5) { Log "    $tbl key $($row[$keyCol]): $($_.Exception.Message)" 'Yellow' } }
        }
        if ($i % 2000 -eq 0) { Log "    $tbl : $i / $($src.Rows.Count)..." }
    }
    Log ("  {0,-22} {1,8} copied  {2,8} already there  {3} errors" -f $tbl, $ins, $skip, $errs) $(if ($errs) {'Yellow'} else {'Green'})
    return $errs
}

Write-Host '=== sync GP rows from primary into secondary ===' -ForegroundColor Cyan
if ($WhatIf) { Write-Host 'DRY RUN (-WhatIf): nothing will be written.' -ForegroundColor Yellow }
Write-Host "Folder: $here"

$sets = @()
foreach ($s in $ALL_STORES) {
    if (Test-Path (Join-Path $here "$s\primary.accdb")) { $sets += @{ Store=$s; dir=(Join-Path $here $s) } }
}
if ($sets.Count -eq 0 -and (Test-Path (Join-Path $here 'primary.accdb'))) {
    if (-not $Store) { Die 'found primary.accdb here but not which store. Re-run with  -Store 1ORM' }
    $sets += @{ Store=$Store; dir=$here }
}
if ($sets.Count -eq 0) { Die 'no primary.accdb found in this folder.' }

foreach ($x in $sets) {
    if (-not (Test-Path (Join-Path $x.dir 'secondary.accdb'))) { Die "$($x.Store) has no secondary.accdb next to its primary.accdb" }
}
Write-Host ''
foreach ($x in $sets) { Write-Host "  $($x.Store)  ->  $(Join-Path $x.dir 'secondary.accdb')" }
if (-not $Auto -and -not $WhatIf) {
    $go = Read-Host 'Type GO to copy (anything else quits)'
    if ($go -ne 'GO') { exit 0 }
}
Log '=== SYNC START ===' 'Cyan'
$totalErrs = 0

foreach ($x in $sets) {
    $st = $x.Store
    Log "STORE $st" 'Cyan'
    $pri = Join-Path $x.dir 'primary.accdb'
    $sec = Join-Path $x.dir 'secondary.accdb'

    $cSrc = Open-Db $pri $true
    if (-not $cSrc) { Die "cannot open $pri - is the Access engine installed in this PowerShell's bitness?" }
    $cDst = Open-Db $sec $false
    if (-not $cDst) { $cSrc.Close(); Die "cannot open $sec for writing (in use, or read-only on disk)" }

    # ---- schemas must match or we stop ----
    $plan = @(
        @{ tbl='Customers';            where="[Source] = 'GP'";              key='ID' },
        @{ tbl='Customer_PastHistory'; where="PastHistory LIKE '%Great Plains history%'"; key='CustID' },
        @{ tbl='POS_SalesSlip';        where="Notes LIKE 'GPIMPORT:%'";      key='ID' },
        @{ tbl='POS_Transactions';     where="SalesSlipID IN (SELECT ID FROM POS_SalesSlip WHERE Notes LIKE 'GPIMPORT:%')"; key='TransactionID' }
    )
    # LIKE wildcard self-check. If this engine wanted * instead of %, every
    # marker query would match nothing and we would copy an empty set while
    # reporting success. Verify against a wildcard-free count first.
    $inRange = [int](Scalar $cSrc "SELECT COUNT(*) FROM POS_SalesSlip WHERE ID >= $BASE")
    $marked  = [int](Scalar $cSrc "SELECT COUNT(*) FROM POS_SalesSlip WHERE Notes LIKE 'GPIMPORT:%'")
    Log "  primary: $inRange slips at ID >= $BASE, $marked carry a GPIMPORT marker"
    if ($inRange -gt 0 -and $marked -eq 0) {
        $cSrc.Close(); $cDst.Close()
        Die "no slip matched the GPIMPORT marker although $inRange sit in the imported ID range. This engine may want * rather than % as the LIKE wildcard - copying now would silently do nothing."
    }
    if ($inRange -gt $marked) {
        Log "  note: $($inRange - $marked) slips at ID >= $BASE are NOT from the GP import (Clarity issues live slip IDs in this range) - they will not be copied." 'Yellow'
    }

    $bad = @()
    $todo = @()
    foreach ($p in $plan) {
        $cp = Cols $cSrc $p.tbl
        $cs = Cols $cDst $p.tbl
        if ($cp.Count -eq 0) { Log "  $($p.tbl): not in primary - skipping" 'Yellow'; continue }
        if ($cs.Count -eq 0) { Log "  $($p.tbl): not in secondary - skipping" 'Yellow'; continue }
        $diffA = @($cp | Where-Object { $cs -notcontains $_ })
        $diffB = @($cs | Where-Object { $cp -notcontains $_ })
        if ($diffA.Count -gt 0 -or $diffB.Count -gt 0) {
            $bad += $p.tbl
            Log "  $($p.tbl): SCHEMA MISMATCH" 'Red'
            if ($diffA.Count) { Log ("    only in primary  : " + ($diffA -join ', ')) 'Red' }
            if ($diffB.Count) { Log ("    only in secondary: " + ($diffB -join ', ')) 'Red' }
        } else {
            $todo += $p
        }
    }
    if ($bad.Count -gt 0) {
        $cSrc.Close(); $cDst.Close()
        Die "schemas differ for: $($bad -join ', '). Copying would put values in the wrong columns."
    }
    if ($todo.Count -eq 0) { Log '  nothing to sync here' 'Yellow'; $cSrc.Close(); $cDst.Close(); continue }

    # ---- backup secondary ----
    if (-not $WhatIf -and -not $NoBackup) {
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $bkp = [System.IO.Path]::ChangeExtension($sec, $null) + "backup_$stamp.accdb"
        $cDst.Close()
        Copy-Item $sec $bkp
        Log "  backup -> $bkp" 'Green'
        $cDst = Open-Db $sec $false
        if (-not $cDst) { $cSrc.Close(); Die "could not reopen $sec after backup" }
    }

    # ---- copy, parents before children ----
    foreach ($p in $todo) { $totalErrs += (Sync-Table $cSrc $cDst $p.tbl $p.where $p.key) }

    $cSrc.Close(); $cDst.Close()
}

Log '=== SYNC COMPLETE ===' 'Cyan'
if ($WhatIf) { Log 'That was a dry run - nothing was written. Re-run without -WhatIf to do it.' 'Yellow' }
elseif ($totalErrs -gt 0) { Log "$totalErrs rows failed - review the lines above, then re-run (it resumes)." 'Yellow' }
else { Log 'secondary now carries the same GP rows as primary.' 'Green' }
if (-not $Auto) { Read-Host 'Press Enter to close' }
