from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path

from lookout.registry.digest import parse_image

_DOCKER_HUB_CONFIG_KEYS = ("registry-1.docker.io", "https://index.docker.io/v1/", "docker.io")


@dataclass
class RegistryAuth:
    username: str | None = None
    password: str | None = None
    token: str | None = None


def resolve_auth(
    image: str,
    docker_config_path: str | None = None,
    fallback: RegistryAuth | None = None,
    fallback_registry: str | None = None,
) -> RegistryAuth | None:
    """Resolve basic-auth credentials for `image`'s registry from a Docker
    CLI config.json's plain "auths" section (i.e. what `docker login` writes).

    Does not shell out to credential helpers (credHelpers/credsStore) — if
    the only stored credential for a registry lives behind one of those,
    this falls through to `fallback` same as if config.json had nothing at
    all for that registry.

    `fallback` (from LOOKOUT_REGISTRY_USERNAME/PASSWORD) is only used when
    `image`'s registry matches `fallback_registry` (LOOKOUT_REGISTRY_HOST).
    Without that scoping check, these credentials would be sent to *every*
    registry lacking a config.json entry — including public ones like Docker
    Hub, whose token endpoint rejects a bad Basic-auth attempt outright
    (unlike an anonymous request, which succeeds for public images). This
    was a real regression caught live: registry.3digital.com started
    working, but Docker Hub pulls broke in the same run.
    """
    found = _from_config(image, docker_config_path)
    if found is not None:
        return found
    if fallback is not None and fallback_registry is not None:
        if parse_image(image).registry == fallback_registry:
            return fallback
    return None


def _from_config(image: str, docker_config_path: str | None) -> RegistryAuth | None:
    path = _config_path(docker_config_path)
    if not path.is_file():
        return None

    config = json.loads(path.read_text())
    auths = config.get("auths") or {}
    if not auths:
        return None

    registry = parse_image(image).registry
    entry = auths.get(registry)
    if entry is None and registry in _DOCKER_HUB_CONFIG_KEYS:
        entry = next((auths[key] for key in _DOCKER_HUB_CONFIG_KEYS if key in auths), None)
    if not entry:
        return None

    raw_auth = entry.get("auth")
    if not raw_auth:
        return None

    decoded = base64.b64decode(raw_auth).decode()
    username, _, password = decoded.partition(":")
    return RegistryAuth(username=username, password=password)


def _config_path(docker_config_path: str | None) -> Path:
    if docker_config_path:
        return Path(docker_config_path)
    env_dir = os.environ.get("DOCKER_CONFIG")
    if env_dir:
        return Path(env_dir) / "config.json"
    return Path("~/.docker/config.json").expanduser()
