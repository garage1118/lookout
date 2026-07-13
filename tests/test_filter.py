from typing import Any

from lookout.config import Settings
from lookout.core.filter import _parse_own_container_id, apply
from lookout.docker.container import ENABLE_LABEL, Container


def make_container(
    name: str, labels: dict[str, str] | None = None, container_id: str | None = None
) -> Container:
    return Container(
        id=container_id or f"id-{name}",
        name=name,
        image_id="sha256:x",
        image_name="myapp:latest",
        labels=labels or {},
        inspect={},
    )


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_default_includes_everything_not_explicitly_disabled() -> None:
    containers = [make_container("a"), make_container("b", {ENABLE_LABEL: "false"})]

    result = apply(containers, settings())

    assert [c.name for c in result] == ["a"]


def test_label_enable_scope_requires_explicit_true() -> None:
    containers = [
        make_container("no-label"),
        make_container("enabled", {ENABLE_LABEL: "true"}),
        make_container("disabled", {ENABLE_LABEL: "false"}),
    ]

    result = apply(containers, settings(label_enable=True))

    assert [c.name for c in result] == ["enabled"]


def test_include_names_restricts_to_named_containers() -> None:
    containers = [make_container("a"), make_container("b"), make_container("c")]

    result = apply(containers, settings(include_names=["a", "c"]))

    assert [c.name for c in result] == ["a", "c"]


def test_exclude_names_removes_named_containers() -> None:
    containers = [make_container("a"), make_container("b"), make_container("c")]

    result = apply(containers, settings(exclude_names=["b"]))

    assert [c.name for c in result] == ["a", "c"]


def test_exclude_wins_over_include() -> None:
    containers = [make_container("a"), make_container("b")]

    result = apply(containers, settings(include_names=["a", "b"], exclude_names=["b"]))

    assert [c.name for c in result] == ["a"]


def test_self_exemption_excludes_own_container() -> None:
    own_id = "abc123def456" + "0" * 52  # a full 64-char id, like a real container id
    containers = [make_container("lookout", container_id=own_id), make_container("web")]

    result = apply(containers, settings(), own_container_id="abc123def456")

    assert [c.name for c in result] == ["web"]


def test_self_exemption_not_overridable_by_include() -> None:
    own_id = "abc123def456" + "0" * 52
    containers = [make_container("lookout", container_id=own_id)]

    result = apply(containers, settings(include_names=["lookout"]), own_container_id="abc123def456")

    assert result == []


def test_self_exemption_noop_without_a_known_own_container_id(monkeypatch: Any) -> None:
    monkeypatch.delenv("HOSTNAME", raising=False)
    containers = [make_container("lookout"), make_container("web")]

    result = apply(containers, settings(), own_container_id=None)

    assert {c.name for c in result} == {"lookout", "web"}


def test_parse_own_container_id_from_real_mountinfo_line() -> None:
    # Captured live from a real container - the id must not depend on the
    # container's hostname, which is a separate, independently-overridable
    # field elsewhere in the container's config.
    container_id = "18d877d63c308db9c9602bf1bfb49302a468548befc120382d2d6f19d96f9927"
    mountinfo = (
        "12459 12450 259:2 /var/lib/docker/containers/"
        f"{container_id}/resolv.conf /etc/resolv.conf rw,relatime - ext4 /dev/nvme1n1p2 rw\n"
        "12460 12450 259:2 /var/lib/docker/containers/"
        f"{container_id}/hostname /etc/hostname rw,relatime - ext4 /dev/nvme1n1p2 rw\n"
        "12461 12450 259:2 /var/lib/docker/containers/"
        f"{container_id}/hosts /etc/hosts rw,relatime - ext4 /dev/nvme1n1p2 rw\n"
    )

    assert _parse_own_container_id(mountinfo) == container_id


def test_parse_own_container_id_returns_none_when_not_found() -> None:
    assert _parse_own_container_id("some unrelated mountinfo content\n") is None
