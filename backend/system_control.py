"""
system_control.py  –  Safe Windows command executor for Jarvis.

Executes PowerShell / CMD commands requested by the LLM.
Dangerous commands (shutdown, format, del, etc.) are blocked unless
the user explicitly confirms via the UI.

Also provides real-time system metrics (CPU, RAM, battery).
"""

import subprocess
import re
from typing import Tuple

# ── Dangerous patterns (require user confirmation) ─────────────────
DANGEROUS_PATTERNS: list[str] = [
    r"\bshutdown\b",
    r"\brestart\b",
    r"\bformat\b",
    r"\bdel\b",
    r"\brmdir\b",
    r"\bRemove-Item\b",
    r"\brm\s+-rf\b",
    r"\bStop-Computer\b",
    r"\bRestart-Computer\b",
    r"\breg\s+delete\b",
    r"\btaskkill\b",
    r"\bnet\s+stop\b",
    r"\bdiskpart\b",
]


def is_dangerous(command: str) -> bool:
    """Return True if the command matches any dangerous pattern."""
    for pat in DANGEROUS_PATTERNS:
        if re.search(pat, command, re.IGNORECASE):
            return True
    return False


def execute_command(command: str, timeout: int = 30) -> Tuple[bool, str]:
    """
    Execute a Windows shell command via PowerShell.

    Returns
    -------
    (success: bool, output: str)
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = result.stdout.strip() or result.stderr.strip()
        return (result.returncode == 0, output or "Command executed successfully.")
    except subprocess.TimeoutExpired:
        return (False, f"Command timed out after {timeout}s.")
    except Exception as exc:
        return (False, f"Error: {exc}")


def get_system_info() -> dict:
    """
    Gather real-time system metrics via PowerShell.

    Returns dict with keys: cpu_percent, ram_percent, ram_used_gb,
    ram_total_gb, battery_percent, battery_charging, disk_free_gb.
    All values are None if the query fails.
    """
    info = {
        "cpu_percent": None,
        "ram_percent": None,
        "ram_used_gb": None,
        "ram_total_gb": None,
        "battery_percent": None,
        "battery_charging": None,
        "disk_free_gb": None,
    }

    # CPU + RAM in a single PowerShell call for speed
    try:
        ps_script = (
            "$os = Get-CimInstance Win32_OperatingSystem; "
            "$cpu = (Get-CimInstance Win32_Processor).LoadPercentage; "
            "$totalMB = [math]::Round($os.TotalVisibleMemorySize / 1024); "
            "$freeMB = [math]::Round($os.FreePhysicalMemory / 1024); "
            "$usedMB = $totalMB - $freeMB; "
            "$ramPct = [math]::Round($usedMB / $totalMB * 100); "
            "Write-Host \"$cpu|$ramPct|$([math]::Round($usedMB/1024,1))|$([math]::Round($totalMB/1024,1))\""
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("|")
            if len(parts) >= 4:
                info["cpu_percent"] = _safe_float(parts[0])
                info["ram_percent"] = _safe_float(parts[1])
                info["ram_used_gb"] = _safe_float(parts[2])
                info["ram_total_gb"] = _safe_float(parts[3])
    except Exception:
        pass

    # Battery
    try:
        bat_script = (
            "$b = Get-WmiObject Win32_Battery; "
            "if ($b) { Write-Host \"$($b.EstimatedChargeRemaining)|$($b.BatteryStatus)\" } "
            "else { Write-Host 'none' }"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", bat_script],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip() != "none":
            parts = result.stdout.strip().split("|")
            if len(parts) >= 2:
                info["battery_percent"] = _safe_float(parts[0])
                # BatteryStatus: 2 = AC/Charging, 1 = Discharging
                info["battery_charging"] = parts[1].strip() == "2"
    except Exception:
        pass

    # Disk free space (C: drive)
    try:
        disk_script = (
            "$d = Get-PSDrive C; "
            "Write-Host ([math]::Round($d.Free / 1GB, 1))"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", disk_script],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0 and result.stdout.strip():
            info["disk_free_gb"] = _safe_float(result.stdout.strip())
    except Exception:
        pass

    return info


def _safe_float(val: str) -> float | None:
    """Convert string to float, returning None on failure."""
    try:
        return float(val.strip())
    except (ValueError, TypeError, AttributeError):
        return None
