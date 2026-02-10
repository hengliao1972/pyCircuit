param(
  [string]$LinxRoot = ""
)

$ErrorActionPreference = "Stop"

function Test-Cmd([string]$name) {
  return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Print-Check([string]$label, [bool]$ok, [string]$detail = "") {
  $status = if ($ok) { "OK" } else { "MISSING" }
  if ([string]::IsNullOrWhiteSpace($detail)) {
    Write-Host ("[{0}] {1}" -f $status, $label)
  } else {
    Write-Host ("[{0}] {1}: {2}" -f $status, $label, $detail)
  }
}

if ([string]::IsNullOrWhiteSpace($LinxRoot)) {
  $LinxRoot = Join-Path $env:USERPROFILE "linx"
}

Write-Host ("[linx] USERPROFILE: {0}" -f $env:USERPROFILE)
Write-Host ("[linx] LinxRoot:    {0}" -f $LinxRoot)

Print-Check "linx root exists" (Test-Path $LinxRoot) $LinxRoot

$repos = @("pyCircuit", "linx-isa", "llvm-project", "qemu", "linux")
foreach ($r in $repos) {
  $p = Join-Path $LinxRoot $r
  Print-Check "repo $r" (Test-Path $p) $p
}

# Vivado
$viv = @()
$viv += Get-ChildItem -Path "C:\\AMDDesignTools\\*\\Vivado\\bin\\vivado.bat" -ErrorAction SilentlyContinue
$viv += Get-ChildItem -Path "C:\\Xilinx\\Vivado\\*\\bin\\vivado.bat" -ErrorAction SilentlyContinue
Print-Check "Vivado" ($viv.Count -gt 0) ($(if ($viv.Count -gt 0) { ($viv | Sort-Object FullName -Descending | Select-Object -First 1).FullName } else { "" }))

# WSL
if (Test-Cmd "wsl.exe") {
  $null = & wsl.exe --status 2>$null
  $ok = ($LASTEXITCODE -eq 0)
  Print-Check "WSL installed" $ok ""
} else {
  Print-Check "WSL installed" $false ""
}

# Build tools
Print-Check "git" (Test-Cmd "git") ""
Print-Check "python" (Test-Cmd "python") ""
Print-Check "cmake" (Test-Cmd "cmake") ""
Print-Check "ninja" (Test-Cmd "ninja") ""

Write-Host ""
Write-Host "Next steps (suggested):"
Write-Host ("- FPGA LED blink:     powershell -ExecutionPolicy Bypass -File `"{0}`" -Program" -f (Join-Path $PSScriptRoot "zybo_z7_20_flow.ps1"))
Write-Host ("- FPGA Linx CPU demo: powershell -ExecutionPolicy Bypass -File `"{0}`" -Program" -f (Join-Path $PSScriptRoot "zybo_z7_20_linx_cpu_flow.ps1"))
Write-Host ("- FPGA Linx platform (PS/PL): powershell -ExecutionPolicy Bypass -File `"{0}`" -Core InOrder -Program" -f (Join-Path $PSScriptRoot "zybo_z7_20_linx_platform_flow.ps1"))
Write-Host ("- Install WSL2:       powershell -ExecutionPolicy Bypass -File `"{0}`"" -f (Join-Path $PSScriptRoot "install_wsl2_ubuntu2204.ps1"))
Write-Host ("- Install Ubuntu (TUNA rootfs): powershell -ExecutionPolicy Bypass -File `"{0}`"" -f (Join-Path $PSScriptRoot "install_ubuntu2204_tuna_wsl.ps1"))
Write-Host ("- In WSL:             bash tools/wsl/bootstrap_pyc_ubuntu2204.sh")
Write-Host ("- In WSL:             bash tools/wsl/bootstrap_linx_ubuntu2204.sh")
Write-Host ("- In WSL:             bash tools/wsl/build_linx_toolchains_ubuntu2204.sh")
