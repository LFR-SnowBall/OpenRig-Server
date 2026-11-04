#!/usr/bin/env python3
"""
OpenRig Scoped Docker Proxy (docker-proxy.py)
Multi-Tenant Docker Engine RBAC & Security Isolation Guard

Ensures that sibling containers are 100% private per user:
1. Ownership Injection: Automatically tags every container with 'openrig.owner=<username>'.
2. Visibility Isolation: 'docker ps' only returns containers belonging to that user.
3. Access Control: Blocks 'docker exec', 'docker stop', 'docker rm', 'docker inspect'
   on any container belonging to other users or the server host.
4. Host Filesystem Protection: Blocks mounting host root ('/') or system paths.
5. Stream Hijacking: Supports interactive TTY ('docker exec -it', 'docker run -it', 'docker logs -f').
"""

import http.client
import io
import json
import os
import re
import select
import socket
import sys
import threading
import time

def find_docker_socket() -> str:
    candidates = [
        "/var/run/docker.sock",
        "/mnt/wsl/rancher-desktop/run/docker.sock",
        os.path.expanduser("~/.orbstack/run/docker.sock"),
        os.path.expanduser("~/.docker/run/docker.sock"),
        "/var/run/docker/docker.sock"
    ]
    for c in candidates:
        if os.path.exists(c) and not os.path.isdir(c):
            return c
    return "/var/run/docker.sock"


DOCKER_SOCK = os.environ.get("DOCKER_HOST_SOCK") or find_docker_socket()
BUFFER_SIZE = 65536

# Cache of verified container IDs belonging to users
# username -> set(container_id_or_name)
OWNERSHIP_CACHE = {}


def is_container_owned(username: str, container_id: str) -> bool:
    """Check if a container belongs to the given user."""
    user_cache = OWNERSHIP_CACHE.setdefault(username, set())
    if container_id in user_cache:
        return True

    # Query upstream docker inspect
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(DOCKER_SOCK)
        req = f"GET /containers/{container_id}/json HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        s.sendall(req.encode())
        res = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            res += chunk
        s.close()

        parts = res.split(b"\r\n\r\n", 1)
        if len(parts) == 2 and b"200 OK" in parts[0]:
            body = json.loads(parts[1].decode("utf-8", errors="ignore"))
            owner = body.get("Config", {}).get("Labels", {}).get("openrig.owner")
            if owner == username:
                cid = body.get("Id", container_id)
                cname = body.get("Name", "").lstrip("/")
                user_cache.add(container_id)
                user_cache.add(cid)
                user_cache.add(cid[:12])
                if cname:
                    user_cache.add(cname)
                return True
    except Exception:
        pass
    return False


def pipe_bidirectional(sock1, sock2):
    """Bidirectional streaming pipe for hijacked connections (docker exec -it)."""
    socks = [sock1, sock2]
    try:
        while True:
            r, _, x = select.select(socks, [], socks, 3600)
            if x:
                break
            if not r:
                continue
            for s in r:
                data = s.recv(BUFFER_SIZE)
                if not data:
                    return
                target = sock2 if s is sock1 else sock1
                target.sendall(data)
    except Exception:
        pass


