# Windows setup: Zybo Z7-20 (UART + JTAG) + Vivado

This doc captures the minimal steps to make a Digilent Zybo Z7-20 usable on Windows:

1. Windows enumerates the FTDI interfaces (UART + JTAG).
2. Vivado Hardware Manager sees a Digilent cable and the XC7Z020 device.
3. Vivado can build and program a simple LED-blink design from this repo.

## Quick device checks (PowerShell)

Confirm the COM port exists:

```powershell
Get-PnpDevice -PresentOnly -Class Ports | Format-Table -AutoSize
```

Confirm the FTDI VID/PID (`VID_0403&PID_6010`) is present:

```powershell
Get-PnpDevice -PresentOnly | ? { $_.InstanceId -match 'VID_0403&PID_6010' } | fl
```

## Vivado + cable drivers

- Install Vivado (edition/version that supports Zynq-7000 XC7Z020).
- Ensure Hardware Manager / Hardware Server components are included.

After installing Vivado, install cable drivers (Admin required). The path varies by Vivado version, but typically:

```text
<Vivado>\data\xicom\cable_drivers\nt64\install_drivers_wrapper.bat
```

This script accepts a few options (optional), e.g.:

```text
-log_filename C:\path\to\install_drivers.log
```

If Vivado still cannot see the cable, install Digilent Adept/Runtime and reboot.

## Board files (optional)

If you install Digilent Vivado board files, Vivado will list **Zybo Z7-20** under "Boards".
If you skip board files, you can still target the part directly:

```text
xc7z020clg400-1
```

## Repo bring-up build (LED blink)

Build a minimal bitstream using checked-in generated RTL:

```powershell
vivado -mode batch -source boards/zybo_z7_20/vivado/build_zybo_counter.tcl
```

Program automatically (optional):

```powershell
$env:PYC_PROGRAM=1
vivado -mode batch -source boards/zybo_z7_20/vivado/build_zybo_counter.tcl
```

## References

```text
Digilent Vivado board files:
https://github.com/Digilent/vivado-boards

Zybo Z7 master constraints:
https://raw.githubusercontent.com/Digilent/digilent-xdc/master/Zybo-Z7-Master.xdc

Digilent Adept Utilities / Runtime download:
https://lp.digilent.com/complete-adept-utilities-download
```
