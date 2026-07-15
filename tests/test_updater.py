from __future__ import annotations

from typing import Any

from lookout.config import Settings
from lookout.core.lifecycle import POST_UPDATE_LABEL, PRE_UPDATE_LABEL
from lookout.core.updater import run, stop_order
from lookout.docker.container import DEPENDS_ON_LABEL, MONITOR_ONLY_LABEL, Container
from lookout.registry.auth import RegistryAuth


def make_container(
    name: str,
    *,
    labels: dict[str, str] | None = None,
    repo_digests: list[str] | None = None,
    image_name: str = "myapp:latest",
    network_mode: str | None = None,
) -> Container:
    inspect: dict[str, object] = {}
    if network_mode is not None:
        inspect["HostConfig"] = {"NetworkMode": network_mode}
    return Container(
        id=f"id-{name}",
        name=name,
        image_id=f"sha256:{name}-old",
        image_name=image_name,
        labels=labels or {},
        inspect=inspect,
        repo_digests=repo_digests or [],
    )


def settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


class FakeDockerClient:
    def __init__(self, containers: list[Container]) -> None:
        self._containers = list(containers)
        self.calls: list[str] = []
        self.stop_fail: set[str] = set()
        self.pull_fail: set[str] = set()
        self.exec_fail: set[str] = set()
        self.find_local_image_id_fail: set[str] = set()
        self.removed_images: list[str] = []
        self.found_image_id: str | None = None
        self.local_image_id = "sha256:local"
        self.pull_auth: dict[str, RegistryAuth | None] = {}

    def list_containers(self) -> list[Container]:
        return list(self._containers)

    def pull_image(self, image: str, auth: RegistryAuth | None = None) -> str:
        if image in self.pull_fail:
            raise RuntimeError(f"pull failed: {image}")
        self.pull_auth[image] = auth
        self.calls.append(f"pull:{image}")
        return "sha256:pulled"

    def get_image_id(self, image_name: str) -> str:
        self.calls.append(f"get_image_id:{image_name}")
        return self.local_image_id

    def find_local_image_id(self, image_name: str, digest: str) -> str | None:
        if image_name in self.find_local_image_id_fail:
            raise RuntimeError(f"docker API error: {image_name}")
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
        if container.name in self.exec_fail:
            raise RuntimeError(f"exec failed: {container.name}")
        self.calls.append(f"exec:{container.name}")
        return 0, b""


class FakeRegistryClient:
    def __init__(self, digests: dict[str, str], errors: set[str] | None = None) -> None:
        self.digests = digests
        self.errors = errors or set()
        self.received_auth: list[object] = []

    def get_latest_digest(self, image: str, auth: object, cache: object = None) -> str:
        self.received_auth.append(auth)
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
    # The replacement image is resolved before anything is stopped -- a slow
    # or failing pull should never happen while the container is down.
    assert docker_client.calls == [
        "find_local_image_id:myapp:latest:sha256:new",
        "pull:myapp:latest",
        "stop:web",
        "recreate:web:sha256:pulled",
    ]


def test_run_leaves_fresh_containers_untouched() -> None:
    container = make_container("web", repo_digests=["myapp@sha256:same"])
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:same"})

    session = run(docker_client, registry_client, settings())

    assert session.stale == []
    assert session.updated == []
    assert docker_client.calls == []


def test_run_no_pull_skips_recreate_when_local_image_already_matches() -> None:
    # Regression test: staleness is judged against the registry digest, but
    # --no-pull recreates onto whatever's cached locally under the tag. If
    # nothing external has pulled a newer image, that's the same image
    # already running, and recreating onto it would restart-loop the
    # container every poll forever with a false "updated" notification. A
    # container with no network-mode dependency in this situation is a pure
    # no-op and is never even stopped (see noop_names in core/updater.run).
    container = make_container("web", repo_digests=["myapp@sha256:old"])
    docker_client = FakeDockerClient([container])
    docker_client.local_image_id = container.image_id
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings(no_pull=True))

    assert [c.name for c in session.stale] == ["web"]
    assert session.updated == []
    assert session.failed == []
    assert docker_client.calls == [
        "find_local_image_id:myapp:latest:sha256:new",
        "get_image_id:myapp:latest",
    ]


