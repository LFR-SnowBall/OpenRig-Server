# 🛡️ Security Policy (`OpenRig-Server`)

`OpenRig-Server` implements strict multi-tenant isolation and hardware-bound authentication controls:

1. **Root-Owned Guarded `authorized_keys` (`/etc/openrig/authorized_keys`):**
   - Owned by `root:root` (`0644`) inside each tenant container so the non-root developer account (`rig`) cannot strip or alter `command="/usr/local/bin/rig-session-guard <HW_ID> <DEV_NAME>"`.
2. **No Host Docker Socket in Multi-Tenant Containers:**
   - Tenant containers (`openrig-user-<name>`) never mount the server host's `/var/run/docker.sock` and enforce `--pids-limit 512`, `--memory`, `--memory-swap`, and `--cpus` cgroup quotas.
3. **Gatekeeper Hardening (`gatekeeper.py`):**
   - **IP Rate-Limiting:** Blocks source IPs for 15 minutes (`HTTP 429`) after 5 failed authentication or enrollment attempts.
   - **Anti-Replay TOTP:** Burns every 6-digit Keychain TOTP code upon first use (`USED_TOTP_CODES`) so it cannot be replayed within the 30-second window.
   - **Strict Input Validation:** Enforces `^[a-zA-Z0-9_-]{1,32}$` on usernames, roles, and device names to prevent command injection.

## 📬 Reporting a Vulnerability
Report vulnerabilities privately to **Luis Fernando Real (`LFR-SnowBall`)** at `luisitoreal01@gmail.com`.
