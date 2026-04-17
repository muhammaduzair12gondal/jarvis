"""
offline_router.py  –  Instant command router for common operations.

Bypasses the LLM entirely for well-known user intents, giving ~50ms response
times instead of 2-3 second LLM round-trips.  Falls through to the LLM for
anything not matched.
"""

import re
from dataclasses import dataclass
from typing import Callable

@dataclass
class OfflineRoute:
    patterns: list[str]
    response: str
    command: str | None
    thought: str = "Handled by offline router — instant response."

# ── Route definitions ──────────────────────────────────────────────

_ROUTES: list[OfflineRoute] = [
    # ── App launches ───────────────────────────────────────────
    OfflineRoute(
        patterns=[r"\bopen\s+chrome\b", r"\blaunch\s+chrome\b", r"\bstart\s+chrome\b"],
        response="Opening Chrome for you, Uzair.",
        command="Start-Process 'chrome'",
    ),
    OfflineRoute(
        patterns=[r"\bopen\s+(?:file\s*)?explorer\b", r"\bopen\s+(?:my\s+)?files\b"],
        response="Opening File Explorer, Uzair.",
        command="Start-Process 'explorer'",
    ),
    OfflineRoute(
        patterns=[r"\bopen\s+notepad\b", r"\blaunch\s+notepad\b"],
        response="Opening Notepad, Uzair.",
        command="Start-Process 'notepad'",
    ),
    OfflineRoute(
        patterns=[r"\bopen\s+(?:vs\s*code|vscode|visual\s*studio\s*code)\b"],
        response="Launching VS Code, Uzair.",
        command="Start-Process 'code'",
    ),
    OfflineRoute(
        patterns=[r"\bopen\s+(?:cmd|command\s*prompt|terminal)\b"],
        response="Opening a terminal window, Uzair.",
        command="Start-Process 'cmd'",
    ),
    OfflineRoute(
        patterns=[r"\bopen\s+(?:powershell|ps)\b"],
        response="Opening PowerShell, Uzair.",
        command="Start-Process 'powershell'",
    ),
    OfflineRoute(
        patterns=[r"\bopen\s+calculator\b", r"\blaunch\s+calculator\b", r"\bopen\s+calc\b"],
        response="Opening the calculator, Uzair.",
        command="Start-Process 'calc'",
    ),
    OfflineRoute(
        patterns=[r"\bopen\s+settings\b", r"\bopen\s+(?:windows\s+)?settings\b", r"\bsystem\s+settings\b"],
        response="Opening Windows Settings, Uzair.",
        command="Start-Process 'ms-settings:'",
    ),
    OfflineRoute(
        patterns=[r"\bopen\s+task\s*manager\b"],
        response="Opening Task Manager, Uzair.",
        command="Start-Process 'taskmgr'",
    ),
    OfflineRoute(
        patterns=[r"\bopen\s+(?:edge|microsoft\s+edge)\b"],
        response="Opening Microsoft Edge, Uzair.",
        command="Start-Process 'msedge'",
    ),
    OfflineRoute(
        patterns=[r"\bopen\s+(?:firefox|mozilla)\b"],
        response="Opening Firefox, Uzair.",
        command="Start-Process 'firefox'",
    ),

    # ── System queries ─────────────────────────────────────────
    OfflineRoute(
        patterns=[r"\b(?:check|show|get|what(?:'?s| is))\s+(?:my\s+)?battery\b", r"\bbattery\s+(?:level|status|percent)\b"],
        response="Checking your battery status, Uzair.",
        command="(Get-WmiObject Win32_Battery).EstimatedChargeRemaining | ForEach-Object { Write-Host \"Battery is at $_%.\" }",
    ),
    OfflineRoute(
        patterns=[r"\b(?:what(?:'?s| is)|show|get)\s+(?:my\s+)?(?:ip|ip\s*address)\b"],
        response="Fetching your IP address, Uzair.",
        command="(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.InterfaceAlias -notlike '*Loopback*' }).IPAddress | ForEach-Object { Write-Host \"Your IP: $_\" }",
    ),
    OfflineRoute(
        patterns=[r"\b(?:what(?:'?s| is)|show|check)\s+(?:the\s+)?(?:time|current\s+time)\b"],
        response="Here's the current time, Uzair.",
        command="Get-Date -Format 'dddd, MMMM dd, yyyy  hh:mm:ss tt'",
    ),
    OfflineRoute(
        patterns=[r"\b(?:what(?:'?s| is)|show|check)\s+(?:the\s+)?date\b", r"\btoday(?:'?s)?\s+date\b"],
        response="Here's today's date, Uzair.",
        command="Get-Date -Format 'dddd, MMMM dd, yyyy'",
    ),
    OfflineRoute(
        patterns=[r"\b(?:check|show|get)\s+(?:disk|storage|drive)\s*(?:space|usage)?\b", r"\bhow\s+much\s+(?:storage|space|disk)\b"],
        response="Checking your disk space, Uzair.",
        command="Get-PSDrive -PSProvider FileSystem | Select-Object Name, @{N='Used(GB)';E={[math]::Round($_.Used/1GB,1)}}, @{N='Free(GB)';E={[math]::Round($_.Free/1GB,1)}} | Format-Table -AutoSize",
    ),
    OfflineRoute(
        patterns=[r"\b(?:check|show|get|am\s+i)\s+(?:connected\s+to\s+)?(?:wifi|internet|network|online)\b"],
        response="Checking your network status, Uzair.",
        command="Test-Connection -ComputerName google.com -Count 1 -Quiet | ForEach-Object { if($_) { Write-Host 'You are connected to the internet.' } else { Write-Host 'No internet connection detected.' } }",
    ),
    OfflineRoute(
        patterns=[r"\bwho\s*am\s*i\b", r"\b(?:what(?:'?s| is)|show)\s+(?:my\s+)?(?:user\s*name|user)\b"],
        response="Let me check, Uzair.",
        command="Write-Host \"Logged in as: $env:USERNAME on $env:COMPUTERNAME\"",
    ),

    # ── System actions ─────────────────────────────────────────
    OfflineRoute(
        patterns=[r"\block\s+(?:the\s+)?(?:screen|computer|pc|laptop)\b", r"\block\s+it\b"],
        response="Locking your screen, Uzair.",
        command="rundll32.exe user32.dll,LockWorkStation",
    ),
    OfflineRoute(
        patterns=[r"\b(?:take\s+a\s+)?screenshot\b", r"\bscreen\s*capture\b", r"\bcapture\s+(?:the\s+)?screen\b"],
        response="Taking a screenshot, Uzair. It'll be saved to your desktop.",
        command="$path=\"$env:USERPROFILE\\Desktop\\screenshot_$(Get-Date -Format 'yyyyMMdd_HHmmss').png\"; Add-Type -AssemblyName System.Windows.Forms; $bmp = New-Object Drawing.Bitmap([System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width, [System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height); $g = [Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen(0,0,0,0,$bmp.Size); $bmp.Save($path); Write-Host \"Screenshot saved: $path\"",
    ),
    OfflineRoute(
        patterns=[r"\bvolume\s+up\b", r"\bincrease\s+(?:the\s+)?volume\b", r"\bturn\s+up\s+(?:the\s+)?volume\b"],
        response="Turning the volume up, Uzair.",
        command="$obj = New-Object -ComObject WScript.Shell; 1..5 | ForEach-Object { $obj.SendKeys([char]175) }; Write-Host 'Volume increased.'",
    ),
    OfflineRoute(
        patterns=[r"\bvolume\s+down\b", r"\bdecrease\s+(?:the\s+)?volume\b", r"\bturn\s+down\s+(?:the\s+)?volume\b", r"\blower\s+(?:the\s+)?volume\b"],
        response="Turning the volume down, Uzair.",
        command="$obj = New-Object -ComObject WScript.Shell; 1..5 | ForEach-Object { $obj.SendKeys([char]174) }; Write-Host 'Volume decreased.'",
    ),
    OfflineRoute(
        patterns=[r"\bmute\b", r"\bunmute\b", r"\btoggle\s+mute\b"],
        response="Toggling mute, Uzair.",
        command="$obj = New-Object -ComObject WScript.Shell; $obj.SendKeys([char]173); Write-Host 'Mute toggled.'",
    ),
    OfflineRoute(
        patterns=[r"\b(?:empty|clear)\s+(?:the\s+)?(?:recycle\s*bin|trash)\b"],
        response="Emptying the Recycle Bin, Uzair.",
        command="Clear-RecycleBin -Force -ErrorAction SilentlyContinue; Write-Host 'Recycle Bin emptied.'",
    ),
]