def test_run_leaves_noop_stale_container_completely_untouched() -> None:
    # Regression test: a stale container with no network-mode dependency
    # whose resolved image already matches what it's running has nothing to
    # actually do. Before this fix it was still stopped and restarted every
    # single poll forever (with pre/post-update hooks firing each time) even
    # though its container id never changed -- this asserts it's now skipped
    # entirely: no stop, no start, no hooks.
    container = make_container(
        "web",
        repo_digests=["myapp@sha256:old"],
        labels={PRE_UPDATE_LABEL: "true", POST_UPDATE_LABEL: "true"},
    )
    docker_client = FakeDockerClient([container])
    docker_client.local_image_id = container.image_id
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings(no_pull=True))

    assert [c.name for c in session.stale] == ["web"]
    assert session.updated == []
    assert session.failed == []
    assert docker_client.calls == [
        "find_local_image_id:myapp:latest:sha256:new",
        "get_image_id:myapp:latest",
    ]


def test_run_cascades_recreate_to_network_mode_dependents() -> None:
    # Regression test: Docker resolves a container:<name> NetworkMode
    # reference to a concrete id at create() time and never updates it
    # later. If "web" (the target) gets recreated without "sidecar" (which
    # shares its network namespace) also being recreated in the same run,
    # sidecar's reference would permanently point at web's now-dead old id.
    # sidecar's own image is unchanged, so without cascading it would hit
    # the same-image restart-in-place shortcut instead of a real recreate.
    web = make_container("web", image_name="myapp:latest", repo_digests=["myapp@sha256:old"])
    sidecar = make_container(
        "sidecar",
        image_name="other:latest",
        repo_digests=["other@sha256:same"],
        network_mode="container:web",
    )
    docker_client = FakeDockerClient([web, sidecar])
    docker_client.found_image_id = "sha256:web-new"
    docker_client.local_image_id = sidecar.image_id
    registry_client = FakeRegistryClient(
        {"myapp:latest": "sha256:new", "other:latest": "sha256:same"}
    )

    session = run(docker_client, registry_client, settings(no_pull=True))

    assert {c.name for c in session.stale} == {"web", "sidecar"}
    assert {c.name for c in session.updated} == {"web", "sidecar"}
    assert session.failed == []
    assert f"recreate:sidecar:{sidecar.image_id}" in docker_client.calls
    assert "start:sidecar" not in docker_client.calls
    # web (the dependency) stops after sidecar (the dependent) and starts
    # before it, same ordering contract as the depends-on label.
    assert docker_client.calls.index("stop:sidecar") < docker_client.calls.index("stop:web")
    assert docker_client.calls.index(
        f"recreate:web:{docker_client.local_image_id}"
    ) < docker_client.calls.index(f"recreate:sidecar:{sidecar.image_id}")


def test_run_network_mode_cascade_skips_monitor_only_target() -> None:
    # Regression test: a monitor-only target can never actually be recreated
    # by lookout while monitor-only holds, so cascading into its
    # network-mode dependent would be pure waste (a pointless stop/start of
    # the dependent) every single poll, forever, with no benefit -- the
    # dependent's stored network_mode reference is never actually at risk
    # since the target's id never changes.
    web = make_container(
        "web", labels={MONITOR_ONLY_LABEL: "true"}, repo_digests=["myapp@sha256:old"]
    )
    sidecar = make_container(
        "sidecar",
        image_name="other:latest",
        repo_digests=["other@sha256:same"],
        network_mode="container:web",
    )
    docker_client = FakeDockerClient([web, sidecar])
    registry_client = FakeRegistryClient(
        {"myapp:latest": "sha256:new", "other:latest": "sha256:same"}
    )

    session = run(docker_client, registry_client, settings())

    assert [c.name for c in session.stale] == ["web"]
    assert session.updated == []
    assert docker_client.calls == ["find_local_image_id:myapp:latest:sha256:new"]


