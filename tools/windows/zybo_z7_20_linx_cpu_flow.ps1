param(
  [switch]$BuildOnly,
  [switch]$Program,
  [switch]$NoRuns,
  [string]$VivadoBat = ""
)

$ErrorActionPreference = "Stop"

function Find-VivadoBat {
  $candidates = @()
  $candidates += Get-ChildItem -Path "C:\\AMDDesignTools\\*\\Vivado\\bin\\vivado.bat" -ErrorAction SilentlyContinue
  $candidates += Get-ChildItem -Path "C:\\Xilinx\\Vivado\\*\\bin\\vivado.bat" -ErrorAction SilentlyContinue
  if (-not $candidates -or $candidates.Count -eq 0) {
    throw "vivado.bat not found. Pass -VivadoBat C:\\path\\to\\vivado.bat"
  }
  return ($candidates | Sort-Object FullName -Descending | Select-Object -First 1).FullName
}

if ([string]::IsNullOrWhiteSpace($VivadoBat)) {
  $VivadoBat = Find-VivadoBat
}
if (-not (Test-Path $VivadoBat)) {
  throw "VivadoBat does not exist: $VivadoBat"
}

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\\..")).Path
$tcl  = Join-Path $repo "boards\\zybo_z7_20\\vivado\\build_zybo_linx_cpu.tcl"
if (-not (Test-Path $tcl)) {
  throw "Missing build script: $tcl"
}

Write-Host ("[zybo-linx] repo:   {0}" -f $repo)
Write-Host ("[zybo-linx] vivado: {0}" -f $VivadoBat)
Write-Host ("[zybo-linx] tcl:    {0}" -f $tcl)

Push-Location $repo
try {
  if ($NoRuns) {
    $env:PYC_NO_RUNS = "1"
  } else {
    Remove-Item Env:PYC_NO_RUNS -ErrorAction SilentlyContinue
  }

  if ($Program -and -not $BuildOnly) {
    $env:PYC_PROGRAM = "1"
    & $VivadoBat -mode batch -source $tcl
  } else {
    Remove-Item Env:PYC_PROGRAM -ErrorAction SilentlyContinue
    & $VivadoBat -mode batch -source $tcl
  }
} finally {
  Pop-Location
}

