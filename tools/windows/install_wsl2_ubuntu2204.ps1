param(
  [string]$Distro = "Ubuntu-22.04"
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
  Write-Host "[wsl] Elevating to Administrator to install WSL2 + $Distro..."
  $args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$PSCommandPath`"",
    "-Distro", "`"$Distro`""
  )
  Start-Process -Verb RunAs -FilePath "powershell.exe" -ArgumentList $args
  exit 0
}

Write-Host "[wsl] Installing WSL2 + $Distro (this may require a reboot)..."
& wsl.exe --install -d $Distro

Write-Host "[wsl] If prompted, reboot Windows. After reboot, launch '$Distro' once to create a Linux user."

