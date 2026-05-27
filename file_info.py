import datetime as dt
import mimetypes
import os
import platform
import shutil
import subprocess
from pathlib import Path


WINDOWS_SYSTEM_DIRS = (
    "\\windows\\",
    "\\programdata\\microsoft\\",
)

SYSTEM_DESCRIPTIONS = {
    "ntoskrnl.exe": "Windows NT kernel",
    "explorer.exe": "Windows shell",
    "svchost.exe": "Service Host process",
    "winlogon.exe": "Windows logon process",
    "csrss.exe": "Client/Server Runtime subsystem",
    "lsass.exe": "Local Security Authority process",
    "services.exe": "Service Control Manager",
    "smss.exe": "Session Manager subsystem",
    "cmd.exe": "Windows command interpreter",
    "powershell.exe": "Windows PowerShell",
}


def analyze_path(path: str, scan_defender: bool = False) -> dict:
    info = {
        "extension": "",
        "file_type": "Directory" if os.path.isdir(path) else "Unknown file",
        "modified": "",
        "owner": "",
        "attributes": "",
        "is_system": False,
        "system_description": "",
        "defender_status": "Not scanned",
    }

    try:
        stat = os.stat(path, follow_symlinks=False)
    except OSError:
        return info

    p = Path(path)
    if not os.path.isdir(path):
        info["extension"] = p.suffix.lower() or "(none)"
        guessed_type = mimetypes.guess_type(path)[0]
        info["file_type"] = guessed_type or f"{info['extension']} file"

    info["modified"] = dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    info["owner"] = _get_owner(p)
    info["attributes"] = _get_attributes(path, stat)
    info["is_system"], info["system_description"] = _detect_system_file(path, info["attributes"])

    if scan_defender:
        info["defender_status"] = scan_with_windows_defender(path)

    return info


def apply_info_to_node(node, info: dict) -> None:
    skip = {"children", "parent"}
    for key, value in info.items():
        if key not in skip and hasattr(node, key):
            setattr(node, key, value)


def node_to_dict(node) -> dict:
    return {
        "name": node.name,
        "path": node.path,
        "is_dir": node.is_dir,
        "size": node.size,
        "children": [node_to_dict(child) for child in node.children],
        "extension": node.extension,
        "file_type": node.file_type,
        "modified": node.modified,
        "owner": node.owner,
        "attributes": node.attributes,
        "is_system": node.is_system,
        "system_description": node.system_description,
        "defender_status": node.defender_status,
        "scan_error": node.scan_error,
    }


def scan_with_windows_defender(path: str) -> str:
    if os.name != "nt":
        return "Windows Defender is available only on Windows"

    mpcmdrun = _find_mpcmdrun()
    if not mpcmdrun:
        return "MpCmdRun.exe not found"

    try:
        result = subprocess.run(
            [mpcmdrun, "-Scan", "-ScanType", "3", "-File", path],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return "Defender scan timeout"
    except OSError as exc:
        return f"Defender scan failed: {exc}"

    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode == 0:
        return "No threats found"
    if result.returncode == 2 and "Threat" in output:
        return "Threat found or remediation required"
    if result.returncode != 0 and "Threat" not in output:
        return "No threats found"
    return f"Defender returned code {result.returncode}"


def _get_owner(path: Path) -> str:
    try:
        if os.name == "nt":
            import win32security
            sd = win32security.GetFileSecurity(str(path), win32security.OWNER_SECURITY_INFORMATION)
            sid = sd.GetSecurityDescriptorOwner()
            name, domain, _ = win32security.LookupAccountSid(None, sid)
            return f"{domain}\\{name}"
        return path.owner()
    except Exception:
        return "Unknown"


def _get_attributes(path: str, stat) -> str:
    attrs = []
    if os.path.isdir(path):
        attrs.append("Directory")
    if os.path.islink(path):
        attrs.append("Symlink")
    if os.name == "nt" and hasattr(stat, "st_file_attributes"):
        flags = stat.st_file_attributes
        mapping = (
            (0x1, "Read-only"),
            (0x2, "Hidden"),
            (0x4, "System"),
            (0x20, "Archive"),
            (0x400, "Reparse point"),
            (0x800, "Compressed"),
            (0x4000, "Encrypted"),
        )
        attrs.extend(name for bit, name in mapping if flags & bit)
    return ", ".join(dict.fromkeys(attrs)) or "Normal"


def _detect_system_file(path: str, attributes: str) -> tuple[bool, str]:
    lowered = path.lower()
    name = os.path.basename(lowered)
    if "System" in attributes:
        return True, SYSTEM_DESCRIPTIONS.get(name, "Windows system-marked file")
    if os.name == "nt" and any(marker in lowered for marker in WINDOWS_SYSTEM_DIRS):
        return True, SYSTEM_DESCRIPTIONS.get(name, "File is inside a Windows system/application directory")
    return False, ""


def _find_mpcmdrun() -> str:
    direct = shutil.which("MpCmdRun.exe")
    if direct:
        return direct

    candidates = [
        Path(os.environ.get("ProgramFiles", "")) / "Windows Defender" / "MpCmdRun.exe",
        Path(os.environ.get("ProgramData", "")) / "Microsoft" / "Windows Defender" / "Platform",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
        if candidate.is_dir():
            versions = sorted(candidate.glob("*/MpCmdRun.exe"), reverse=True)
            if versions:
                return str(versions[0])
    return ""


def get_system_status() -> dict:
    return {
        "host": platform.node() or os.environ.get("COMPUTERNAME", "unknown"),
        "user": os.environ.get("USERNAME") or os.environ.get("USER") or "unknown",
        "os": platform.platform(),
        "time": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "processes": _list_processes(),
    }


def _list_processes(limit: int = 30) -> list[str]:
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            lines = result.stdout.splitlines()
            return [line.split('","')[0].strip('"') for line in lines[:limit] if line.strip()]
        result = subprocess.run(
            ["ps", "-eo", "comm="],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return [line.strip() for line in result.stdout.splitlines()[:limit] if line.strip()]
    except Exception as exc:
        return [f"Unable to read processes: {exc}"]
