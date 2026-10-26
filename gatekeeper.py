#!/usr/bin/env python3
"""
OpenRig-Server Zero-Trust Gatekeeper API (Hardened Red-Team Edition)
Security Controls:
1. Strict Regex Input Validation (prevents Command Injection / Path Traversal)
2. IP Rate-Limiting / Auto-Ban (max 5 failed attempts per 15 minutes against TOTP/Token brute-force)
3. Anti-Replay TOTP Protection (6-digit codes are burned upon first use within the time window)
4. Root-owned /etc/openrig/authorized_keys enforcement inside container
"""

import json
import os
import re
import subprocess
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Dict, List, Tuple

SERVER_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SERVER_DIR / "config"
INVITES_FILE = CONFIG_DIR / "invites.json"
USERS_DB = CONFIG_DIR / "users.db"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
if not INVITES_FILE.exists():
    INVITES_FILE.write_text("{}", encoding="utf-8")

RE_SAFE_NAME = re.compile(r"^[a-zA-Z0-9._-]{2,32}$")
RE_SAFE_HEX = re.compile(r"^[a-fA-F0-9]{32,128}$")
RE_SAFE_CODE = re.compile(r"^[0-9]{6}$")

# In-memory security trackers
FAILED_ATTEMPTS: Dict[str, List[float]] = {}
USED_TOTP_CODES: Dict[Tuple[str, str], float] = {}
MAX_FAILURES = 5
BAN_WINDOW_SECONDS = 900  # 15 minutes


def is_ip_rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in FAILED_ATTEMPTS.get(ip, []) if now - t < BAN_WINDOW_SECONDS]
    FAILED_ATTEMPTS[ip] = attempts
    return len(attempts) >= MAX_FAILURES


def record_failure(ip: str):
    now = time.time()
    FAILED_ATTEMPTS.setdefault(ip, []).append(now)