def test_run_network_mode_dependent_restarts_in_place_when_target_not_actually_recreated() -> None:
    # Regression test: sidecar is still cascaded into session.stale because
    # its network-mode target (web) is stale, but if web's own update turns
    # out to be a same-image restart-in-place (e.g. --no-pull with nothing
    # new pulled locally) rather than a real recreate, web's container id
    # never actually changes -- sidecar's stored network_mode reference is
    # still perfectly valid, so forcing a full recreate of sidecar too would
    # just be a wasted stop/create/start every poll for no reason. Both
    # containers share one image id here specifically so the fake's
    # single-valued get_image_id() can make *both* look unchanged at once.
    shared_image_id = "sha256:shared-old"
    web = Container(
        id="id-web",
        name="web",
        image_id=shared_image_id,
        image_name="myapp:latest",
        labels={},
        inspect={},
        repo_digests=["myapp@sha256:old"],
    )
    sidecar = Container(
        id="id-sidecar",
        name="sidecar",
        image_id=shared_image_id,
        image_name="other:latest",
        labels={},
        inspect={"HostConfig": {"NetworkMode": "container:web"}},
        repo_digests=["other@sha256:same"],
    )
    docker_client = FakeDockerClient([web, sidecar])
    docker_client.local_image_id = shared_image_id
    registry_client = FakeRegistryClient(
        {"myapp:latest": "sha256:new", "other:latest": "sha256:same"}
    )

    session = run(docker_client, registry_client, settings(no_pull=True))

    assert {c.name for c in session.stale} == {"web", "sidecar"}
    assert session.updated == []
    # web has no network-mode dependency of its own and its image is
    # unchanged, so it's a pure no-op this run -- never stopped or started
    # at all (see noop_names in core/updater.run).
    assert "stop:web" not in docker_client.calls
    assert "start:web" not in docker_client.calls
    assert "start:sidecar" in docker_client.calls
    assert not any(c.startswith("recreate:") for c in docker_client.calls)


def test_run_counts_update_as_successful_despite_post_update_hook_error() -> None:
    # The container is already recreated and started by the time the
    # post-update hook runs; a hook that errors out (distinct from one that
    # runs and exits non-zero, which lifecycle._run_hook only warns about)
    # shouldn't turn a successful update into a "failed" one.
    container = make_container(
        "web",
        repo_digests=["myapp@sha256:old"],
        labels={POST_UPDATE_LABEL: "curl -f localhost/health"},
    )
    docker_client = FakeDockerClient([container])
    docker_client.exec_fail = {"web"}
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings())

    assert [c.name for c in session.updated] == ["web"]
    assert session.failed == []


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
        "pull:myapp:latest",
        "stop:web",
        "recreate:web:sha256:pulled",
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

    assert [(c.name, reason) for c, reason in session.skipped] == [("web", "pinned")]
    assert session.stale == []
    assert docker_client.calls == []


def test_run_records_registry_errors_as_skipped() -> None:
    container = make_container("web", repo_digests=["myapp@sha256:old"])
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({}, errors={"myapp:latest"})

    session = run(docker_client, registry_client, settings())

    assert [(c.name, reason) for c, reason in session.skipped] == [("web", "check failed")]
    assert session.stale == []


def test_run_records_local_docker_check_errors_as_skipped_without_aborting_the_run() -> None:
    # Regression test: _is_stale()'s find_local_image_id fallback used to be
    # called outside the per-container try/except that already covers a
    # registry-side failure -- a transient Docker API error on one
    # container's staleness check would propagate out of run() entirely and
    # silently skip every container after it (and the run's own
    # notification). It must be caught and recorded the same way a registry
    # failure already is, and the next container must still be processed.
    web = make_container("web", repo_digests=[])  # orphaned -> triggers the fallback
    api = make_container("api", image_name="api:latest", repo_digests=["api@sha256:old"])
    docker_client = FakeDockerClient([web, api])
    docker_client.find_local_image_id_fail = {"myapp:latest"}
    registry_client = FakeRegistryClient(
        {"myapp:latest": "sha256:new", "api:latest": "sha256:new"}
    )

    session = run(docker_client, registry_client, settings())

    assert [(c.name, reason) for c, reason in session.skipped] == [("web", "check failed")]
    assert [c.name for c in session.stale] == ["api"]
    assert [c.name for c in session.updated] == ["api"]


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


