from __future__ import annotations

from lookout.config import Settings
from lookout.core.updater import run, stop_order
from lookout.docker.container import DEPENDS_ON_LABEL, MONITOR_ONLY_LABEL, Container


def make_container(
    name: str,
    *,
    labels: dict[str, str] | None = None,
    repo_digests: list[str] | None = None,
    image_name: str = "myapp:latest",
) -> Container:
    return Container(
        id=f"id-{name}",
        name=name,
        image_id=f"sha256:{name}-old",
        image_name=image_name,
        labels=labels or {},
        inspect={},
        repo_digests=repo_digests or [],
    )


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class FakeDockerClient:
    def __init__(self, containers: list[Container]) -> None:
        self._containers = list(containers)
        self.calls: list[str] = []
        self.stop_fail: set[str] = set()
        self.removed_images: list[str] = []
        self.found_image_id: str | None = None

    def list_containers(self) -> list[Container]:
        return list(self._containers)

    def pull_image(self, image: str) -> str:
        self.calls.append(f"pull:{image}")
        return "sha256:pulled"

    def get_image_id(self, image_name: str) -> str:
        self.calls.append(f"get_image_id:{image_name}")
        return "sha256:local"

    def find_local_image_id(self, image_name: str, digest: str) -> str | None:
        self.calls.append(f"find_local_image_id:{image_name}:{digest}")
        return self.found_image_id

    def stop(self, container: Container, timeout: int) -> None:
        if container.name in self.stop_fail:
            raise RuntimeError(f"stop failed: {container.name}")
        self.calls.append(f"stop:{container.name}")

    def rename(self, container: Container, new_name: str) -> None:
        raise NotImplementedError

    def recreate(self, container: Container, new_image_id: str) -> Container:
        self.calls.append(f"recreate:{container.name}:{new_image_id}")
        return Container(
            id=f"new-{container.name}",
            name=container.name,
            image_id=new_image_id,
            image_name=container.image_name,
            labels=container.labels,
            inspect={},
        )

    def start(self, container: Container) -> None:
        self.calls.append(f"start:{container.name}")

    def remove_image(self, image_id: str) -> None:
        self.removed_images.append(image_id)

    def exec_run(self, container: Container, command: list[str]) -> tuple[int, bytes]:
        self.calls.append(f"exec:{container.name}")
        return 0, b""


class FakeRegistryClient:
    def __init__(self, digests: dict[str, str], errors: set[str] | None = None) -> None:
        self.digests = digests
        self.errors = errors or set()

    def get_latest_digest(self, image: str, auth: object, cache: object = None) -> str:
        if image in self.errors:
            raise RuntimeError(f"registry unreachable: {image}")
        return self.digests[image]


def test_stop_order_puts_dependents_before_dependencies() -> None:
    db = make_container("db")
    web = make_container("web", labels={DEPENDS_ON_LABEL: "db"})
    cache = make_container("cache")

    order = stop_order([db, web, cache])

    names = [c.name for c in order]
    assert set(names) == {"db", "web", "cache"}
    assert names.index("web") < names.index("db")


def test_run_updates_a_stale_container() -> None:
    container = make_container("web", repo_digests=["myapp@sha256:old"])
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings())

    assert [c.name for c in session.stale] == ["web"]
    assert [c.name for c in session.updated] == ["web"]
    assert docker_client.calls == [
        "find_local_image_id:myapp:latest:sha256:new",
        "stop:web",
        "pull:myapp:latest",
        "recreate:web:sha256:pulled",
        "start:web",
    ]


def test_run_leaves_fresh_containers_untouched() -> None:
    container = make_container("web", repo_digests=["myapp@sha256:same"])
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:same"})

    session = run(docker_client, registry_client, settings())

    assert session.stale == []
    assert session.updated == []
    assert docker_client.calls == []


def test_run_falls_back_to_local_image_lookup_when_repo_digests_orphaned() -> None:
    # Reproduces a real bug: Docker clears an image's RepoDigests when its
    # tag is locally reassigned to a different image (e.g. by something
    # other than lookout rebuilding/pulling the same tag on this host). The
    # container's own repo_digests no longer proves anything either way —
    # lookout must fall back to asking Docker whether the image it's
    # actually running is the one with the latest digest.
    container = make_container("web", repo_digests=[])  # orphaned, not "never pulled"
    docker_client = FakeDockerClient([container])
    docker_client.found_image_id = "sha256:a-newer-local-image"  # differs from container.image_id
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings())

    assert [c.name for c in session.stale] == ["web"]
    assert [c.name for c in session.updated] == ["web"]
    # The update loop always pulls unless no-pull is set, regardless of how
    # staleness was determined - pulling an already-present digest is just a
    # cheap no-op registry round-trip, not a correctness issue.
    assert docker_client.calls == [
        "find_local_image_id:myapp:latest:sha256:new",
        "stop:web",
        "pull:myapp:latest",
        "recreate:web:sha256:pulled",
        "start:web",
    ]


def test_run_treats_container_as_fresh_when_local_lookup_matches_running_image() -> None:
    container = make_container("web", repo_digests=[])  # orphaned
    docker_client = FakeDockerClient([container])
    docker_client.found_image_id = container.image_id  # exactly what's already running
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings())

    assert session.stale == []
    assert session.updated == []
    assert docker_client.calls == ["find_local_image_id:myapp:latest:sha256:new"]


def test_run_skips_pinned_images() -> None:
    container = make_container(
        "web", image_name="myapp@sha256:" + "a" * 64, repo_digests=["myapp@sha256:" + "a" * 64]
    )
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({})

    session = run(docker_client, registry_client, settings())

    assert [c.name for c in session.skipped] == ["web"]
    assert session.stale == []
    assert docker_client.calls == []


def test_run_records_registry_errors_as_skipped() -> None:
    container = make_container("web", repo_digests=["myapp@sha256:old"])
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({}, errors={"myapp:latest"})

    session = run(docker_client, registry_client, settings())

    assert [c.name for c in session.skipped] == ["web"]
    assert session.stale == []


def test_run_reports_monitor_only_stale_containers_without_updating() -> None:
    container = make_container(
        "web", labels={MONITOR_ONLY_LABEL: "true"}, repo_digests=["myapp@sha256:old"]
    )
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings())

    assert [c.name for c in session.stale] == ["web"]
    assert session.updated == []
    # Staleness is still checked (and thus the fallback lookup still fires)
    # even though monitor-only means nothing gets stopped/recreated.
    assert docker_client.calls == ["find_local_image_id:myapp:latest:sha256:new"]


def test_run_records_stop_failure_and_skips_recreate() -> None:
    container = make_container("web", repo_digests=["myapp@sha256:old"])
    docker_client = FakeDockerClient([container])
    docker_client.stop_fail.add("web")
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings())

    assert len(session.failed) == 1
    assert session.failed[0][0].name == "web"
    assert session.updated == []
    assert not any(c.startswith("recreate:") for c in docker_client.calls)


def test_run_cleanup_removes_superseded_image() -> None:
    container = make_container("web", repo_digests=["myapp@sha256:old"])
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings(cleanup=True))

    assert docker_client.removed_images == [container.image_id]
    assert session.updated