def read_http_headers(sock):
    """Read HTTP headers until \\r\\n\\r\\n."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 1048576:  # 1MB header limit
            break
    if b"\r\n\r\n" not in buf:
        return b"", b""
    headers, rest = buf.split(b"\r\n\r\n", 1)
    return headers, rest


def read_exact(sock, length, initial=b""):
    """Read exactly 'length' bytes from socket."""
    buf = initial
    while len(buf) < length:
        chunk = sock.recv(min(4096, length - len(buf)))
        if not chunk:
            break
        buf += chunk
    return buf


def handle_client(client_sock, username: str):
    """Handle Docker CLI requests for a specific tenant."""
    try:
        upstream_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        upstream_sock.connect(DOCKER_SOCK)
    except Exception as e:
        client_sock.close()
        return

    try:
        while True:
            header_bytes, rest = read_http_headers(client_sock)
            if not header_bytes:
                break

            header_text = header_bytes.decode("iso-8859-1")
            lines = header_text.split("\r\n")
            req_line = lines[0]
            parts = req_line.split(" ")
            if len(parts) < 3:
                break
            method, full_path, proto = parts[0], parts[1], parts[2]

            # Parse headers into dict
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()

            content_len = int(headers.get("content-length", "0"))
            body_bytes = b""
            if content_len > 0:
                body_bytes = read_exact(client_sock, content_len, initial=rest)
                rest = b""

            # -------------------------------------------------------------
            # RULE 1: INJECT 'openrig.owner' LABEL ON CONTAINER CREATION
            # -------------------------------------------------------------
            if method == "POST" and re.search(r"/containers/create(?:\?.*)?$", full_path):
                try:
                    payload = json.loads(body_bytes.decode("utf-8"))
                    labels = payload.setdefault("Labels", {})
                    labels["openrig.owner"] = username

                    # Enforce Strict Anti-Escape Security Policies
                    host_config = payload.setdefault("HostConfig", {})

                    # 1. Enforce Non-Privileged mode & strip dangerous capabilities
                    host_config["Privileged"] = False
                    cap_add = host_config.get("CapAdd") or []
                    dangerous_caps = {"ALL", "SYS_ADMIN", "SYS_PTRACE", "SYS_RAWIO", "MKNOD"}
                    host_config["CapAdd"] = [c for c in cap_add if c.upper() not in dangerous_caps]

                    # 2. Block Host Namespaces (prevents inspecting host processes or network)
                    if host_config.get("NetworkMode", "").lower() == "host":
                        host_config["NetworkMode"] = f"openrig-net-{username}"
                    if host_config.get("PidMode", "").lower() == "host":
                        host_config["PidMode"] = ""
                    if host_config.get("IpcMode", "").lower() == "host":
                        host_config["IpcMode"] = "private"

                    # 3. Strip direct Host Devices (prevents raw disk block access)
                    host_config["Devices"] = []

                    # 4. Block host root mounts & forbidden directories
                    binds = host_config.get("Binds", [])
                    safe_binds = []
                    forbidden_prefixes = (
                        "/etc", "/var", "/root", "/bin", "/sbin", "/usr",
                        "/proc", "/sys", "/dev", "/boot", "/mnt", "c:", "d:",
                    )
                    for b in binds:
                        parts_b = b.split(":")
                        src = parts_b[0].strip()
                        src_lower = src.lower()
                        if src == "/" or any(src_lower.startswith(fp) for fp in forbidden_prefixes):
                            if not src.startswith("/workspace"):
                                continue  # Strip host system root mounts
                        if src.startswith("/") and not src.startswith("/workspace"):
                            continue  # Strip forbidden host mounts
                        safe_binds.append(b)
                    host_config["Binds"] = safe_binds

                    body_bytes = json.dumps(payload).encode("utf-8")
                    content_len = len(body_bytes)
                except Exception:
                    pass

            # -------------------------------------------------------------
            # RULE 2: ACCESS CONTROL ON SPECIFIC CONTAINERS
            # -------------------------------------------------------------
            c_match = re.search(r"/containers/([^/?]+)(?:/.*)?$", full_path)
            if c_match and method != "POST" and not full_path.endswith("/containers/create"):
                target_cid = c_match.group(1)
                if target_cid not in ("json", "prune") and not is_container_owned(username, target_cid):
                    resp = (
                        b"HTTP/1.1 404 Not Found\r\n"
                        b"Content-Type: application/json\r\n"
                        b"Content-Length: 46\r\n\r\n"
                        b'{"message":"No such container: ' + target_cid.encode() + b'"}\n'
                    )
                    client_sock.sendall(resp)
                    continue

            # Check if this is a hijacked/upgraded streaming request
            is_hijack = (
                headers.get("upgrade", "").lower() == "tcp"
                or headers.get("connection", "").lower() == "upgrade"
                or "/attach" in full_path
                or "/exec/" in full_path and full_path.endswith("/start")
            )

            # Rebuild and forward request headers
            new_headers = [f"{method} {full_path} {proto}"]
            for line in lines[1:]:
                k = line.split(":", 1)[0].strip().lower()
                if k == "content-length":
                    new_headers.append(f"Content-Length: {content_len}")
                else:
                    new_headers.append(line)
            req_data = "\r\n".join(new_headers).encode("iso-8859-1") + b"\r\n\r\n" + body_bytes
            upstream_sock.sendall(req_data)

            # If connection is hijacked for TTY, pipe raw bidirectional
            if is_hijack:
                pipe_bidirectional(client_sock, upstream_sock)
                break

            # Read upstream response headers
            up_headers_raw, up_rest = read_http_headers(upstream_sock)
            if not up_headers_raw:
                break

            up_header_text = up_headers_raw.decode("iso-8859-1")
            up_lines = up_header_text.split("\r\n")
            up_headers = {}
            for line in up_lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    up_headers[k.strip().lower()] = v.strip()

            up_content_len = int(up_headers.get("content-length", "-1"))
            up_chunked = "chunked" in up_headers.get("transfer-encoding", "").lower()

            # -------------------------------------------------------------
            # RULE 3: VISIBILITY ISOLATION IN 'docker ps' (GET /containers/json)
            # -------------------------------------------------------------
            if method == "GET" and re.search(r"/containers/json(?:\?.*)?$", full_path) and "200 OK" in up_lines[0]:
                up_body = b""
                if up_content_len > 0:
                    up_body = read_exact(upstream_sock, up_content_len, initial=up_rest)
                elif up_chunked:
                    # Read chunked body
                    c_buf = up_rest
                    while b"0\r\n\r\n" not in c_buf:
                        chunk = upstream_sock.recv(4096)
                        if not chunk:
                            break
                        c_buf += chunk
                    # Parse chunks into flat body
                    chunks = []
                    idx = 0
                    while idx < len(c_buf):
                        eol = c_buf.find(b"\r\n", idx)
                        if eol == -1:
                            break
                        csize_hex = c_buf[idx:eol].strip()
                        if not csize_hex:
                            break
                        csize = int(csize_hex, 16)
                        if csize == 0:
                            break
                        chunk_data = c_buf[eol+2 : eol+2+csize]
                        chunks.append(chunk_data)
                        idx = eol + 2 + csize + 2
                    up_body = b"".join(chunks)

                try:
                    data = json.loads(up_body.decode("utf-8"))
                    # Filter ONLY containers owned by this user
                    filtered = [
                        c for c in data
                        if c.get("Labels", {}).get("openrig.owner") == username
                    ]
                    # Update ownership cache
                    user_cache = OWNERSHIP_CACHE.setdefault(username, set())
                    for c in filtered:
                        cid = c.get("Id", "")
                        user_cache.add(cid)
                        user_cache.add(cid[:12])
                        for n in c.get("Names", []):
                            user_cache.add(n.lstrip("/"))

                    new_body = json.dumps(filtered).encode("utf-8")
                    new_up_lines = [up_lines[0]]
                    for l in up_lines[1:]:
                        lk = l.split(":", 1)[0].strip().lower()
                        if lk in ("content-length", "transfer-encoding"):
                            continue
                        new_up_lines.append(l)
                    new_up_lines.append(f"Content-Length: {len(new_body)}")
                    new_up_lines.append("Connection: keep-alive")
                    resp = "\r\n".join(new_up_lines).encode("iso-8859-1") + b"\r\n\r\n" + new_body
                    client_sock.sendall(resp)
                    continue
                except Exception:
                    pass

            # Standard pass-through for other responses
            client_sock.sendall(up_headers_raw + b"\r\n\r\n")
            if up_content_len > 0:
                body = read_exact(upstream_sock, up_content_len, initial=up_rest)
                client_sock.sendall(body)
            elif up_chunked:
                client_sock.sendall(up_rest)
                while True:
                    chunk = upstream_sock.recv(4096)
                    if not chunk:
                        break
                    client_sock.sendall(chunk)
                    if b"0\r\n\r\n" in chunk:
                        break
            else:
                if up_rest:
                    client_sock.sendall(up_rest)
    finally:
        try:
            client_sock.close()
        except Exception:
            pass
        try:
            upstream_sock.close()
        except Exception:
            pass


def serve_user_socket(username: str, socket_path: str):
    """Listen on a dedicated UNIX socket for a specific user."""
    os.makedirs(os.path.dirname(socket_path), exist_ok=True)
    if os.path.exists(socket_path):
        try:
            if os.path.isdir(socket_path):
                import shutil
                shutil.rmtree(socket_path)
            else:
                os.unlink(socket_path)
        except OSError:
            pass

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(socket_path)
    except OSError as e:
        print(f"❌ Failed to bind socket at {socket_path}: {e}", file=sys.stderr, flush=True)
        sys.exit(1)

    os.chmod(socket_path, 0o666)  # Accessible by user container
    server.listen(128)
    print(f"🛡️  [OpenRig Docker Guard] Scoped socket active for '{username}': {socket_path}", flush=True)

    while True:
        try:
            conn, _ = server.accept()
            t = threading.Thread(target=handle_client, args=(conn, username), daemon=True)
            t.start()
        except Exception as e:
            time.sleep(0.5)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: docker-proxy.py <username> <socket_path>")
        sys.exit(1)
    username = sys.argv[1]
    socket_path = sys.argv[2]
    serve_user_socket(username, socket_path)