def test_run_records_pull_failure_and_never_stops_the_container() -> None:
    # Regression test: a container whose replacement image can't be pulled
    # (registry blip, auth failure, ...) must never be stopped at all. An
    # earlier version pulled *after* stopping, so a failure here left the
    # container down with nothing to restart it -- and since
    # list_containers() only sees running containers, it was invisible to
    # every later poll too (caught live).
    container = make_container("web", repo_digests=["myapp@sha256:old"])
    docker_client = FakeDockerClient([container])
    docker_client.pull_fail = {"myapp:latest"}
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings())

    assert len(session.failed) == 1
    assert session.failed[0][0].name == "web"
    assert session.updated == []
    assert "stop:web" not in docker_client.calls
    assert not any(c.startswith("recreate:") for c in docker_client.calls)


def test_run_cleanup_removes_superseded_image() -> None:
    container = make_container("web", repo_digests=["myapp@sha256:old"])
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({"myapp:latest": "sha256:new"})

    session = run(docker_client, registry_client, settings(cleanup=True))

    assert docker_client.removed_images == [container.image_id]
    assert session.updated


def test_run_warns_when_stale_targets_netns_dependent_is_filtered_out(caplog: Any) -> None:
    # Regression test: the cascade can only recreate a dependent it can see
    # in `targets` -- a dependent excluded via --exclude (or not matching
    # --include, or lacking the --label-enable label) is invisible to it
    # entirely, so its container:web reference will still go stale once web
    # is recreated, with nothing lookout can do about it (the operator's own
    # filtering choice) beyond surfacing a warning instead of staying silent.
    web = make_container("web", image_name="myapp:latest", repo_digests=["myapp@sha256:old"])
    sidecar = make_container(
        "sidecar",
        image_name="other:latest",
        repo_digests=["other@sha256:same"],
        network_mode="container:web",
    )
    docker_client = FakeDockerClient([web, sidecar])
    registry_client = FakeRegistryClient(
        {"myapp:latest": "sha256:new", "other:latest": "sha256:same"}
    )

    with caplog.at_level("WARNING"):
        session = run(docker_client, registry_client, settings(exclude_names=["sidecar"]))

    assert [c.name for c in session.stale] == ["web"]
    assert "sidecar" in caplog.text
    assert "web" in caplog.text


def test_run_passes_resolved_auth_through_to_pull_image() -> None:
    # Regression test: the digest check and the pull used to resolve
    # credentials completely independently -- the digest check got them, the
    # pull never did, so a container relying solely on the
    # LOOKOUT_REGISTRY_* env-var fallback (no config.json at all, e.g. a
    # Portainer-style deployment) would authenticate correctly against the
    # registry for staleness but then pull anonymously and 401.
    container = make_container(
        "web", image_name="registry.example.com/myapp:latest", repo_digests=[]
    )
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({"registry.example.com/myapp:latest": "sha256:new"})

    run(
        docker_client,
        registry_client,
        settings(
            registry_host="registry.example.com",
            registry_username="user",
            registry_password="pass",
        ),
    )

    auth = docker_client.pull_auth["registry.example.com/myapp:latest"]
    assert auth is not None
    assert auth.username == "user"
    assert auth.password == "pass"


def test_run_unwraps_registry_password_secret_for_fallback_auth() -> None:
    # Regression test: registry_password is a SecretStr (so it can't leak
    # via a Settings repr/log line) -- run() must unwrap it with
    # get_secret_value() before building RegistryAuth, not pass the
    # SecretStr object itself through to the registry client.
    container = make_container(
        "web", image_name="registry.example.com/myapp:latest", repo_digests=[]
    )
    docker_client = FakeDockerClient([container])
    registry_client = FakeRegistryClient({"registry.example.com/myapp:latest": "sha256:new"})

    run(
        docker_client,
        registry_client,
        settings(
            registry_host="registry.example.com",
            registry_username="user",
            registry_password="the-actual-secret",
        ),
    )

    assert len(registry_client.received_auth) == 1
    auth = registry_client.received_auth[0]
    assert auth is not None
    assert auth.password == "the-actual-secret"  # type: ignore[attr-defined]
