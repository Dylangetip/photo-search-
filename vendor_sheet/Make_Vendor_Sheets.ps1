# Make_Vendor_Sheets.ps1 - condensed photo sheets of one vendor's stock on hand.
#
# Reads an items csv (next to this script), finds each item's photo in the
# Clarity picture folder, and writes one compact HTML page per store with the
# photos embedded, so the file can be emailed or opened anywhere and still
# shows the pictures. Print to PDF if you want paper.
#
# It finds the picture folder itself. If it cannot, point it at the folder:
#     Make_Vendor_Sheets.ps1 -ImageFolder "N:\images"
#
# -Csv <file>     the item list to sheet, defaults to the only csv in the folder
# -Title <text>   heading on each sheet, e.g. "Simon G."
# -Tag <text>     file name prefix, defaults to Vendor
# -ThumbWidth     photo size in pixels, default 150. Bigger means a bigger file.
#
# READ ONLY. It never writes to a database and never changes a photo.

param([string]$ImageFolder, [string]$Csv, [string]$Title, [string]$Tag,
      [int]$ThumbWidth = 150, [switch]$Auto)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName System.Drawing
function Say($m, $c='Gray') { Write-Host $m -ForegroundColor $c }

if (-not $Csv) {
    $found = @(Get-ChildItem -Path $here -Filter '*_items.csv' -File -ErrorAction SilentlyContinue)
    if ($found.Count -eq 1) { $Csv = $found[0].FullName }
    elseif ($found.Count -gt 1) {
        Say "More than one item list here. Name the one you want:" 'Red'
        foreach ($f in $found) { Say "   -Csv $($f.Name)" }
        if (-not $Auto) { Read-Host 'Enter' }; exit 1
    }
}
if (-not $Csv -or -not (Test-Path $Csv)) { Say "No item list (*_items.csv) found here." 'Red'; if(-not $Auto){Read-Host 'Enter'}; exit 1 }
if (-not $Title) { $Title = [IO.Path]::GetFileNameWithoutExtension($Csv) -replace '_items$','' }
if (-not $Tag)   { $Tag   = ($Title -replace '[^A-Za-z0-9]','') }
if (-not $Tag)   { $Tag   = 'Vendor' }
$items = @(Import-Csv $Csv)
Say "$($items.Count) items to sheet." 'Cyan'

function Find-ImageFolder($items) {
    $probe = @($items | Where-Object { $_.image_file } | Select-Object -First 12 | ForEach-Object { $_.image_file })
    if ($probe.Count -eq 0) { return $null }
    $roots = @()
    foreach ($d in (Get-PSDrive -PSProvider FileSystem)) {
        $r = $d.Root
        $roots += @($r, (Join-Path $r 'images'), (Join-Path $r 'Images'), (Join-Path $r 'IMAGES'),
                    (Join-Path $r 'pictures'), (Join-Path $r 'Pictures'),
                    (Join-Path $r 'clarity\images'), (Join-Path $r 'Clarity\Images'),
                    (Join-Path $r 'clarity\pictures'))
        foreach ($s in '1ORM','2SDY','3MRY') {
            $roots += @((Join-Path $r "$s\images"), (Join-Path $r "clarity\$s\images"))
        }
    }
    $roots += @((Join-Path $here 'images'), $here)
    foreach ($c in ($roots | Select-Object -Unique)) {
        try {
            if (-not (Test-Path $c)) { continue }
            foreach ($f in $probe) { if (Test-Path (Join-Path $c $f)) { return $c } }
        } catch { }
    }
    return $null
}
if (-not $ImageFolder) { Say 'Looking for the Clarity picture folder...'; $ImageFolder = Find-ImageFolder $items }
if (-not $ImageFolder -or -not (Test-Path $ImageFolder)) {
    Say 'Could not find the folder holding the item photos.' 'Yellow'
    Say "Look for a folder full of files like $($items[0].image_file), then run:" 'Yellow'
    Say '    Make_Kashi_Sheets.ps1 -ImageFolder "N:\path\to\images"' 'Yellow'
    if (-not $Auto) { Read-Host 'Enter to exit' }; exit 1
}
Say "Picture folder: $ImageFolder" 'Green'
$byName = @{}
foreach ($f in (Get-ChildItem -Path $ImageFolder -File -ErrorAction SilentlyContinue)) {
    $k = $f.Name.ToLower(); if (-not $byName.ContainsKey($k)) { $byName[$k] = $f.FullName }
}
Say "  $($byName.Count) picture files there."

# Resizing needs System.Drawing. A function whose body mentions those types
# throws the moment it is entered where GDI+ will not load, before any guard
# inside it could run, so the fallback lives in its own function that names
# none of them.
$script:CanResize = $false
try { $p = New-Object System.Drawing.Bitmap(2,2); $p.Dispose(); $script:CanResize = $true } catch { }
if (-not $script:CanResize) { Say 'Resizing unavailable here; embedding small photos as they are.' 'Yellow' }

