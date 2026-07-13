from __future__ import annotations

import logging

from lookout.docker.client import DockerClient
from lookout.docker.container import Container

PRE_CHECK_LABEL = "io.lookout.lifecycle.pre-check"
POST_CHECK_LABEL = "io.lookout.lifecycle.post-check"
PRE_UPDATE_LABEL = "io.lookout.lifecycle.pre-update"
POST_UPDATE_LABEL = "io.lookout.lifecycle.post-update"

logger = logging.getLogger(__name__)


def pre_update(client: DockerClient, container: Container) -> None:
    _run_hook(client, container, PRE_UPDATE_LABEL)


def post_update(client: DockerClient, container: Container) -> None:
    _run_hook(client, container, POST_UPDATE_LABEL)


def _run_hook(client: DockerClient, container: Container, label: str) -> None:
    command = container.labels.get(label)
    if not command:
        return
    exit_code, output = client.exec_run(container, ["/bin/sh", "-c", command])
    if exit_code != 0:
        logger.warning(
            "lifecycle hook %s failed on %s (exit %d): %s",
            label,
            container.name,
            exit_code,
            output.decode(errors="replace"),
        )
