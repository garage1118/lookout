from collections.abc import Callable

import httpx
import pytest

from lookout.registry.digest import (
    DEFAULT_REGISTRY,
    AuthCache,
    RegistryClient,
    is_pinned,
    parse_image,
)

_DIGEST = "sha256:" + "a" * 64


@pytest.mark.parametrize(
    ("image", "registry", "repository", "reference"),
    [
        ("alpine", DEFAULT_REGISTRY, "library/alpine", "latest"),
        ("alpine:3.20", DEFAULT_REGISTRY, "library/alpine", "3.20"),
        ("myuser/myapp:latest", DEFAULT_REGISTRY, "myuser/myapp", "latest"),
        ("ghcr.io/owner/repo:v1", "ghcr.io", "owner/repo", "v1"),
        ("localhost:5000/myapp:latest", "localhost:5000", "myapp", "latest"),
        ("registry.example.com/team/app", "registry.example.com", "team/app", "latest"),
        ("docker.io/library/nginx", DEFAULT_REGISTRY, "library/nginx", "latest"),
    ],
)
def test_parse_image(image: str, registry: str, repository: str, reference: str) -> None:
    ref = parse_image(image)
    assert ref.registry == registry
    assert ref.repository == repository
    assert ref.reference == reference
    assert ref.pinned is False


def test_parse_image_pinned_by_digest() -> None:
    ref = parse_image(
        "alpine@sha256:1234567890123456789012345678901234567890123456789012345678901234"
    )
    assert ref.pinned is True
    assert ref.reference.startswith("sha256:")


def test_is_pinned() -> None:
    assert is_pinned("alpine@sha256:" + "a" * 64) is True
    assert is_pinned("alpine:latest") is False


def _bearer_registry_handler(
    probe_calls: list[str], token_calls: list[str | None]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path
        if path == "/v2/":
            probe_calls.append(host)
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": f'Bearer realm="https://{host}/token",service="{host}"'
                },
            )
        if path == "/token":
            token_calls.append(request.url.params.get("scope"))
            return httpx.Response(200, json={"token": "fake-token"})
        if "/manifests/" in path:
            return httpx.Response(200, headers={"Docker-Content-Digest": _DIGEST}, json={})
        return httpx.Response(404)

    return handler


def test_cache_probes_registry_once_across_multiple_images() -> None:
    probe_calls: list[str] = []
    token_calls: list[str | None] = []
    transport = httpx.MockTransport(_bearer_registry_handler(probe_calls, token_calls))
    client = RegistryClient(transport=transport)
    cache: AuthCache = {}

    digest_a = client.get_latest_digest(
        "registry.example.com/team/app-a:latest", None, cache=cache
    )
    digest_b = client.get_latest_digest(
        "registry.example.com/team/app-b:latest", None, cache=cache
    )

    assert digest_a == _DIGEST
    assert digest_b == _DIGEST
    assert probe_calls == ["registry.example.com"]  # only probed once
    # each image still gets its own correctly-scoped token, though
    assert token_calls == ["repository:team/app-a:pull", "repository:team/app-b:pull"]


def test_without_cache_each_call_probes_independently() -> None:
    probe_calls: list[str] = []
    token_calls: list[str | None] = []
    transport = httpx.MockTransport(_bearer_registry_handler(probe_calls, token_calls))
    client = RegistryClient(transport=transport)

    client.get_latest_digest("registry.example.com/team/app-a:latest", None)
    client.get_latest_digest("registry.example.com/team/app-b:latest", None)

    assert probe_calls == ["registry.example.com", "registry.example.com"]


def test_cache_is_scoped_per_registry_not_global() -> None:
    probe_calls: list[str] = []
    token_calls: list[str | None] = []
    transport = httpx.MockTransport(_bearer_registry_handler(probe_calls, token_calls))
    client = RegistryClient(transport=transport)
    cache: AuthCache = {}

    client.get_latest_digest("registry-a.example.com/app:latest", None, cache=cache)
    client.get_latest_digest("registry-b.example.com/app:latest", None, cache=cache)

    assert probe_calls == ["registry-a.example.com", "registry-b.example.com"]


def test_cache_works_for_anonymous_registry() -> None:
    probe_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/":
            probe_calls.append(request.url.host)
            return httpx.Response(200)
        if "/manifests/" in request.url.path:
            return httpx.Response(200, headers={"Docker-Content-Digest": _DIGEST})
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = RegistryClient(transport=transport)
    cache: AuthCache = {}

    d1 = client.get_latest_digest("registry.example.com/app-a:latest", None, cache=cache)
    d2 = client.get_latest_digest("registry.example.com/app-b:latest", None, cache=cache)

    assert d1 == d2 == _DIGEST
    assert probe_calls == ["registry.example.com"]
