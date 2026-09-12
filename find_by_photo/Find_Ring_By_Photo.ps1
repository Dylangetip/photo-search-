# Find_Ring_By_Photo.ps1 - find an item in inventory from a photograph.
#
#     Find_Ring_By_Photo.ps1 -Photo "C:\Users\bwilde\Desktop\ring.jpg" -OnHandOnly -Rings
#
# -Photo <file>        the picture of the piece you are holding (required)
# -ImageFolder <path>  Clarity picture folder, found automatically if omitted
# -OnHandOnly          only items currently in a store          <- use this
# -Rings               only engagement rings and bands          <- and this
# -Top <n>             how many to show, default 120
# -Rebuild             throw away the hash cache and redo it
#
# HOW IT WORKS, AND WHAT TO EXPECT
# Each picture is cropped to the piece itself and reduced to a 256 bit
# signature. Cropping matters: jewellery is photographed small against a big
# plain background, and without cropping almost every picture in the catalogue
# reduces to the same pattern, which is why an earlier version returned 40
# unrelated rings all scoring alike.
#
# Treat the ordering as a shortlist, not an answer. Use -OnHandOnly -Rings to
# cut the field to roughly a thousand, then scan the sheet with your eyes. The
# ranking puts likely pieces near the front; you make the call.
#
# The first run reads every picture and takes a while. It caches, so later runs
# are quick. READ ONLY: it never writes to a database or changes a picture.

