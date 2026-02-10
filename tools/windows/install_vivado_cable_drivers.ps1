param(
  [string]$VivadoRoot = "C:\\AMDDesignTools\\2025.2\\Vivado"
)

$ErrorActionPreference = "Stop"

function Test-IsAdmin {
  try {
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
  } catch {
    return $false
  }
}

if (-not (Test-IsAdmin)) {
  Write-Host "[zybo] Elevating to Administrator for driver install..."
  $args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$PSCommandPath`"",
    "-VivadoRoot", "`"$VivadoRoot`""
  )
  Start-Process -Verb RunAs -FilePath "powershell.exe" -ArgumentList $args
  exit 0
}

$installer = Join-Path $VivadoRoot "data\\xicom\\cable_drivers\\nt64\\install_drivers_wrapper.bat"
if (-not (Test-Path $installer)) {
  throw "Installer not found: $installer"
}

$log = Join-Path $env:TEMP "vivado_cable_drivers_install.log"
Write-Host "[zybo] Running: $installer"
Write-Host "[zybo] Log:     $log"

& $installer -log_filename $log

Write-Host "[zybo] Done. Unplug/replug the board, then open Vivado Hardware Manager."

