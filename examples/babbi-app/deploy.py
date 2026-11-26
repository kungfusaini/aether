#!/usr/bin/env python3
"""Deploy a prebuilt image through her rootless Portainer, not host SSH/Docker.

Only stdlib is needed. PORTAINER_TOKEN and optional REGISTRY_TOKEN are environment
variables; neither is printed or passed in command-line arguments.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the management token to another origin.


class Portainer:
    def __init__(self, base, token):
        url = urllib.parse.urlsplit(base)
        if url.scheme != "https" and not (url.scheme == "http" and url.hostname == "127.0.0.1"):
            raise ValueError("HTTPS required except for administrator localhost tests")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Invalid dashboard URL")
        self.base = base.rstrip("/") + "/api"
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, path, method="GET", data=None, extra_headers=None, stream=False):
        headers = {"X-API-Key": self.token}
        headers.update(extra_headers or {})
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=180) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            # Response bodies can include Compose env values. Do not log them.
            raise RuntimeError(f"Portainer {method} {path.split('?')[0]} failed: HTTP {error.code}") from None
        if stream:
            for line in raw.splitlines():
                if line and json.loads(line).get("error"):
                    raise RuntimeError("Registry pull failed; check package access")
            return None
        return json.loads(raw) if raw else None


def deploy(api, name, image, compose_file, port=3000):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name) or name in {"apps", "www", "mail", "demo", "babbi-platform"}:
        raise ValueError("Choose an app name, not an infrastructure hostname")
    if not 1 <= port <= 65535:
        raise ValueError("Invalid internal application port")
    endpoints = api.request("/endpoints")
    if len(endpoints) != 1:
        raise RuntimeError("Expected exactly her one rootless environment")
    endpoint = endpoints[0]["Id"]
    docker = f"/endpoints/{endpoint}/docker"
    info = api.request(docker + "/info")
    if "name=rootless" not in info.get("SecurityOptions", []):
        raise RuntimeError("Refusing to deploy to a non-rootless engine")
    auth = {}
    registry_token = os.environ.get("REGISTRY_TOKEN")
    if registry_token:
        if not image.startswith("ghcr.io/"):
            raise ValueError("GitHub registry credentials may only be used for ghcr.io images")
        encoded = base64.b64encode(json.dumps({"username": os.environ["REGISTRY_USER"], "password": registry_token, "serveraddress": "ghcr.io"}).encode()).decode()
        auth["X-Registry-Auth"] = encoded
    api.request(docker + "/images/create?" + urllib.parse.urlencode({"fromImage": image}), "POST", extra_headers=auth, stream=True)
    info = api.request(docker + "/images/" + urllib.parse.quote(image, safe="/:@") + "/json")
    digests = info.get("RepoDigests", [])
    if not digests:
        raise RuntimeError("Pulled image has no immutable digest")
    tagged = image.split("@", 1)[0]
    image_repo = tagged.rsplit(":", 1)[0] if ":" in tagged.rsplit("/", 1)[-1] else tagged
    digest = next((d for d in digests if d.startswith(image_repo + "@sha256:")), None)
    if not digest:
        raise RuntimeError("Image digest does not match the requested package")
    stacks = api.request("/stacks")
    existing = next((s for s in stacks if s["Name"] == name and s["EndpointId"] == endpoint), None)
    # Preserve app-specific values entered in the dashboard, including DB secrets.
    env = {item["name"]: item["value"] for item in ((existing or {}).get("Env") or [])}
    env.update(APP_IMAGE=digest, APP_NAME=name, APP_HOST=name + ".babbi.world", APP_PORT=str(port))
    payload = {"StackFileContent": Path(compose_file).read_text(), "Env": [{"name": k, "value": v} for k, v in env.items()]}
    if existing:
        payload.update(Prune=False, PullImage=False)
        api.request(f'/stacks/{existing["Id"]}?endpointId={endpoint}', "PUT", payload)
    else:
        payload.update(Name=name)
        api.request(f"/stacks/create/standalone/string?endpointId={endpoint}", "POST", payload)
    filters = urllib.parse.urlencode({"filters": json.dumps({"label": [f"com.docker.compose.project={name}", "com.docker.compose.service=web"]})})
    for attempt in range(30):
        containers = api.request(docker + "/containers/json?" + filters)
        if any(c.get("ImageID") == info["Id"] and c.get("State") == "running" for c in containers):
            print(f"Deployed {name}.babbi.world ({digest}); matching image is running.")
            return
        time.sleep(2)
    raise RuntimeError("Matching web image did not become running; inspect the stack in the dashboard")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--compose", default="compose.yml")
    parser.add_argument("--port", type=int, default=3000)
    args = parser.parse_args()
    deploy(Portainer(os.environ.get("PORTAINER_URL", "https://apps.babbi.world"), os.environ["PORTAINER_TOKEN"]), args.name, args.image, args.compose, args.port)