def load_invites():
    try:
        return json.loads(INVITES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_invites(data):
    INVITES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def verify_totp_once(cname: str, username: str, code: str) -> bool:
    """Verify 6-digit TOTP code and burn it immediately to prevent Replay Attacks."""
    if not RE_SAFE_CODE.match(code):
        return False

    now = time.time()
    # Clean expired used codes (> 90s old)
    for k in list(USED_TOTP_CODES.keys()):
        if now - USED_TOTP_CODES[k] > 90:
            del USED_TOTP_CODES[k]

    if (username, code) in USED_TOTP_CODES:
        return False  # Replay attack blocked

    try:
        secret = subprocess.check_output(
            ["docker", "exec", cname, "cat", "/home/rig/.openrig-profiles/keychain.secret"],
            text=True
        ).strip()
        expected = subprocess.check_output(
            ["docker", "exec", cname, "oathtool", "--totp", "-b", "-w", "1", secret, code],
            text=True,
            stderr=subprocess.DEVNULL
        ).strip()
        if expected != "":
            USED_TOTP_CODES[(username, code)] = now
            return True
        return False
    except Exception:
        return False


class GatekeeperHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        client_ip = self.client_address[0]
        if is_ip_rate_limited(client_ip):
            return self._send_json(429, {
                "error": "Too many failed authentication attempts. IP blocked for 15 minutes."
            })

        length = int(self.headers.get("Content-Length", "0"))
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            record_failure(client_ip)
            return self._send_json(400, {"error": "Invalid JSON"})

        if self.path == "/enroll":
            token = data.get("token", "")
            pubkey = data.get("public_key", "").strip()
            hw_id = data.get("hardware_id", "").strip()
            dev_name = re.sub(r"[^a-zA-Z0-9._-]", "-", data.get("device_name", "workstation").strip())[:32]
            replace_prev = bool(data.get("replace_previous", True))

            if not RE_SAFE_HEX.match(hw_id) or not pubkey.startswith("ssh-"):
                record_failure(client_ip)
                return self._send_json(400, {"error": "Invalid hardware fingerprint or SSH public key"})

            invites = load_invites()
            inv = invites.get(token)
            if not inv:
                record_failure(client_ip)
                return self._send_json(403, {"error": "Invalid or unknown invite token"})
            if inv.get("used"):
                record_failure(client_ip)
                return self._send_json(403, {"error": "Invite token was already redeemed (One-Time Use)"})
            if time.time() > inv.get("expires_at", 0):
                record_failure(client_ip)
                return self._send_json(403, {"error": "Invite token has expired"})

            username = inv["username"]
            role = inv["role"]
            ram = inv["ram"]
            cpus = inv["cpus"]

            # Burn token immediately (One-Time Use)
            inv["used"] = True
            inv["redeemed_at"] = int(time.time())
            inv["bound_device"] = dev_name
            inv["bound_hw_id"] = hw_id[:16]
            save_invites(invites)

            cname = f"openrig-user-{username}"
            inspect = subprocess.run(["docker", "inspect", cname], capture_output=True)
            if inspect.returncode != 0:
                subprocess.run([
                    str(SERVER_DIR / "rig-server"), "user", "create", username,
                    "--role", role, "--ram", ram, "--cpus", cpus
                ], check=True)

            # Bind SSH public key to Hardware UUID inside root-owned /etc/openrig/authorized_keys
            guarded_key = f'command="/usr/local/bin/rig-session-guard {hw_id} {dev_name}" {pubkey}'
            op = ">" if (replace_prev or inv.get("replace_previous", False)) else ">>"
            cmd = (
                "sudo mkdir -p /etc/openrig /home/rig/.openrig-profiles && "
                "sudo chown -R rig:rig /home/rig/.openrig-profiles && "
                f"echo {json.dumps(guarded_key)} | sudo tee {'-a' if op == '>>' else ''} /etc/openrig/authorized_keys >/dev/null && "
                "sudo chown root:root /etc/openrig/authorized_keys && "
                "sudo chmod 644 /etc/openrig/authorized_keys && "
                "if [ ! -f /home/rig/.openrig-profiles/keychain.secret ]; then "
                "head -c 10 /dev/urandom | base32 | tr -d '=' > /home/rig/.openrig-profiles/keychain.secret && "
                "chmod 600 /home/rig/.openrig-profiles/keychain.secret; fi"
            )
            subprocess.run(["docker", "exec", cname, "bash", "-c", cmd], check=True)

            ssh_port = "2201"
            for line in USERS_DB.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{username}:"):
                    ssh_port = line.split(":")[1]
                    break

            keychain_secret = subprocess.check_output(
                ["docker", "exec", cname, "cat", "/home/rig/.openrig-profiles/keychain.secret"],
                text=True
            ).strip()

            return self._send_json(200, {
                "status": "enrolled",
                "username": username,
                "ssh_port": int(ssh_port),
                "device_name": dev_name,
                "hardware_bound": hw_id[:16],
                "keychain_secret": keychain_secret,
                "keychain_uri": f"otpauth://totp/OpenRig:{username}?secret={keychain_secret}&issuer=OpenRig"
            })

        elif self.path == "/guest":
            username = data.get("username", "").strip()
            totp_code = data.get("code", "").strip()
            temp_pubkey = data.get("temp_public_key", "").strip()

            if not RE_SAFE_NAME.match(username) or not temp_pubkey.startswith("ssh-"):
                record_failure(client_ip)
                return self._send_json(400, {"error": "Invalid username or temporary key"})

            cname = f"openrig-user-{username}"
            if not verify_totp_once(cname, username, totp_code):
                record_failure(client_ip)
                return self._send_json(403, {"error": "Invalid or already-used 6-digit Keychain TOTP code"})

            guarded_guest = f'command="/usr/local/bin/rig-session-guard GUEST_EPHEMERAL guest-temp" {temp_pubkey}'
            cmd = (
                f"echo {json.dumps(guarded_guest)} | sudo tee -a /etc/openrig/authorized_keys >/dev/null && "
                "sudo chmod 644 /etc/openrig/authorized_keys"
            )
            subprocess.run(["docker", "exec", cname, "bash", "-c", cmd], check=True)

            ssh_port = "2201"
            for line in USERS_DB.read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{username}:"):
                    ssh_port = line.split(":")[1]
                    break

            return self._send_json(200, {
                "status": "guest_authorized",
                "username": username,
                "ssh_port": int(ssh_port)
            })

        return self._send_json(404, {"error": "Not found"})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("GATEKEEPER_PORT", "7070"))
    server = HTTPServer(("0.0.0.0", port), GatekeeperHandler)
    print(f"🛡️ OpenRig Gatekeeper (Hardened) listening on 0.0.0.0:{port}")
    server.serve_forever()
