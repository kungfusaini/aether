#!/usr/bin/env python3
"""One-time, localhost-only bootstrap. Never prints passwords, JWTs, or API keys."""
import json
import os
from pathlib import Path
import secrets
import urllib.request

BASE = "http://127.0.0.1:19000/api"
SECRET_DIR = Path("/home/babbi-apps/platform/.secrets")


def request(path, method="GET", data=None, jwt=None, multipart=False):
    headers = {}
    if jwt:
        headers["Authorization"] = "Bearer " + jwt
    body = None
    if multipart:
        boundary = "babbi-" + secrets.token_hex(16)
        body = "".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in data.items())
        body = (body + f"--{boundary}--\r\n").encode()
        headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run through the Aether administrator connection.")
    password = (SECRET_DIR / "portainer-admin-password").read_text().strip()
    jwt = request("/auth", "POST", {"Username": "admin", "Password": password})["jwt"]
    endpoints = request("/endpoints", jwt=jwt)
    if not endpoints:
        endpoint = request("/endpoints", "POST", {"Name": "Babbi rootless apps", "EndpointCreationType": "1", "URL": "unix:///var/run/docker.sock"}, jwt=jwt, multipart=True)
    else:
        if len(endpoints) != 1:
            raise SystemExit("Unexpected additional environment; refusing to bootstrap.")
        endpoint = endpoints[0]
    info = request(f'/endpoints/{endpoint["Id"]}/docker/info', jwt=jwt)
    if "name=rootless" not in info.get("SecurityOptions", []):
        raise SystemExit("Environment is not rootless; refusing to create deploy credentials.")
    print("Verified rootless environment:", endpoint["Name"], "ID", endpoint["Id"])
    token_path = SECRET_DIR / "portainer-deploy-token"
    if not token_path.exists():
        users = request("/users", jwt=jwt)
        user_id = next(u["Id"] for u in users if u["Username"] == "admin")
        key = request(f"/users/{user_id}/tokens", "POST", {"description": "Babbi workspace deployment", "password": password}, jwt=jwt)["rawAPIKey"]
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key)
        os.chown(token_path, 2000, 2000)
        print("Created private deployment-token file (value not printed).")
    print("Bootstrap complete. Human password handoff/rotation is still required.")


if __name__ == "__main__":
    main()