# ── Dynamic parameter routes (need regex captures) ─────────────

_DYNAMIC_ROUTES: list[tuple[str, Callable[[re.Match], dict]]] = [
    # "search for X" / "google X"
    (
        r"(?:search\s+(?:for\s+)?|google\s+)(.{3,120})",
        lambda m: {
            "thought": "User wants a web search — routing directly.",
            "response": f"Searching the web for \"{m.group(1).strip()}\", Uzair.",
            "command": f"Start-Process 'https://www.google.com/search?q={m.group(1).strip().replace(' ', '+')}'",
        },
    ),
    # "create folder X on desktop"
    (
        r"create\s+(?:a\s+)?folder\s+(?:called\s+|named\s+)?([a-zA-Z0-9_ -]+?)(?:\s+on\s+(?:my\s+)?desktop)?$",
        lambda m: {
            "thought": "User wants to create a folder on the desktop.",
            "response": f"Creating the \"{m.group(1).strip()}\" folder on your desktop, Uzair.",
            "command": f"New-Item -Path \"$env:USERPROFILE\\Desktop\\{m.group(1).strip()}\" -ItemType Directory -Force | ForEach-Object {{ Write-Host \"Folder created: $($_.FullName)\" }}",
        },
    ),
    # "open website X"
    (
        r"(?:open|go\s+to|visit)\s+(?:the\s+)?(?:website\s+|site\s+)?(?:https?://)?([a-zA-Z0-9][a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/\S*)?)",
        lambda m: {
            "thought": "User wants to open a website.",
            "response": f"Opening {m.group(1).strip()}, Uzair.",
            "command": f"Start-Process 'https://{m.group(1).strip()}'",
        },
    ),
]


def try_offline_route(user_message: str) -> dict | None:
    """
    Try to match a user message against known offline routes.

    Returns a dict with {thought, response, command} if matched, else None.
    When None is returned, the caller should fall through to the LLM.
    """
    text = (user_message or "").strip()
    if not text:
        return None

    # 1. Try static routes
    for route in _ROUTES:
        for pattern in route.patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return {
                    "thought": route.thought,
                    "response": route.response,
                    "command": route.command,
                }

    # 2. Try dynamic routes
    for pattern, builder in _DYNAMIC_ROUTES:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return builder(m)

    return None
