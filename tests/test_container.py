from lookout.docker.container import Container


def test_from_inspect_parses_name_and_image() -> None:
    data = {
        "Id": "abc123",
        "Name": "/my-app",
        "Image": "sha256:deadbeef",
        "Config": {
            "Image": "myapp:latest",
            "Labels": {"io.lookout.enable": "true"},
        },
    }

    container = Container.from_inspect(data)

    assert container.id == "abc123"
    assert container.name == "my-app"
    assert container.image_id == "sha256:deadbeef"
    assert container.image_name == "myapp:latest"
    assert container.labels["io.lookout.enable"] == "true"


def test_is_monitor_only_defaults_false() -> None:
    container = Container.from_inspect(
        {"Id": "x", "Name": "/x", "Image": "sha256:x", "Config": {}}
    )
    assert container.is_monitor_only() is False


def test_is_lookout_instance_true_for_lookout_entrypoint() -> None:
    container = Container.from_inspect(
        {
            "Id": "x",
            "Name": "/lookout-fast",
            "Image": "sha256:x",
            "Config": {"Image": "garage1118/lookout:1.1.0", "Entrypoint": ["lookout"]},
        }
    )
    assert container.is_lookout_instance() is True


def test_is_lookout_instance_false_for_other_containers() -> None:
    container = Container.from_inspect(
        {
            "Id": "x",
            "Name": "/web",
            "Image": "sha256:x",
            "Config": {"Image": "myapp:latest", "Entrypoint": ["myapp"]},
        }
    )
    assert container.is_lookout_instance() is False


def test_is_lookout_instance_false_when_no_entrypoint() -> None:
    container = Container.from_inspect(
        {"Id": "x", "Name": "/x", "Image": "sha256:x", "Config": {}}
    )
    assert container.is_lookout_instance() is False


def test_scope_defaults_none() -> None:
    container = Container.from_inspect(
        {"Id": "x", "Name": "/x", "Image": "sha256:x", "Config": {}}
    )
    assert container.scope() is None


def test_scope_returns_label_value() -> None:
    container = Container.from_inspect(
        {
            "Id": "x",
            "Name": "/x",
            "Image": "sha256:x",
            "Config": {"Labels": {"io.lookout.scope": "dev"}},
        }
    )
    assert container.scope() == "dev"


def test_scope_empty_label_treated_as_unset() -> None:
    container = Container.from_inspect(
        {
            "Id": "x",
            "Name": "/x",
            "Image": "sha256:x",
            "Config": {"Labels": {"io.lookout.scope": ""}},
        }
    )
    assert container.scope() is None


def test_has_digest_matches_known_repo_digest() -> None:
    container = Container.from_inspect(
        {"Id": "x", "Name": "/x", "Image": "sha256:x", "Config": {}},
        repo_digests=["myapp@sha256:abc"],
    )
    assert container.has_digest("sha256:abc") is True
    assert container.has_digest("sha256:other") is False


def test_has_digest_false_when_no_repo_digests() -> None:
    # Empty repo_digests is NOT proof of freshness (see core/updater._is_stale)
    # - it just means this check can't answer the question on its own.
    container = Container.from_inspect({"Id": "x", "Name": "/x", "Image": "sha256:x", "Config": {}})
    assert container.has_digest("sha256:anything") is False


def test_has_no_tagged_image_name_true_for_empty_or_bare_image_id() -> None:
    # A container started directly from an image id (docker run sha256:...,
    # or a bare id) has no registry/repository/tag for parse_image() to work
    # with -- this is what lets core/updater.run skip it cleanly instead of
    # logging a fresh exception every poll.
    no_image = Container.from_inspect({"Id": "x", "Name": "/x", "Image": "sha256:x", "Config": {}})
    assert no_image.has_no_tagged_image_name() is True

    bare_digest = Container.from_inspect(
        {"Id": "x", "Name": "/x", "Image": "sha256:x", "Config": {"Image": "a" * 64}}
    )
    assert bare_digest.has_no_tagged_image_name() is True

    prefixed_digest = Container.from_inspect(
        {"Id": "x", "Name": "/x", "Image": "sha256:x", "Config": {"Image": "sha256:" + "a" * 64}}
    )
    assert prefixed_digest.has_no_tagged_image_name() is True


def test_has_no_tagged_image_name_false_for_a_real_image_name() -> None:
    container = Container.from_inspect(
        {"Id": "x", "Name": "/x", "Image": "sha256:x", "Config": {"Image": "myapp:latest"}}
    )
    assert container.has_no_tagged_image_name() is False
