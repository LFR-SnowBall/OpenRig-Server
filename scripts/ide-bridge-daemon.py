#!/usr/bin/env python3
"""
Cross-Platform OpenRig Host IDE Bridge & Trigger Watcher (127.0.0.1 Only)
Supports macOS, Linux, and Windows in both 'server' and 'client' modes, and
routes across VS Code-family IDEs, Zed, and JetBrains Gateway.
"""

import argparse
import http.server
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

PORT = 7071
RE_SAFE_ALIAS = re.compile(r"^[a-zA-Z0-9._-]{1,64}$")
RE_SAFE_PATH = re.compile(r"^/[a-zA-Z0-9._/@+\- ]{0,512}$")
RE_SAFE_HOST = re.compile(r"^[a-zA-Z0-9._:-]{1,128}$")

VSCODE_IDE_ORDER = (
    "antigravity",
    "cursor",
    "code",
    "code-insiders",
    "windsurf",
    "kiro",
    "codium",
    "trae",
)

JETBRAINS_CODES = {
    "idea": "IU",
    "pycharm": "PY",
    "webstorm": "WS",
    "goland": "GO",
    "rider": "RD",
    "clion": "CL",
    "phpstorm": "PS",
    "rubymine": "RM",
    "rustrover": "RR",
    "gateway": "",
}

IDE_ALIASES = {
    "": "auto",
    "rig-open-ide": "auto",
    "default": "auto",
    "auto": "auto",
    "antigravity-ide": "antigravity",
    "vscode": "code",
    "vscodium": "codium",
    "intellij": "idea",
}

ALLOWED_IDES = frozenset(("auto",) + VSCODE_IDE_ORDER + ("zed",) + tuple(JETBRAINS_CODES.keys()))


def normalize_ide(name: str) -> str:
    n = (name or "").strip().lower()
    return IDE_ALIASES.get(n, n)


def is_safe_ide(name: str) -> bool:
    return normalize_ide(name) in ALLOWED_IDES


def is_safe_alias(alias: str) -> bool:
    return bool(RE_SAFE_ALIAS.match(alias or ""))


def is_safe_path(target_path: str) -> bool:
    if not target_path or ".." in target_path:
        return False
    return bool(RE_SAFE_PATH.match(target_path))


def get_default_host_ide() -> str:
    env_ide = os.environ.get("OPENRIG_IDE", "")
    if env_ide.strip():
        n = normalize_ide(env_ide)
        if n in ALLOWED_IDES:
            return n
    pref_file = Path.home() / ".openrig" / "ide"
    try:
        if pref_file.is_file():
            for line in pref_file.read_text(encoding="utf-8", errors="ignore").splitlines():
                val = line.strip()
                if val:
                    n = normalize_ide(val)
                    if n in ALLOWED_IDES:
                        return n
                    break
    except Exception:
        pass
    return "auto"


def _executable_ok(path_str: str) -> bool:
    if not path_str:
        return False
    if platform.system() == "Darwin" and path_str.endswith(".app"):
        return os.path.isdir(path_str)
    if not os.path.isfile(path_str):
        return False
    return platform.system() == "Windows" or os.access(path_str, os.X_OK)


