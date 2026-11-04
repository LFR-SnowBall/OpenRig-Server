# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.0] - 2026-11-03

### Added
- **Self-Updating Engine & Semantic Versioning (`rig-server update / version`):**
  - Instant OTA updates directly from GitHub with zero server downtime.
  - Command `rig-server version` to inspect active release version and commit hash.
- **Military-Grade Encrypted Server Backup (`rig-server backup export / restore`):**
  - Encrypts single tenant workspaces or entire server states (all tenants, source code, `config/users.db`, quotas, and Gatekeeper policies) using **AES-256-CBC with PBKDF2** (100,000 iterations).
  - Produces portable, password-protected `.rigpack` bundles.
  - Automatically excludes bulky transient caches (`node_modules`, `target`, `.gradle`, etc.) while retaining all Git history and configurations.
  - Validates integrity and decrypts cleanly on any destination server.
- **Live Encrypted Server-to-Server Migration (`rig-server migrate push`):**
  - Streams encrypted server state directly over SSH to another bare-metal host, cloud VM, or desktop server.
- **Shared Zero-Key Corporate AI Broker (`rig-server ai`):**
  - Thread-safe AI broker supporting AWS Bedrock, Anthropic, Google Gemini, and OpenAI.
  - Native AWS SigV4 signing (`botocore`) in RAM using the host's AWS profile or IAM role.
  - Container-scoped tickets (`rig_ai_ticket_<user>_...`) preventing credential exfiltration from inside worker containers.
- **Enterprise DLP Governance & Multi-Tenant IPC Isolation:**
  - Enforced `--ipc=private` and root-owned `/etc/openrig/dlp-policy.json` per tenant container.
  - Added `rig-server brain-requests` review and approval flow for user graduation requests.
- **Live Cgroup v2 Hardware Upgrades (`rig-server requests`):**
  - Allows Server Admin to inspect and approve RAM and vCPU upgrades requested live by developers via `rig request`.

### Changed
- Standardized all repository branding, banners, and documentation to open-source personal author: `Luis Fernando Real (LFR-SnowBall) <luisitoreal01@gmail.com>`.
- Standardized all manager CLI strings, error handling, and prompts in 100% English.

---

## [1.0.0] - 2026-10-23

### Added
- Initial public release of **OpenRig-Server**.
- Multi-tenant developer & security workstation orchestrator.
- Hardware-bound device enrollment (`IOPlatformUUID` / `/etc/machine-id`) preventing key reuse on foreign machines.
- Anti-malware session guard (`/usr/local/bin/rig-session-guard`) and zero-trace TOTP Guest Mode.
- Live hot-scaling of RAM and CPU quotas without session restart (`rig-server scale`).
- Automated multi-tenant container lifecycle management (`rig-server user create/list/stop/start/remove`).
- Hardened Gatekeeper HTTP service on port 7070 with brute-force rate-limiting and anti-replay protection.