param(
  [Parameter(Mandatory=$true)][string]$Photo,
  [string]$ImageFolder,
  [int]$Top = 120,
  [switch]$Rebuild,
  [switch]$OnHandOnly,
  [switch]$Rings,
  [switch]$Auto
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Add-Type -AssemblyName System.Drawing
function Say($m,$c='Gray'){ Write-Host $m -ForegroundColor $c }
function Die($m){ Say $m 'Red'; if(-not $Auto){Read-Host 'Enter to exit'}; exit 1 }
if (-not (Test-Path $Photo)) { Die "Cannot find your photo: $Photo" }
$lookupPath = Join-Path $here 'item_lookup.csv'
if (-not (Test-Path $lookupPath)) { Die "item_lookup.csv is not in this folder." }

# ---------- reading pixels ---------------------------------------------------
# LockBits copies the whole bitmap out in one go. GetPixel would need ~28,000
# calls per picture for the crop probe, which is far too slow over 49,000 files.
function Get-Grey($bmp,$w,$h) {
    $small = New-Object System.Drawing.Bitmap($w,$h,[System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
    $g = [System.Drawing.Graphics]::FromImage($small)
    $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.DrawImage($bmp,0,0,$w,$h); $g.Dispose()
    $out = New-Object int[] ($w*$h)
    try {
        $rect = New-Object System.Drawing.Rectangle(0,0,$w,$h)
        $bd = $small.LockBits($rect,[System.Drawing.Imaging.ImageLockMode]::ReadOnly,
                              [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
        $stride = $bd.Stride
        $buf = New-Object byte[] ($stride*$h)
        [System.Runtime.InteropServices.Marshal]::Copy($bd.Scan0,$buf,0,$buf.Length)
        $small.UnlockBits($bd)
        for ($y=0; $y -lt $h; $y++) {
            $ro = $y*$stride; $oo = $y*$w
            for ($x=0; $x -lt $w; $x++) {
                $i = $ro + $x*3
                $out[$oo+$x] = [int](0.114*$buf[$i] + 0.587*$buf[$i+1] + 0.299*$buf[$i+2])
            }
        }
    } catch {
        for ($y=0; $y -lt $h; $y++) { for ($x=0; $x -lt $w; $x++) {
            $p=$small.GetPixel($x,$y)
            $out[$y*$w+$x]=[int](0.299*$p.R+0.587*$p.G+0.114*$p.B) } }
    } finally { $small.Dispose() }
    return $out
}
$PW=192; $PH=144; $THR=16; $PAD=0.06; $HS=16     # probe size, subject threshold, pad, hash grid
function Get-Box($bmp) {
    $a = Get-Grey $bmp $PW $PH
    $b = New-Object System.Collections.ArrayList
    for ($x=0;$x -lt $PW;$x++){ [void]$b.Add($a[$x]); [void]$b.Add($a[($PH-1)*$PW+$x]) }
    for ($y=0;$y -lt $PH;$y++){ [void]$b.Add($a[$y*$PW]); [void]$b.Add($a[$y*$PW+$PW-1]) }
    $sorted=@($b | Sort-Object); $bg=$sorted[[int]($sorted.Count/2)]
    $x0=$PW; $y0=$PH; $x1=-1; $y1=-1; $n=0
    for ($y=0;$y -lt $PH;$y++){ $ro=$y*$PW
        for ($x=0;$x -lt $PW;$x++){
            if ([Math]::Abs($a[$ro+$x]-$bg) -gt $THR) {
                $n++
                if($x -lt $x0){$x0=$x}; if($x -gt $x1){$x1=$x}
                if($y -lt $y0){$y0=$y}; if($y -gt $y1){$y1=$y}
            } } }
    if ($n -lt 8 -or $x1 -lt $x0 -or $y1 -lt $y0) { return $null }
    $fx0=$x0/$PW; $fy0=$y0/$PH; $fx1=($x1+1)/$PW; $fy1=($y1+1)/$PH
    $w=$fx1-$fx0; $h=$fy1-$fy0
    return @{ x0=[Math]::Max(0.0,$fx0-$w*$PAD); y0=[Math]::Max(0.0,$fy0-$h*$PAD)
              x1=[Math]::Min(1.0,$fx1+$w*$PAD); y1=[Math]::Min(1.0,$fy1+$h*$PAD) }
}
$HEX='0123456789abcdef'
function Hash-Image($path) {
    $img=$null
    try {
        $img=[System.Drawing.Image]::FromFile($path)
        if ($img.Width -lt 4 -or $img.Height -lt 4) { return $null }
        $box = Get-Box $img
        $src = $img
        $cropped = $null
        if ($box) {
            $cx=[int]($box.x0*$img.Width); $cy=[int]($box.y0*$img.Height)
            $cw=[Math]::Max(2,[int](($box.x1-$box.x0)*$img.Width))
            $ch=[Math]::Max(2,[int](($box.y1-$box.y0)*$img.Height))
            if ($cx+$cw -gt $img.Width) { $cw=$img.Width-$cx }
            if ($cy+$ch -gt $img.Height){ $ch=$img.Height-$cy }
            if ($cw -ge 2 -and $ch -ge 2) {
                $cropped = New-Object System.Drawing.Bitmap($cw,$ch)
                $g=[System.Drawing.Graphics]::FromImage($cropped)
                $dst=New-Object System.Drawing.Rectangle(0,0,$cw,$ch)
                $sr =New-Object System.Drawing.Rectangle($cx,$cy,$cw,$ch)
                $g.DrawImage($img,$dst,$sr,[System.Drawing.GraphicsUnit]::Pixel); $g.Dispose()
                $src=$cropped
            }
        }
        $a = Get-Grey $src ($HS+1) $HS
        if ($cropped) { $cropped.Dispose() }
        $sb=New-Object System.Text.StringBuilder
        $v=0; $c=0
        for ($y=0;$y -lt $HS;$y++){ $ro=$y*($HS+1)
            for ($x=0;$x -lt $HS;$x++){
                $bit = ($a[$ro+$x+1] -gt $a[$ro+$x])
                $v = ($v -shl 1) -bor ([int]$bit); $c++
                if ($c -eq 4) { [void]$sb.Append($HEX[$v]); $v=0; $c=0 }
            } }
        return $sb.ToString()
    } catch { return $null } finally { if($img){$img.Dispose()} }
}
$POP=@(0,1,1,2,1,2,2,3,1,2,2,3,2,3,3,4)
function HexDist($a,$b){
    if(-not $a -or -not $b -or $a.Length -ne $b.Length){ return 9999 }
    $d=0
    for($i=0;$i -lt $a.Length;$i++){
        $d += $POP[ ([Convert]::ToInt32($a[$i],16)) -bxor ([Convert]::ToInt32($b[$i],16)) ]
    }
    return $d
}

# ---------- picture folder ---------------------------------------------------
$lookup=@(Import-Csv $lookupPath)
Say "$($lookup.Count) catalogue pictures known." 'Cyan'
function Find-ImageFolder($rows){
    $probe=@($rows | Where-Object{$_.image_file} | Select-Object -First 12 | ForEach-Object{$_.image_file})
    $roots=@()
    foreach($d in (Get-PSDrive -PSProvider FileSystem)){
        $r=$d.Root
        $roots+=@($r,(Join-Path $r 'images'),(Join-Path $r 'Images'),(Join-Path $r 'IMAGES'),
                  (Join-Path $r 'pictures'),(Join-Path $r 'Pictures'),(Join-Path $r 'clarity\images'))
        foreach($s in '1ORM','2SDY','3MRY'){ $roots+=(Join-Path $r "$s\images") }
    }
    $roots+=@((Join-Path $here 'images'),$here)
    foreach($c in ($roots|Select-Object -Unique)){
        try{ if(Test-Path $c){ foreach($f in $probe){ if(Test-Path (Join-Path $c $f)){ return $c } } } }catch{}
    }
    return $null
}
if(-not $ImageFolder){ Say 'Looking for the Clarity picture folder...'; $ImageFolder=Find-ImageFolder $lookup }
if(-not $ImageFolder -or -not (Test-Path $ImageFolder)){ Die "Could not find the picture folder. Re-run with -ImageFolder ""N:\path\to\images""" }
Say "Picture folder: $ImageFolder" 'Green'

# ---------- which pictures are worth hashing --------------------------------
$byFile=@{}
foreach($r in $lookup){
    $k=$r.image_file.ToLower()
    if(-not $byFile.ContainsKey($k)){ $byFile[$k]=$r }
}
$files=@(Get-ChildItem -Path $ImageFolder -File -ErrorAction SilentlyContinue |
         Where-Object{ $_.Extension -match '(?i)\.(jpg|jpeg|png|bmp|gif)$' })
$want=@()
foreach($f in $files){
    $m=$byFile[$f.Name.ToLower()]
    if($OnHandOnly){ if(-not $m){continue}; if(-not $m.onhand){continue} }
    if($Rings){
        if(-not $m){continue}
        if($m.inventory_number -notmatch '^(10|11|12|20|22)-'){ continue }
    }
    $want+=$f
}
Say "$($files.Count) pictures in the folder; $($want.Count) to compare after filters." 'Cyan'
if($want.Count -eq 0){ Die "Nothing left after the filters. Try without -OnHandOnly or -Rings." }

$cachePath=Join-Path $here 'image_hashes.csv'
$cache=@{}
if((Test-Path $cachePath) -and -not $Rebuild){
    foreach($r in (Import-Csv $cachePath)){ $cache[$r.file]=$r.hash }
    Say "Loaded $($cache.Count) hashes from image_hashes.csv." 'Green'
}
$todo=@($want | Where-Object{ -not $cache.ContainsKey($_.Name) })
if($todo.Count -gt 0){
    Say "Hashing $($todo.Count) pictures..." 'Yellow'
    $n=0;$bad=0;$t0=Get-Date
    foreach($f in $todo){
        $n++
        $h=Hash-Image $f.FullName
        if($h){ $cache[$f.Name]=$h } else { $bad++ }
        if($n % 500 -eq 0){
            $el=((Get-Date)-$t0).TotalSeconds; $rate=[Math]::Max($n/$el,0.001)
            Say ("  {0} / {1}   {2:N1}/sec, about {3:N0} min left" -f $n,$todo.Count,$rate,(($todo.Count-$n)/$rate/60))
        }
    }
    Say "  hashed $($n-$bad), skipped $bad." 'Green'
    $rows=foreach($k in $cache.Keys){ [PSCustomObject]@{ file=$k; hash=$cache[$k] } }
    $rows | Export-Csv $cachePath -NoTypeInformation
    Say "  cache saved." 'Green'
}

Say "Hashing your photo..." 'Cyan'
$q=Hash-Image $Photo
if(-not $q){ Die "Could not read $Photo as an image." }

# ---------- rank -------------------------------------------------------------
# values are computed before the object is built: putting an if() straight into
# a [PSCustomObject]@{} literal misbehaves on Windows PowerShell 5.1 and blanked
# every item number in the previous version.
$res=New-Object System.Collections.ArrayList
foreach($f in $want){
    $h=$cache[$f.Name]
    if(-not $h){ continue }
    $d=HexDist $q $h
    $m=$byFile[$f.Name.ToLower()]
    $item='(not in the catalogue list)'; $desc=''; $vend=''; $oh=''; $cst=''; $ret=''
    if($null -ne $m){
        $item=[string]$m.inventory_number; $desc=[string]$m.desc; $vend=[string]$m.vendor
        $oh=[string]$m.onhand; $cst=[string]$m.cost; $ret=[string]$m.retail
    }
    $o=New-Object PSObject
    $o | Add-Member NoteProperty file   $f.Name
    $o | Add-Member NoteProperty dist   $d
    $o | Add-Member NoteProperty item   $item
    $o | Add-Member NoteProperty desc   $desc
    $o | Add-Member NoteProperty vendor $vend
    $o | Add-Member NoteProperty onhand $oh
    $o | Add-Member NoteProperty cost   $cst
    $o | Add-Member NoteProperty retail $ret
    [void]$res.Add($o)
}
$best=@($res | Sort-Object dist | Select-Object -First $Top)
Say ''
Say "Top of the shortlist (out of $($res.Count) compared):" 'Cyan'
$i=0
foreach($b in ($best|Select-Object -First 12)){
    $i++
    Say ("  {0,2}. {1,3}/256  {2,-14} {3,-20} {4}" -f $i,$b.dist,$b.item,$b.onhand,$b.desc)
}

# ---------- results page -----------------------------------------------------
function Emb($path,$w){
    try{
        $img=[System.Drawing.Image]::FromFile($path)
        $r=$w/[double]$img.Width; $nh=[int][Math]::Max(1,$img.Height*$r)
        $bmp=New-Object System.Drawing.Bitmap($w,$nh)
        $g=[System.Drawing.Graphics]::FromImage($bmp)
        $g.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.DrawImage($img,0,0,$w,$nh);$g.Dispose();$img.Dispose()
        $ms=New-Object System.IO.MemoryStream
        $bmp.Save($ms,[System.Drawing.Imaging.ImageFormat]::Jpeg);$bmp.Dispose()
        $b=[Convert]::ToBase64String($ms.ToArray());$ms.Dispose();return $b
    }catch{ return $null }
}
function E($s){ if($null -eq $s){return ''} ("$s").Replace('&','&amp;').Replace('<','&lt;').Replace('>','&gt;') }
$css=@'
<style>
body{font-family:Segoe UI,Arial,sans-serif;margin:16px;color:#111}
h1{font-size:18px;color:#1F3864;margin:0 0 2px}.sub{font-size:11px;color:#555;margin-bottom:12px;max-width:900px}
.q{position:sticky;top:0;background:#fff;padding-bottom:8px;border-bottom:2px solid #1F3864;margin-bottom:12px}
.q img{height:190px;border-radius:4px;border:2px solid #1F3864}
.grid{display:flex;flex-wrap:wrap;gap:6px}
.c{width:132px;border:1px solid #ccc;border-radius:4px;padding:4px;page-break-inside:avoid}
.c img{width:100%;border-radius:2px;background:#f4f4f4}
.sku{font-weight:700;font-size:10px;color:#1F3864}
.l{font-size:9px;color:#444;line-height:1.25;max-height:34px;overflow:hidden}
.oh{background:#EAF1E4;font-weight:700;padding:0 3px;border-radius:2px}
.d{font-size:9px;color:#888}
</style>
'@
$sb=New-Object System.Text.StringBuilder
[void]$sb.Append("<html><head><meta charset='utf-8'><title>Photo match</title>$css</head><body>")
$qb=Emb $Photo 260
[void]$sb.Append("<div class='q'><h1>Find this piece</h1>")
if($qb){[void]$sb.Append("<img src='data:image/jpeg;base64,$qb'/>")}
[void]$sb.Append("</div>")
[void]$sb.Append("<div class='sub'>Showing the $($best.Count) closest of $($res.Count) pictures compared. The order is a shortlist, not a verdict: jewellery photographs are mostly plain background, so ranking narrows the field but your eye makes the call. Green means on hand.</div><div class='grid'>")
foreach($b in $best){
    $p=Join-Path $ImageFolder $b.file
    $e=Emb $p 128
    [void]$sb.Append("<div class='c'>")
    if($e){[void]$sb.Append("<img src='data:image/jpeg;base64,$e'/>")}
    [void]$sb.Append("<div class='sku'>$(E $b.item)</div>")
    if($b.onhand){[void]$sb.Append("<div class='l'><span class='oh'>$(E $b.onhand)</span></div>")}
    [void]$sb.Append("<div class='l'>$(E $b.desc)</div>")
    [void]$sb.Append("<div class='d'>$($b.dist)/256</div></div>")
}
[void]$sb.Append('</div></body></html>')
$out=Join-Path $here 'Photo_Match_Results.html'
[IO.File]::WriteAllText($out,$sb.ToString(),[Text.Encoding]::UTF8)
Say ''
Say "Results written to $out" 'Green'
if(-not $Auto){ Read-Host 'Press Enter to close' }
