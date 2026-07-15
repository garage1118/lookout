"""Registry manifest digest lookup.

Handles the two auth shapes registries actually use in practice:
- Docker Hub-style token exchange: unauthenticated request to /v2/ returns a
  401 with a Bearer challenge naming a realm to fetch a short-lived token
  from (auth.docker.io). GHCR and ECR advertise the same Bearer challenge
  shape, just with their own realm — so one code path covers both.
- Basic-auth-only private registries: no Bearer challenge at all; credentials
  (if any) go straight on the manifest request.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from lookout.registry.auth import RegistryAuth

DEFAULT_REGISTRY = "registry-1.docker.io"
_DOCKER_HUB_ALIASES = {"docker.io", "index.docker.io"}

_MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)


@dataclass
class ImageRef:
    registry: str
    repository: str
    reference: str  # a tag, or "sha256:..." when pinned
    pinned: bool


@dataclass
class _AuthMethod:
    """Cached shape of a registry's auth requirement, keyed by registry host
    in RegistryClient.get_latest_digest's `cache` param. Anonymous and
    Basic-auth-only registries both have bearer_realm=None; distinguished by
    `anonymous` since a Basic-only registry still needs credentials sent on
    the manifest request itself, just not via a token exchange."""

    anonymous: bool
    bearer_realm: str | None = None
    bearer_params: dict[str, str] = field(default_factory=dict)


AuthCache = dict[str, _AuthMethod]
"""Opaque cache type for RegistryClient.get_latest_digest — construct one
per run (not shared across runs) with `{}` and pass the same dict to every
call within that run, so each registry's auth challenge is only probed once
no matter how many images on it are checked."""


def is_pinned(image: str) -> bool:
    return "@sha256:" in image


def parse_image(image: str) -> ImageRef:
    """Split "image[:tag][@digest]" into registry/repository/reference,
    mirroring Docker's own resolution rules: no registry prefix means
    Docker Hub, and a single-segment repository is implicitly under
    "library/" there.
    """
    name = image
    digest = None
    if "@" in name:
        name, digest = name.split("@", 1)

    tag = "latest"
    last_slash = name.rfind("/")
    last_colon = name.rfind(":")
    if last_colon > last_slash:
        name, tag = name[:last_colon], name[last_colon + 1 :]

    parts = name.split("/", 1)
    looks_like_host = len(parts) == 2 and (
        "." in parts[0] or ":" in parts[0] or parts[0] == "localhost"
    )
    if looks_like_host:
        registry, repository = parts
    else:
        registry, repository = DEFAULT_REGISTRY, name
        if "/" not in repository:
            repository = f"library/{repository}"

    if registry in _DOCKER_HUB_ALIASES:
        registry = DEFAULT_REGISTRY

    if digest:
        return ImageRef(registry=registry, repository=repository, reference=digest, pinned=True)
    return ImageRef(registry=registry, repository=repository, reference=tag, pinned=False)


class RegistryClient:
    """One `httpx.Client` is opened here and reused for the lifetime of this
    `RegistryClient` instance (constructed once in cli.py and reused across
    every poll in daemon mode) rather than a fresh one per
    get_latest_digest() call — httpx.Client's connection pool means N images
    on the same registry host now share connections/TLS sessions instead of
    each paying a fresh handshake, the same reuse the per-run AuthCache
    already gets for the auth challenge itself."""

    def __init__(self, timeout: float = 10.0, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(timeout=timeout, transport=transport)

    def get_latest_digest(
        self, image: str, auth: RegistryAuth | None, cache: AuthCache | None = None
    ) -> str:
        if is_pinned(image):
            raise ValueError(f"{image} is pinned to a digest; nothing to check")

        ref = parse_image(image)
        client = self._client
        headers = {"Accept": _MANIFEST_ACCEPT}
        token = self._authenticate(client, ref, auth, cache)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif auth and auth.username:
            headers["Authorization"] = _basic_auth_header(auth)

        url = f"https://{ref.registry}/v2/{ref.repository}/manifests/{ref.reference}"
        response = client.head(url, headers=headers)
        if response.status_code == 405 or "docker-content-digest" not in response.headers:
            response = client.get(url, headers=headers)
        response.raise_for_status()

        digest = response.headers.get("docker-content-digest")
        if not digest:
            raise RuntimeError(f"registry did not return a content digest for {image}")
        return str(digest)

    def _authenticate(
        self,
        client: httpx.Client,
        ref: ImageRef,
        auth: RegistryAuth | None,
        cache: AuthCache | None,
    ) -> str | None:
        """Exchange a Bearer challenge for a token. Returns None for
        anonymous-access and basic-auth-only registries — the caller sends
        Basic auth directly in that case.

        The auth *method* (anonymous / bearer-with-realm / other) is cached
        per registry host when `cache` is given, so checking N images on the
        same registry only probes /v2/ once instead of N times. The token
        exchange itself is never cached — it's scoped per-repository and
        short-lived, so there's nothing to reuse there even within one run.
        """
        method = cache.get(ref.registry) if cache is not None else None
        if method is None:
            method = self._discover(client, ref.registry)
            if cache is not None:
                cache[ref.registry] = method

        if method.anonymous or method.bearer_realm is None:
            return None

        params = dict(method.bearer_params)
        params["scope"] = f"repository:{ref.repository}:pull"
        request_auth = (auth.username, auth.password or "") if auth and auth.username else None
        token_response = client.get(method.bearer_realm, params=params, auth=request_auth)
        token_response.raise_for_status()
        data = token_response.json()
        token = data.get("token") or data.get("access_token")
        return str(token) if token else None

    def _discover(self, client: httpx.Client, registry: str) -> _AuthMethod:
        probe = client.get(f"https://{registry}/v2/")
        if probe.status_code == 200:
            return _AuthMethod(anonymous=True)

        challenge = probe.headers.get("www-authenticate", "")
        if not challenge.lower().startswith("bearer"):
            return _AuthMethod(anonymous=False)

        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        realm = params.pop("realm", None)
        if not realm:
            return _AuthMethod(anonymous=False)
        # The root /v2/ probe isn't repository-scoped, so registries (GHCR
        # included) hand back a placeholder scope here — always recomputed
        # per-image in _authenticate, never trusted from the probe.
        params.pop("scope", None)
        return _AuthMethod(anonymous=False, bearer_realm=realm, bearer_params=params)


def _basic_auth_header(auth: RegistryAuth) -> str:
    raw = f"{auth.username}:{auth.password or ''}".encode()
    return f"Basic {base64.b64encode(raw).decode()}"
