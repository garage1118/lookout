from lookout.core.session import Session
from lookout.docker.container import Container


def make_container(name: str, image_name: str = "myapp:latest") -> Container:
    return Container(
        id=f"id-{name}",
        name=name,
        image_id="sha256:x",
        image_name=image_name,
        labels={},
        inspect={},
    )


def test_summary_with_nothing_to_report() -> None:
    session = Session()
    assert session.summary() == "lookout run summary: 0 updated, 0 failed, 0 stale, 0 skipped"


def test_summary_lists_updated_containers() -> None:
    session = Session(updated=[make_container("web")])
    summary = session.summary()
    assert "1 updated" in summary
    assert "Updated:" in summary
    assert "web (myapp:latest)" in summary


def test_summary_lists_stale_but_not_updated_containers() -> None:
    web = make_container("web")
    session = Session(stale=[web])
    summary = session.summary()
    assert "1 stale" in summary
    assert "Stale (not updated):" in summary
    assert "web (myapp:latest)" in summary


def test_summary_excludes_updated_containers_from_stale_not_updated_section() -> None:
    web = make_container("web")
    session = Session(stale=[web], updated=[make_container("web")])
    summary = session.summary()
    assert "Stale (not updated):" not in summary


def test_summary_excludes_failed_containers_from_stale_not_updated_section() -> None:
    web = make_container("web")
    session = Session(stale=[web], failed=[(web, RuntimeError("stop failed"))])
    summary = session.summary()
    assert "Stale (not updated):" not in summary
    assert "Failed:" in summary
    assert "web: stop failed" in summary


def test_summary_lists_skipped_containers() -> None:
    session = Session(skipped=[make_container("pinned")])
    summary = session.summary()
    assert "1 skipped" in summary
    assert "Skipped:" in summary
    assert "pinned" in summary


def test_has_activity_false_for_empty_session() -> None:
    assert Session().has_activity() is False


def test_has_activity_false_for_skipped_only() -> None:
    assert Session(skipped=[make_container("pinned")]).has_activity() is False


def test_has_activity_true_for_updated() -> None:
    assert Session(updated=[make_container("web")]).has_activity() is True


def test_has_activity_true_for_stale() -> None:
    assert Session(stale=[make_container("web")]).has_activity() is True


def test_has_activity_true_for_failed() -> None:
    web = make_container("web")
    assert Session(failed=[(web, RuntimeError("boom"))]).has_activity() is True
