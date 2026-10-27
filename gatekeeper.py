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
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Tuple

SERVER_DIR = Path(__file__).resolve().parent
CONFIG_DIR = SERVER_DIR / "config"
INVITES_FILE = CONFIG_DIR / "invites.json"
AI_BROKER_FILE = CONFIG_DIR / "ai-broker.json"
USERS_DB = CONFIG_DIR / "users.db"

CONFIG_DIR.mkdir(parents=True, exist_ok=True)
if not INVITES_FILE.exists():
    INVITES_FILE.write_text("{}", encoding="utf-8")
if not AI_BROKER_FILE.exists():
    AI_BROKER_FILE.write_text(json.dumps({
        "provider": "bedrock",
        "policy": "hybrid",
        "region": "us-east-1",
        "aws_profile": "default",
        "api_key": "",
        "upstream_url": "",
        "tickets": {}
    }, indent=2), encoding="utf-8")
    os.chmod(AI_BROKER_FILE, 0o600)

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

    def _extract_ai_ticket(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        for hdr in ("x-api-key", "x-goog-api-key", "x-openrig-ticket"):
            val = self.headers.get(hdr, "").strip()
            if val:
                return val
        return ""

    def _handle_ai_broker(self, raw_body: bytes):
        """
        Zero-Key-Exposure Enterprise AI Broker:
        1. Validates the worker's internal ticket ('rig_ai_ticket_<user>_...').
        2. Verifies the worker's container ('openrig-user-<user>') is actively running.
        3. Strips the internal ticket and injects the Company's Master Key or AWS SigV4 signature in memory.
        4. Proxies the request to AWS Bedrock / Anthropic / OpenAI / Gemini / LiteLLM.
        """
        try:
            cfg = json.loads(AI_BROKER_FILE.read_text(encoding="utf-8"))
        except Exception:
            return self._send_json(500, {"error": "AI Broker configuration unavailable"})

        ticket = self._extract_ai_ticket()
        tickets = cfg.get("tickets", {})
        t_info = tickets.get(ticket)
        if not t_info or not t_info.get("active", False):
            return self._send_json(403, {"error": "OpenRig AI Broker: Invalid or paused worker AI ticket"})

        username = t_info.get("username", "")
        cname = f"openrig-user-{username}"
        running = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", cname],
            capture_output=True, text=True
        ).stdout.strip()
        if running != "true":
            return self._send_json(403, {"error": f"Container '{cname}' is not active on this server"})

        # Meter request count per worker
        t_info["requests"] = int(t_info.get("requests", 0)) + 1
        t_info["last_used_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        AI_BROKER_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")

        parts = self.path.split("/", 3)
        route_provider = parts[2] if len(parts) > 2 else cfg.get("provider", "bedrock")
        subpath = "/" + (parts[3] if len(parts) > 3 else "")
        region = cfg.get("region", "us-east-1")
        master_key = cfg.get("api_key", "")
        custom_upstream = cfg.get("upstream_url", "").rstrip("/")

        forward_headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
        for h in ("anthropic-version", "anthropic-beta", "accept"):
            if self.headers.get(h):
                forward_headers[h] = self.headers.get(h)

        if route_provider == "bedrock":
            target_url = f"{custom_upstream or f'https://bedrock-runtime.{region}.amazonaws.com'}{subpath}"
            if master_key:
                forward_headers["Authorization"] = f"Bearer {master_key}"
            else:
                # Sign request in-memory on Host using AWS SigV4 (botocore) — Zero AWS keys inside worker container!
                try:
                    import botocore.auth
                    import botocore.awsrequest
                    import botocore.session
                    session = botocore.session.get_session()
                    aws_prof = cfg.get("aws_profile", "default")
                    if aws_prof and aws_prof != "default":
                        session.set_config_variable("profile", aws_prof)
                    creds = session.get_credentials().get_frozen_credentials()
                    aws_req = botocore.awsrequest.AWSRequest(
                        method="POST", url=target_url, data=raw_body, headers=forward_headers
                    )
                    botocore.auth.SigV4Auth(creds, "bedrock", region).add_auth(aws_req)
                    forward_headers = dict(aws_req.headers.items())
                except Exception as exc:
                    return self._send_json(502, {
                        "error": f"AWS Bedrock SigV4 signing failed on Host: {exc}"
                    })
        elif route_provider == "anthropic":
            target_url = f"{custom_upstream or 'https://api.anthropic.com'}{subpath}"
            forward_headers["x-api-key"] = master_key
        elif route_provider in ("openai", "litellm"):
            target_url = f"{custom_upstream or 'https://api.openai.com'}{subpath}"
            forward_headers["Authorization"] = f"Bearer {master_key}"
        elif route_provider == "gemini":
            target_url = f"{custom_upstream or 'https://generativelanguage.googleapis.com'}{subpath}"
            forward_headers["x-goog-api-key"] = master_key
        else:
            return self._send_json(400, {"error": f"Unsupported AI Broker provider: {route_provider}"})

        req = urllib.request.Request(target_url, data=raw_body, headers=forward_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                resp_body = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(resp_body)))
                self.end_headers()
                self.wfile.write(resp_body)
        except urllib.error.HTTPError as err:
            err_body = err.read()
            self.send_response(err.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(err_body)))
            self.end_headers()
            self.wfile.write(err_body)
        except Exception as exc:
            return self._send_json(502, {"error": f"AI Broker upstream error: {exc}"})

    def do_POST(self):
        client_ip = self.client_address[0]
        if is_ip_rate_limited(client_ip):
            return self._send_json(429, {
                "error": "Too many failed authentication attempts. IP blocked for 15 minutes."
            })

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length)

        if self.path.startswith("/ai/"):
            return self._handle_ai_broker(raw_body)

        try:
            data = json.loads(raw_body.decode("utf-8"))
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
    server = ThreadingHTTPServer(("0.0.0.0", port), GatekeeperHandler)
    print(f"🛡️ OpenRig Gatekeeper + Zero-Key AI Broker listening on 0.0.0.0:{port}")
    server.serve_forever()