function Get-ThumbResized($path, $w) {
    try {
        $img = [System.Drawing.Image]::FromFile($path)
        $ratio = $w / [double]$img.Width
        $nh = [int]([Math]::Max(1, $img.Height * $ratio))
        $bmp = New-Object System.Drawing.Bitmap($w, $nh)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.DrawImage($img, 0, 0, $w, $nh); $g.Dispose(); $img.Dispose()
        $ms = New-Object System.IO.MemoryStream
        $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Jpeg); $bmp.Dispose()
        $b64 = [Convert]::ToBase64String($ms.ToArray()); $ms.Dispose(); return $b64
    } catch { return $null }
}
function Get-RawEmbed($path) {
    try {
        $fi = Get-Item $path
        if ($fi.Length -gt 400KB) { return $null }
        return [Convert]::ToBase64String([IO.File]::ReadAllBytes($path))
    } catch { return $null }
}
function Get-Photo($path, $w) {
    if ($script:CanResize) { return (Get-ThumbResized $path $w) }
    return (Get-RawEmbed $path)
}
function E($s) { if ($null -eq $s) { return '' } ("$s").Replace('&','&amp;').Replace('<','&lt;').Replace('>','&gt;') }

$CSS = @'
<style>
 body{font-family:Segoe UI,Arial,sans-serif;margin:16px;color:#111;background:#fff}
 h1{font-size:17px;color:#1F3864;margin:0 0 2px}
 .sub{font-size:10.5px;color:#555;margin-bottom:10px}
 .grid{display:flex;flex-wrap:wrap;gap:6px}
 .c{width:158px;border:1px solid #d0d0d0;border-radius:4px;padding:5px;page-break-inside:avoid}
 .c img{width:100%;border-radius:2px;display:block;background:#f4f4f4}
 .ni{width:100%;height:96px;background:#f4f4f4;border-radius:2px;display:flex;align-items:center;
     justify-content:center;color:#aaa;font-size:9.5px}
 .sku{font-weight:700;font-size:10.5px;color:#1F3864;margin-top:4px}
 .l{font-size:9.5px;color:#444;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 .old{color:#B03030;font-weight:700}
 .memo{background:#FFF3CD;color:#7A5B00;font-weight:700;padding:0 3px;border-radius:2px;font-size:9px}
 @media print{ body{margin:6mm} .c{width:23%} h1{font-size:14px} }
</style>
'@

$stores = $items | ForEach-Object { $_.store } | Select-Object -Unique | Sort-Object
$made=@()
foreach ($st in $stores) {
    $rows = @($items | Where-Object { $_.store -eq $st })
    $cost = ($rows | Measure-Object -Property cost -Sum).Sum
    $ret  = ($rows | Measure-Object -Property retail -Sum).Sum
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("<html><head><meta charset='utf-8'><title>$st $Title</title>$CSS</head><body>")
    [void]$sb.Append("<h1>$st - $(E $Title) on hand</h1>")
    [void]$sb.Append("<div class='sub'>$($rows.Count) pieces &nbsp;&bull;&nbsp; `$$('{0:N0}' -f $cost) at cost &nbsp;&bull;&nbsp; `$$('{0:N0}' -f $ret) at retail &nbsp;&bull;&nbsp; oldest first. Red age means 3 years or more. MEMO means the vendor still owns it; SAMPLE is a vendor display piece.</div><div class='grid'>")
    $n=0; $miss=0
    foreach ($r in $rows) {
        $n++
        $b64=$null
        if ($r.image_file) {
            $k=$r.image_file.ToLower()
            if ($byName.ContainsKey($k)) { try { $b64 = Get-Photo $byName[$k] $ThumbWidth } catch { $b64=$null } }
        }
        [void]$sb.Append("<div class='c'>")
        if ($b64) { [void]$sb.Append("<img src='data:image/jpeg;base64,$b64'/>") }
        else { $miss++; [void]$sb.Append("<div class='ni'>no photo</div>") }
        # show whatever the list flags this piece as: MEMO, SAMPLE, anything else
        $memo = if ($r.owned) { " <span class='memo'>$(E $r.owned)</span>" } else { '' }
        [void]$sb.Append("<div class='sku'>$(E $r.inventory_number)$memo</div>")
        $ageCls = if ([double]$r.years_old -ge 3) { 'l old' } else { 'l' }
        [void]$sb.Append("<div class='$ageCls'>$($r.years_old) yrs &middot; $(E $r.received)</div>")
        [void]$sb.Append("<div class='l'>cost `$$('{0:N0}' -f [double]$r.cost) &middot; ret `$$('{0:N0}' -f [double]$r.retail)</div>")
        [void]$sb.Append("</div>")
    }
    [void]$sb.Append('</div></body></html>')
    $out = Join-Path $here "$($Tag)_$st.html"
    [IO.File]::WriteAllText($out,$sb.ToString(),[Text.Encoding]::UTF8)
    Say "$st : $($rows.Count) pieces, $miss without a photo -> $out  ($([Math]::Round((Get-Item $out).Length/1MB,1)) MB)" 'Green'
    $made+=$out
}
Say ''
Say "Done. $Title, $($items.Count) pieces. Open a sheet and use Print to PDF if you want paper." 'Cyan'
foreach ($m in $made) { Say "   $m" }
if (-not $Auto) { Read-Host 'Press Enter to close' }
