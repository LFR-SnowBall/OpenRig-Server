# 🛡️ Security Policy (`OpenRig-Server`)

`OpenRig-Server` implements strict multi-tenant isolation, kernel-enforced read-only key mounts, and cryptographically signed hardware-bound authentication out of the box:

1. **Zero-Trust Anti-Key-Injection (Ed25519 Signed Manifest + Kernel `:ro` Mount + Fail-Closed `AuthorizedKeysCommand`):**
   - **Auto-Bootstrapped Server Authority (`config/authority/`):** On initialization, `rig-server` and `gatekeeper.py` generate a host-only Ed25519 signing authority (`server_ca` / `server_ca.pub`). The private signing key never enters any tenant container.
   - **Kernel-Enforced Read-Only Auth Volume (`/etc/openrig-auth:ro`):** Every tenant workstation mounts a dedicated volume `openrig-auth-<user>` in read-only mode (`:ro`). Even `sudo root` inside a tenant container receives `EROFS` (`Read-only file system`) if it attempts to append or alter SSH keys.
   - **Fail-Closed Verification on Every Login (`rig-verify-keys`):** `sshd` enforces `AuthorizedKeysFile none` and `AuthorizedKeysCommand /usr/local/bin/rig-verify-keys %u`, cryptographically verifying `authorized_keys` against `authorized_keys.sig` and `allowed_signers` for the exact tenant namespace (`openrig-authorized-keys:<user>`, preventing cross-tenant key replay). Missing or tampered signatures return zero keys (`fail-closed`) and write a critical security alert.
   - **60-Second Self-Destructing Guest Passes:** `rig guest` issues ephemeral TOTP-verified keys only to the signed volume (never mutating permanent host backups) and automatically republishes the clean permanent keyset after 60 seconds.
2. **Host-Mediated Docker & cgroup Resource Quotas:**
   - Tenant containers enforce `--ipc=private`, `--pids-limit 512`, `--memory`, `--memory-swap`, and `--cpus` cgroup quotas, and support scoped multi-tenant Docker proxy isolation (`OPENRIG_SCOPED_DOCKER_PROXY=true`).
3. **Gatekeeper Hardening (`gatekeeper.py`):**
   - **IP Rate-Limiting:** Blocks source IPs for 15 minutes (`HTTP 429`) after 5 failed authentication or enrollment attempts.
   - **Anti-Replay TOTP:** Burns every 6-digit Keychain TOTP code upon first use (`USED_TOTP_CODES`) so it cannot be replayed within the 30-second window.
   - **Strict Input Validation:** Enforces `^[a-zA-Z0-9._-]{2,32}$` on usernames and sanitizes device names to prevent command injection.

## 📬 Reporting a Vulnerability
Report vulnerabilities privately to **Luis Fernando Real (`LFR-SnowBall`)** at `luisitoreal01@gmail.com`.
