from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from lookout.registry.auth import RegistryAuth, resolve_auth


def write_config(path: Path, auths: dict[str, dict[str, str]]) -> None:
    path.write_text(json.dumps({"auths": auths}))


def basic_auth(username: str, password: str) -> str:
    return base64.b64encode(f"{username}:{password}".encode()).decode()


def test_resolves_credentials_from_config_json(tmp_path: Any) -> None:
    config_path = tmp_path / "config.json"
    write_config(
        config_path, {"registry.example.com": {"auth": basic_auth("alice", "hunter2")}}
    )

    auth = resolve_auth("registry.example.com/myapp:latest", docker_config_path=str(config_path))

    assert auth == RegistryAuth(username="alice", password="hunter2")


def test_resolves_credentials_from_scheme_prefixed_config_key(tmp_path: Any) -> None:
    # Regression test: docker login itself always writes a bare host, but
    # some other tools (Kubernetes imagePullSecrets, some CI credential
    # helpers) write a scheme-prefixed key for a non-Hub registry -- this
    # used to silently fall through to anonymous.
    config_path = tmp_path / "config.json"
    write_config(
        config_path,
        {"https://registry.example.com": {"auth": basic_auth("alice", "hunter2")}},
    )

    auth = resolve_auth("registry.example.com/myapp:latest", docker_config_path=str(config_path))

    assert auth == RegistryAuth(username="alice", password="hunter2")


def test_no_config_file_and_no_fallback_returns_none(tmp_path: Any) -> None:
    missing = tmp_path / "does-not-exist.json"

    auth = resolve_auth("registry.example.com/myapp:latest", docker_config_path=str(missing))

    assert auth is None


def test_fallback_used_when_config_has_no_matching_entry_and_registry_matches(
    tmp_path: Any,
) -> None:
    config_path = tmp_path / "config.json"
    write_config(config_path, {"some-other-registry.com": {"auth": basic_auth("x", "y")}})
    fallback = RegistryAuth(username="fallback-user", password="fallback-pass")

    auth = resolve_auth(
        "registry.example.com/myapp:latest",
        docker_config_path=str(config_path),
        fallback=fallback,
        fallback_registry="registry.example.com",
    )

    assert auth is fallback


def test_fallback_used_when_no_config_file_at_all_and_registry_matches(tmp_path: Any) -> None:
    missing = tmp_path / "does-not-exist.json"
    fallback = RegistryAuth(username="fallback-user", password="fallback-pass")

    auth = resolve_auth(
        "registry.example.com/myapp:latest",
        docker_config_path=str(missing),
        fallback=fallback,
        fallback_registry="registry.example.com",
    )

    assert auth is fallback


def test_config_json_entry_wins_over_fallback(tmp_path: Any) -> None:
    config_path = tmp_path / "config.json"
    write_config(
        config_path, {"registry.example.com": {"auth": basic_auth("config-user", "config-pass")}}
    )
    fallback = RegistryAuth(username="fallback-user", password="fallback-pass")

    auth = resolve_auth(
        "registry.example.com/myapp:latest",
        docker_config_path=str(config_path),
        fallback=fallback,
        fallback_registry="registry.example.com",
    )

    assert auth == RegistryAuth(username="config-user", password="config-pass")


def test_fallback_not_used_for_a_different_registry(tmp_path: Any) -> None:
    # This is the exact regression caught live: fallback credentials scoped
    # to one private registry must not be sent to an unrelated registry
    # (e.g. Docker Hub) just because that registry also has no config.json
    # entry - Docker Hub's token endpoint rejects a bad Basic-auth attempt
    # outright, breaking what would otherwise be anonymous access.
    missing = tmp_path / "does-not-exist.json"
    fallback = RegistryAuth(username="fallback-user", password="fallback-pass")

    auth = resolve_auth(
        "louislam/uptime-kuma:latest",
        docker_config_path=str(missing),
        fallback=fallback,
        fallback_registry="registry.example.com",
    )

    assert auth is None


def test_fallback_not_used_when_no_fallback_registry_given(tmp_path: Any) -> None:
    missing = tmp_path / "does-not-exist.json"
    fallback = RegistryAuth(username="fallback-user", password="fallback-pass")

    auth = resolve_auth(
        "registry.example.com/myapp:latest", docker_config_path=str(missing), fallback=fallback
    )

    assert auth is None
