from __future__ import annotations

from dataclasses import dataclass, field

from lookout.docker.container import Container


@dataclass
class Session:
    """Per-run bookkeeping for the summary notification."""

    updated: list[Container] = field(default_factory=list)
    failed: list[tuple[Container, Exception]] = field(default_factory=list)
    stale: list[Container] = field(default_factory=list)
    skipped: list[Container] = field(default_factory=list)

    def has_activity(self) -> bool:
        """Whether anything notable happened this run — used to skip sending
        a notification for a routine "nothing to do" pass. `skipped` (pinned
        images, or a registry check that failed — the latter is already
        logged locally at error level) is deliberately excluded, since it's
        the routine case for most real setups and would otherwise defeat the
        point of the toggle."""
        return bool(self.updated or self.failed or self.stale)

    def summary(self) -> str:
        updated_names = {c.name for c in self.updated}
        # containers found stale but left alone (monitor-only, or the stop/
        # update attempt failed) — `failed` already covers the latter with
        # its own reason, so only call out the monitor-only leftovers here.
        failed_names = {c.name for c, _ in self.failed}
        not_updated = [
            c for c in self.stale if c.name not in updated_names and c.name not in failed_names
        ]

        lines = [
            f"lookout run summary: {len(self.updated)} updated, "
            f"{len(self.failed)} failed, {len(self.stale)} stale, {len(self.skipped)} skipped"
        ]

        if self.updated:
            lines.append("")
            lines.append("Updated:")
            lines.extend(f"  - {c.name} ({c.image_name})" for c in self.updated)

        if not_updated:
            lines.append("")
            lines.append("Stale (not updated):")
            lines.extend(f"  - {c.name} ({c.image_name})" for c in not_updated)

        if self.failed:
            lines.append("")
            lines.append("Failed:")
            lines.extend(f"  - {c.name}: {exc}" for c, exc in self.failed)

        if self.skipped:
            lines.append("")
            lines.append("Skipped:")
            lines.extend(f"  - {c.name}" for c in self.skipped)

        return "\n".join(lines)