def find_ide_binary(ide_id: str) -> str:
    sysname = platform.system()
    local_app = os.environ.get("LOCALAPPDATA", "")
    prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates_map: dict[str, tuple[list[str], tuple[str, ...]]] = {
        "antigravity": ([], ("antigravity-ide",)),
        "cursor": ([], ("cursor",)),
        "code": ([], ("code",)),
        "code-insiders": ([], ("code-insiders",)),
        "windsurf": ([], ("windsurf",)),
        "kiro": ([], ("kiro",)),
        "codium": ([], ("codium",)),
        "trae": ([], ("trae",)),
        "zed": ([], ("zed", "zedit")),
    }
    if ide_id not in candidates_map:
        return ""

    paths: list[str] = []
    if sysname == "Windows":
        win_paths = {
            "antigravity": [
                str(Path(local_app) / "Programs/Antigravity IDE/Antigravity IDE.exe"),
                str(Path(local_app) / "Programs/Antigravity IDE/bin/antigravity-ide.cmd"),
            ],
            "cursor": [
                str(Path(local_app) / "Programs/cursor/Cursor.exe"),
                str(Path(local_app) / "Programs/cursor/resources/app/bin/cursor.cmd"),
            ],
            "code": [
                str(Path(local_app) / "Programs/Microsoft VS Code/Code.exe"),
                str(Path(prog_files) / "Microsoft VS Code/Code.exe"),
                str(Path(local_app) / "Programs/Microsoft VS Code/bin/code.cmd"),
                str(Path(prog_files) / "Microsoft VS Code/bin/code.cmd"),
            ],
            "code-insiders": [
                str(Path(local_app) / "Programs/Microsoft VS Code Insiders/Code - Insiders.exe"),
                str(Path(prog_files) / "Microsoft VS Code Insiders/Code - Insiders.exe"),
                str(Path(local_app) / "Programs/Microsoft VS Code Insiders/bin/code-insiders.cmd"),
            ],
            "windsurf": [
                str(Path(local_app) / "Programs/Windsurf/Windsurf.exe"),
                str(Path(prog_files) / "Windsurf/Windsurf.exe"),
                str(Path(local_app) / "Programs/Windsurf/bin/windsurf.cmd"),
            ],
            "kiro": [
                str(Path(local_app) / "Programs/Kiro/Kiro.exe"),
                str(Path(prog_files) / "Kiro/Kiro.exe"),
                str(Path(local_app) / "Programs/Kiro/bin/kiro.cmd"),
            ],
            "codium": [
                str(Path(local_app) / "Programs/VSCodium/VSCodium.exe"),
                str(Path(prog_files) / "VSCodium/VSCodium.exe"),
                str(Path(local_app) / "Programs/VSCodium/bin/codium.cmd"),
            ],
            "trae": [
                str(Path(local_app) / "Programs/Trae/Trae.exe"),
                str(Path(prog_files) / "Trae/Trae.exe"),
                str(Path(local_app) / "Programs/Trae/bin/trae.cmd"),
            ],
            "zed": [
                str(Path(local_app) / "Programs/Zed/Zed.exe"),
                str(Path(local_app) / "Zed/Zed.exe"),
                str(Path(prog_files) / "Zed/Zed.exe"),
            ],
        }
        if local_app or prog_files:
            paths.extend(win_paths.get(ide_id, []))
    elif sysname == "Darwin":
        mac_paths = {
            "antigravity": [
                "/Applications/Antigravity IDE.app/Contents/Resources/app/bin/antigravity-ide",
                os.path.expanduser("~/Applications/Antigravity IDE.app/Contents/Resources/app/bin/antigravity-ide"),
                os.path.expanduser("~/.local/bin/antigravity-ide"),
                "/usr/local/bin/antigravity-ide",
            ],
            "cursor": [
                "/Applications/Cursor.app/Contents/Resources/app/bin/cursor",
                os.path.expanduser("~/Applications/Cursor.app/Contents/Resources/app/bin/cursor"),
                "/usr/local/bin/cursor",
            ],
            "code": [
                "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
                os.path.expanduser("~/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
                "/usr/local/bin/code",
            ],
            "code-insiders": [
                "/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/bin/code-insiders",
                "/usr/local/bin/code-insiders",
            ],
            "windsurf": [
                "/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf",
                os.path.expanduser("~/Applications/Windsurf.app/Contents/Resources/app/bin/windsurf"),
                "/usr/local/bin/windsurf",
            ],
            "kiro": [
                "/Applications/Kiro.app/Contents/Resources/app/bin/kiro",
                os.path.expanduser("~/Applications/Kiro.app/Contents/Resources/app/bin/kiro"),
                "/usr/local/bin/kiro",
            ],
            "codium": [
                "/Applications/VSCodium.app/Contents/Resources/app/bin/codium",
                "/usr/local/bin/codium",
            ],
            "trae": [
                "/Applications/Trae.app/Contents/Resources/app/bin/trae",
                "/usr/local/bin/trae",
            ],
            "zed": [
                os.path.expanduser("~/.local/bin/zed"),
                "/usr/local/bin/zed",
                "/Applications/Zed.app/Contents/MacOS/cli",
                "/Applications/Zed.app",
            ],
        }
        paths.extend(mac_paths.get(ide_id, []))
    else:
        linux_paths = {
            "antigravity": [os.path.expanduser("~/.local/bin/antigravity-ide"), "/usr/local/bin/antigravity-ide", "/usr/bin/antigravity-ide"],
            "cursor": ["/usr/local/bin/cursor", "/usr/bin/cursor"],
            "code": ["/usr/local/bin/code", "/usr/bin/code", "/usr/share/code/bin/code"],
            "code-insiders": ["/usr/local/bin/code-insiders", "/usr/bin/code-insiders"],
            "windsurf": ["/usr/local/bin/windsurf", "/usr/bin/windsurf"],
            "kiro": ["/usr/local/bin/kiro", "/usr/bin/kiro"],
            "codium": ["/usr/local/bin/codium", "/usr/bin/codium"],
            "trae": ["/usr/local/bin/trae", "/usr/bin/trae"],
            "zed": [os.path.expanduser("~/.local/bin/zed"), "/usr/local/bin/zed", "/usr/bin/zed"],
        }
        paths.extend(linux_paths.get(ide_id, []))

    for cand in paths:
        if _executable_ok(cand):
            return cand

    for cmd in candidates_map[ide_id][1]:
        found = shutil.which(cmd)
        if found and (sysname != "Windows" or "\\Microsoft\\WindowsApps\\" not in found):
            return found
    return ""


def is_jetbrains_gateway_available() -> bool:
    sysname = platform.system()
    if sysname == "Windows":
        try:
            import winreg  # type: ignore

            for root, sub in (
                (winreg.HKEY_CLASSES_ROOT, "jetbrains-gateway"),
                (winreg.HKEY_CURRENT_USER, r"Software\Classes\jetbrains-gateway"),
                (winreg.HKEY_LOCAL_MACHINE, r"Software\Classes\jetbrains-gateway"),
            ):
                try:
                    with winreg.OpenKey(root, sub):
                        return True
                except OSError:
                    pass
        except Exception:
            pass
        local_app = os.environ.get("LOCALAPPDATA", "")
        if local_app:
            for rel in ("JetBrains/Toolbox/bin/jetbrains-toolbox.exe", "Programs/JetBrains Gateway/bin/gateway64.exe"):
                if os.path.isfile(str(Path(local_app) / rel)):
                    return True
        return False

    if sysname == "Darwin":
        for app in (
            "/Applications/JetBrains Gateway.app",
            os.path.expanduser("~/Applications/JetBrains Gateway.app"),
            "/Applications/JetBrains Toolbox.app",
            os.path.expanduser("~/Applications/JetBrains Toolbox.app"),
        ):
            if os.path.exists(app):
                return True
        return bool(shutil.which("jetbrains-gateway"))

    if shutil.which("jetbrains-gateway"):
        return True
    if shutil.which("xdg-mime"):
        try:
            res = subprocess.run(
                ["xdg-mime", "query", "default", "x-scheme-handler/jetbrains-gateway"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                return True
        except Exception:
            pass
    return False


def get_installed_ides() -> list[str]:
    found: list[str] = []
    for ide_id in VSCODE_IDE_ORDER:
        if find_ide_binary(ide_id):
            found.append(ide_id)
    if find_ide_binary("zed"):
        found.append("zed")
    if is_jetbrains_gateway_available():
        found.extend(JETBRAINS_CODES.keys())
    return found


def resolve_ssh_target(alias: str, ssh_config_path: Path | None = None) -> tuple[str, str, str]:
    host_name, port, user = alias, "22", "rig"
    cfg = ssh_config_path if ssh_config_path is not None else (Path.home() / ".ssh" / "config")
    if not cfg.is_file():
        return host_name, port, user

    in_block = False
    for raw in cfg.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m_host = re.match(r"^Host\s+(.+)$", line, re.IGNORECASE)
        if m_host:
            patterns = m_host.group(1).strip().split()
            in_block = alias in patterns
            continue
        if re.match(r"^Match\s+", line, re.IGNORECASE):
            in_block = False
            continue
        if not in_block:
            continue
        m_hn = re.match(r"^HostName\s+(\S+)$", line, re.IGNORECASE)
        if m_hn and RE_SAFE_HOST.match(m_hn.group(1)):
            host_name = m_hn.group(1)
            continue
        m_port = re.match(r"^Port\s+(\d{1,5})$", line, re.IGNORECASE)
        if m_port and 1 <= int(m_port.group(1)) <= 65535:
            port = m_port.group(1)
            continue
        m_user = re.match(r"^User\s+(\S+)$", line, re.IGNORECASE)
        if m_user and RE_SAFE_ALIAS.match(m_user.group(1)):
            user = m_user.group(1)
    return host_name, port, user


def resolve_host_ide(requested_ide: str = "auto") -> dict[str, str] | None:
    norm = normalize_ide(requested_ide)
    if norm not in ALLOWED_IDES:
        return None

    if norm == "auto":
        norm = get_default_host_ide()

    if norm == "auto":
        for ide_id in VSCODE_IDE_ORDER:
            b = find_ide_binary(ide_id)
            if b:
                return {"id": ide_id, "protocol": "vscode", "binary": b, "code": ""}
        zb = find_ide_binary("zed")
        if zb:
            return {"id": "zed", "protocol": "zed", "binary": zb, "code": ""}
        if is_jetbrains_gateway_available():
            return {"id": "gateway", "protocol": "jetbrains", "binary": "jetbrains-gateway://", "code": ""}
        return None

    if norm in VSCODE_IDE_ORDER:
        b = find_ide_binary(norm)
        if b:
            return {"id": norm, "protocol": "vscode", "binary": b, "code": ""}
        default_ide = get_default_host_ide()
        if default_ide in VSCODE_IDE_ORDER and default_ide != norm:
            db = find_ide_binary(default_ide)
            if db:
                return {"id": default_ide, "protocol": "vscode", "binary": db, "code": ""}
        for ide_id in VSCODE_IDE_ORDER:
            fb = find_ide_binary(ide_id)
            if fb:
                return {"id": ide_id, "protocol": "vscode", "binary": fb, "code": ""}
        return None

    if norm == "zed":
        zb = find_ide_binary("zed")
        if zb:
            return {"id": "zed", "protocol": "zed", "binary": zb, "code": ""}
        return None

    if norm in JETBRAINS_CODES:
        if is_jetbrains_gateway_available():
            return {"id": norm, "protocol": "jetbrains", "binary": "jetbrains-gateway://", "code": JETBRAINS_CODES[norm]}
        return None

    return None


def _spawn_command(cmd: list[str], is_batch: bool = False) -> None:
    env = os.environ.copy()
    env.pop("ELECTRON_RUN_AS_NODE", None)
    env.pop("VSCODE_DEV", None)
    kwargs: dict[str, object] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
    }
    if platform.system() == "Windows":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) if is_batch else 0
    subprocess.Popen(cmd, **kwargs)


def open_ide(alias: str, target_path: str, requested_ide: str = "auto") -> bool:
    if not is_safe_alias(alias) or not is_safe_path(target_path) or not is_safe_ide(requested_ide):
        print(f"[REJECTED] invalid alias={alias!r} path={target_path!r} ide={requested_ide!r}", file=sys.stderr)
        return False

    req_norm = normalize_ide(requested_ide)
    resolved = resolve_host_ide(req_norm)
    if not resolved:
        print(f"No compatible IDE found (requested={req_norm}).", file=sys.stderr)
        return False

    uri_path = target_path.replace(" ", "%20")
    proto = resolved["protocol"]

    if proto == "vscode":
        uri = f"vscode-remote://ssh-remote+{alias}{uri_path}"
        ide_bin = resolved["binary"]
        print(f"[OpenRig Host Bridge] requested={req_norm} resolved={resolved['id']} uri={uri}", flush=True)
        _spawn_command([ide_bin, "--new-window", "--folder-uri", uri], is_batch=ide_bin.lower().endswith((".cmd", ".bat")))
        return True

    if proto == "zed":
        uri = f"ssh://{alias}{uri_path}"
        ide_bin = resolved["binary"]
        print(f"[OpenRig Host Bridge] requested={req_norm} resolved=zed uri={uri}", flush=True)
        if platform.system() == "Darwin" and ide_bin.endswith(".app"):
            _spawn_command(["open", "-a", ide_bin, uri])
        else:
            _spawn_command([ide_bin, uri], is_batch=ide_bin.lower().endswith((".cmd", ".bat")))
        return True

    if proto == "jetbrains":
        host_name, port, user = resolve_ssh_target(alias)
        enc_host = urllib.parse.quote(host_name, safe="")
        enc_user = urllib.parse.quote(user, safe="")
        enc_path = urllib.parse.quote(target_path, safe="")
        gw_uri = f"jetbrains-gateway://connect#host={enc_host}&port={port}&user={enc_user}&projectPath={enc_path}&type=ssh&deploy=true"
        if resolved["code"]:
            gw_uri += f"&ideCode={resolved['code']}"
        print(f"[OpenRig Host Bridge] requested={req_norm} resolved={resolved['id']} uri={gw_uri}", flush=True)
        sysname = platform.system()
        if sysname == "Windows":
            os.startfile(gw_uri)  # type: ignore[attr-defined]
        elif sysname == "Darwin":
            _spawn_command(["open", gw_uri])
        else:
            _spawn_command(["xdg-open", gw_uri])
        return True

    return False


def speak_text(text: str) -> None:
    clean = re.sub(r"[\x00-\x1f\x7f]", " ", text or "").strip()[:500]
    if not clean:
        return
    sysname = platform.system()
    try:
        if sysname == "Darwin" and shutil.which("say"):
            subprocess.Popen(["say", "-v", "Paulina", clean], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif sysname == "Linux":
            for tts in ("spd-say", "espeak"):
                if shutil.which(tts):
                    subprocess.Popen([tts, clean], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break
    except Exception:
        pass


class BridgeHandler(http.server.BaseHTTPRequestHandler):
    default_alias = "openrig"

    def _send(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/health":
            return self._send(200, b"OK\n")
        if parsed.path == "/ides":
            def_ide = get_default_host_ide()
            inst = get_installed_ides()
            inst_str = ",".join(inst) if inst else "none"
            payload = f"default={def_ide}\ninstalled={inst_str}\n".encode("utf-8")
            return self._send(200, payload)
        if parsed.path == "/open":
            params = urllib.parse.parse_qs(parsed.query)
            alias = params.get("alias", [self.default_alias])[0]
            target_path = params.get("path", ["/workspace"])[0]
            req_ide = params.get("ide", ["auto"])[0]
            if not is_safe_alias(alias) or not is_safe_path(target_path) or not is_safe_ide(req_ide):
                return self._send(400, b"INVALID\n")
            if open_ide(alias, target_path, req_ide):
                return self._send(200, b"OK\n")
            return self._send(500, b"IDE_NOT_FOUND\n")
        return self._send(404, b"NOT_FOUND\n")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/say", "/voice", "/"):
            length = min(int(self.headers.get("Content-Length", "0") or "0"), 16384)
            text = self.rfile.read(length).decode("utf-8", errors="ignore").strip()
            speak_text(text)
            return self._send(200, b"OK\n")
        return self._send(404, b"NOT_FOUND\n")

    def log_message(self, format, *args):
        pass


def _process_trigger_file(trig: Path, alias: str) -> None:
    if not trig.is_file():
        return
    raw = trig.read_text(encoding="utf-8", errors="ignore").splitlines()
    trig.unlink(missing_ok=True)
    target = raw[0].strip() if len(raw) >= 1 and raw[0].strip() else "/workspace"
    req_ide = raw[1].strip() if len(raw) >= 2 and raw[1].strip() else "auto"
    open_ide(alias, target, req_ide)


def trigger_watcher_loop(mode: str, watch_root: Path):
    while True:
        try:
            if watch_root.is_dir():
                if mode == "server":
                    for child in watch_root.iterdir():
                        if child.is_dir():
                            _process_trigger_file(child / ".openrig-ide-trigger", f"openrig-{child.name}")
                else:
                    _process_trigger_file(watch_root / ".openrig-ide-trigger", "openrig")
        except Exception:
            pass
        time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("server", "client"), default="client")
    parser.add_argument("--watch-root", default="")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--open", action="store_true", help="Open IDE once and exit")
    parser.add_argument("--alias", default="")
    parser.add_argument("--path", default="/workspace")
    parser.add_argument("--ide", default="auto")
    parser.add_argument("--list-ides", action="store_true", help="Print default and installed IDEs")
    args = parser.parse_args()

    if args.list_ides:
        def_ide = get_default_host_ide()
        inst = get_installed_ides()
        print(f"default={def_ide}")
        print(f"installed={','.join(inst) if inst else 'none'}")
        return

    if args.open:
        alias = args.alias or ("openrig-snowball" if args.mode == "server" else "openrig")
        ok = open_ide(alias, args.path, args.ide)
        sys.exit(0 if ok else 1)

    BridgeHandler.default_alias = "openrig-snowball" if args.mode == "server" else "openrig"
    if args.watch_root:
        t = threading.Thread(target=trigger_watcher_loop, args=(args.mode, Path(args.watch_root)), daemon=True)
        t.start()

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), BridgeHandler)
    print(f"[OpenRig Host IDE Bridge] Listening on 127.0.0.1:{args.port} (mode={args.mode})...", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
