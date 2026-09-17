"""The reviewed-bundle digest over a Project's executable surfaces.

A Project's manifest is not the only thing a session start executes. kiro-cli
reads the session cwd's own ``.kiro/settings/mcp.json`` (full server
definitions, command lines included) and its ``.kiro/agents/`` (which may carry
``mcpServers``) directly, and ``sync`` fast-forwards a linked repository with no
review step. Those two surfaces are therefore part of the reviewed bundle: the
digest recorded when the owner adds a Project covers them alongside
``project.yaml``, ``sync`` recomputes it, and a Project whose digest moved is
*review stale* until the owner looks again.

Text surfaces are deliberately NOT covered. ``.kiro/steering`` and the
checkout's documents reach a session as instructions kiro-cli loads from any
directory it runs in, under the same posture as a directory the user opened by
hand, and ``.kiro/skills`` keeps its own per-directory consent grant. Only
content that becomes an executable definition without the owner seeing it is
gated here.

Absent files hash as absent rather than being skipped, so DELETING a reviewed
``mcp.json`` is a change the owner is shown, not a silent return to a clean
digest. A checkout carrying none of these surfaces has nothing to gate: its
digest is the manifest's alone.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from kiro_crew.project_manifest import PROJECT_MANIFEST_NAME

#: Bounded like the manifest read: an executable definition that does not fit is
#: refused review rather than trusted unread.
REVIEW_FILE_MAX_BYTES = 1024 * 1024
#: A hostile commit cannot make review unaffordable by adding thousands of agent
#: files; past this many the digest records the overflow instead of the contents.
REVIEW_FILE_LIMIT = 512

MCP_SETTINGS_RELPATH = ".kiro/settings/mcp.json"
AGENTS_RELDIR = ".kiro/agents"

_ABSENT = "absent"
_UNREADABLE = "unreadable"
_OVERFLOW = "overflow"


def _hash_file(root: Path, relpath: str) -> str:
    """Digest one reviewed file through the hardened link-refusing reader."""
    from kiro_crew.hooks import FileTooLargeError, safe_read_file_bytes_nolink

    try:
        content = safe_read_file_bytes_nolink(
            str(root / relpath),
            within_root=str(root),
            max_bytes=REVIEW_FILE_MAX_BYTES,
        )
    except FileTooLargeError:
        # Oversized counts as CHANGED, never as unchanged: an unreadable
        # executable definition must not inherit a reviewed digest.
        return f"{_UNREADABLE}:too-large"
    except OSError:
        return f"{_UNREADABLE}:error"
    if content is None:
        # Missing, a link, or not a regular file. A link where a reviewed file
        # belongs is not "absent" -- it is a different thing than what was
        # reviewed, so it hashes distinctly.
        if not (root / relpath).exists():
            return _ABSENT
        return f"{_UNREADABLE}:not-a-regular-file"
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _agent_relpaths(checkout: Path) -> list[str]:
    """Every file under the checkout's ``.kiro/agents/``, sorted, bounded.

    Walked with ``os.walk``-free ``rglob`` and filtered to regular files whose
    resolved location stays inside the agents directory, so a symlinked tree
    cannot enumerate paths the digest would then attribute to the checkout.
    """
    agents_dir = checkout / AGENTS_RELDIR
    try:
        if not agents_dir.is_dir():
            return []
        agents_root = agents_dir.resolve(strict=True)
    except (OSError, RuntimeError):
        return []
    found: list[str] = []
    try:
        for candidate in sorted(agents_dir.rglob("*")):
            try:
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve(strict=True)
                if resolved != agents_root and agents_root not in resolved.parents:
                    continue
                found.append(candidate.relative_to(checkout).as_posix())
            except (OSError, RuntimeError, ValueError):
                continue
            if len(found) > REVIEW_FILE_LIMIT:
                break
    except (OSError, RuntimeError):
        return sorted(found)
    return sorted(found)


def review_file_hashes(bundle_dir: Path, checkout_dir: Path | None) -> dict[str, str]:
    """The reviewed surface as ``relative path -> content hash``.

    ``project.yaml`` is keyed from the bundle; the executable surfaces are keyed
    from the PRIMARY checkout, which is the directory a session actually runs
    in. When the primary checkout IS the bundle (a Project declaring no
    sources), both come from the same tree and the keys stay distinct.
    """
    hashes = {PROJECT_MANIFEST_NAME: _hash_file(bundle_dir, PROJECT_MANIFEST_NAME)}
    if checkout_dir is None:
        return hashes
    hashes[MCP_SETTINGS_RELPATH] = _hash_file(checkout_dir, MCP_SETTINGS_RELPATH)
    relpaths = _agent_relpaths(checkout_dir)
    if len(relpaths) > REVIEW_FILE_LIMIT:
        hashes[AGENTS_RELDIR] = f"{_OVERFLOW}:{len(relpaths)}"
        relpaths = relpaths[:REVIEW_FILE_LIMIT]
    for relpath in relpaths:
        hashes[relpath] = _hash_file(checkout_dir, relpath)
    return hashes


def review_digest(hashes: dict[str, str]) -> str:
    """One stable digest over the reviewed surface.

    Keyed on the path as well as the content so moving a definition between
    files, or deleting one, changes the digest.
    """
    material = "\n".join(f"{path}\0{digest}" for path, digest in sorted(hashes.items()))
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def compute_review_digest(
    bundle_dir: Path, checkout_dir: Path | None
) -> tuple[str, dict[str, str]]:
    """Return the reviewed digest and the per-file hashes it was built from."""
    hashes = review_file_hashes(bundle_dir, checkout_dir)
    return review_digest(hashes), hashes


def changed_review_files(reviewed: dict[str, str], current: dict[str, str]) -> tuple[str, ...]:
    """Relative paths whose reviewed hash differs from the current one.

    Includes paths present on only one side, so both an added and a removed
    executable definition are named to the owner.
    """
    return tuple(
        sorted(
            path
            for path in set(reviewed) | set(current)
            if reviewed.get(path, _ABSENT) != current.get(path, _ABSENT)
        )
    )


def unreviewable_files(current: dict[str, str]) -> tuple[str, ...]:
    """Reviewed paths whose current state can never be an accepted baseline.

    A link, an oversized file or a read error hashes to a marker rather than
    to its content, and the marker is stable: a link's TARGET can change
    without moving it. kiro-cli follows the link and loads the target, so a
    digest that accepted the marker would let a definition the owner never saw
    reach a session. These paths therefore count as stale on every check, and
    stay stale until the owner replaces them with regular files and reviews.
    """
    return tuple(sorted(path for path, digest in current.items() if digest.startswith(_UNREADABLE)))
