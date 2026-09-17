"""The reviewed-bundle digest over the checkout's executable surfaces.

The synced checkout is a second MCP channel: kiro-cli reads its own
``.kiro/settings/mcp.json`` and ``.kiro/agents/`` from the cwd, and ``sync``
fast-forwards with no review step. These cover that the digest sees both
surfaces, that a sync which moves either one marks the Project review stale,
that a session refuses to start on one, and that a re-review clears it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state

from kiro_crew.dashboard import chat_runner, handlers_project
from kiro_crew.dashboard.chat import api_chat_slot_create
from kiro_crew.dashboard.state import _ChatSlot
from kiro_crew.project_git import GitProjectStore, ProjectGitError
from kiro_crew.project_registry import ProjectRegistry
from kiro_crew.project_review import (
    AGENTS_RELDIR,
    MCP_SETTINGS_RELPATH,
    changed_review_files,
    compute_review_digest,
)
from kiro_crew.project_sessions import (
    ProjectSessionError,
    resolve_project_attachment,
    review_stale_files,
)

_PROJECT_ID = "018f4f4a-760f-7a8b-a5d4-5a7e0f130d4e"


def _registry(tmp_path: Path) -> ProjectRegistry:
    return ProjectRegistry(
        projects_dir=tmp_path / "projects",
        registry_dir=tmp_path / "projects-registry",
    )


def _bundle(tmp_path: Path, name: str = "Payments") -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "crew.kiro/v1",
                "kind": "Project",
                "id": _PROJECT_ID,
                "name": name,
                "sources": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return bundle


def _write(root: Path, relpath: str, text: str) -> None:
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def _registered(tmp_path: Path, bundle: Path) -> tuple[ProjectRegistry, object]:
    registry = _registry(tmp_path)
    project = registry.add_local(bundle)
    digest, hashes = compute_review_digest(bundle, bundle)
    return registry, registry.record_review(project.id, digest, hashes)


class TestDigestCoverage:
    def test_a_checkout_with_no_executable_surfaces_has_nothing_to_gate(
        self, tmp_path: Path
    ) -> None:
        bundle = _bundle(tmp_path)
        digest, hashes = compute_review_digest(bundle, bundle)

        assert set(hashes) == {"project.yaml", MCP_SETTINGS_RELPATH}
        assert hashes[MCP_SETTINGS_RELPATH] == "absent"
        assert digest.startswith("sha256:")

    def test_adding_an_mcp_settings_file_moves_the_digest(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        before, before_hashes = compute_review_digest(bundle, bundle)

        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {"x": {"command": "id"}}}))
        after, after_hashes = compute_review_digest(bundle, bundle)

        assert after != before
        assert changed_review_files(before_hashes, after_hashes) == (MCP_SETTINGS_RELPATH,)

    def test_editing_an_mcp_settings_file_moves_the_digest(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {}}))
        before, before_hashes = compute_review_digest(bundle, bundle)

        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {"x": {"command": "sh"}}}))
        after, after_hashes = compute_review_digest(bundle, bundle)

        assert after != before
        assert changed_review_files(before_hashes, after_hashes) == (MCP_SETTINGS_RELPATH,)

    def test_deleting_a_reviewed_mcp_settings_file_moves_the_digest(self, tmp_path: Path) -> None:
        # Absent hashes as absent rather than being skipped, so a deletion is a
        # change the owner is shown instead of a silent return to a clean digest.
        bundle = _bundle(tmp_path)
        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {}}))
        before, before_hashes = compute_review_digest(bundle, bundle)

        (bundle / MCP_SETTINGS_RELPATH).unlink()
        after, after_hashes = compute_review_digest(bundle, bundle)

        assert after != before
        assert changed_review_files(before_hashes, after_hashes) == (MCP_SETTINGS_RELPATH,)

    def test_an_agent_file_is_covered(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        before, before_hashes = compute_review_digest(bundle, bundle)

        _write(bundle, f"{AGENTS_RELDIR}/helper.json", json.dumps({"mcpServers": {}}))
        after, after_hashes = compute_review_digest(bundle, bundle)

        assert after != before
        assert changed_review_files(before_hashes, after_hashes) == (
            f"{AGENTS_RELDIR}/helper.json",
        )

    def test_editing_a_nested_agent_file_is_covered(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        _write(bundle, f"{AGENTS_RELDIR}/team/one.json", json.dumps({"mcpServers": {}}))
        before, before_hashes = compute_review_digest(bundle, bundle)

        _write(
            bundle,
            f"{AGENTS_RELDIR}/team/one.json",
            json.dumps({"mcpServers": {"evil": {"command": "curl"}}}),
        )
        after, after_hashes = compute_review_digest(bundle, bundle)

        assert after != before
        assert changed_review_files(before_hashes, after_hashes) == (
            f"{AGENTS_RELDIR}/team/one.json",
        )

    def test_a_readme_change_does_not_move_the_digest(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        _write(bundle, "README.md", "before")
        before, _ = compute_review_digest(bundle, bundle)

        _write(bundle, "README.md", "after, with completely different prose")
        after, _ = compute_review_digest(bundle, bundle)

        assert after == before

    def test_a_steering_change_does_not_move_the_digest(self, tmp_path: Path) -> None:
        # Text surfaces inherit kiro-cli's repository-trust posture by decision;
        # only content that becomes an executable definition is gated here.
        bundle = _bundle(tmp_path)
        _write(bundle, ".kiro/steering/house.md", "before")
        before, _ = compute_review_digest(bundle, bundle)

        _write(bundle, ".kiro/steering/house.md", "after")
        after, _ = compute_review_digest(bundle, bundle)

        assert after == before

    def test_a_manifest_change_moves_the_digest(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        before, before_hashes = compute_review_digest(bundle, bundle)

        _bundle(tmp_path, name="Renamed")
        after, after_hashes = compute_review_digest(bundle, bundle)

        assert after != before
        assert changed_review_files(before_hashes, after_hashes) == ("project.yaml",)


class TestReviewStaleState:
    def test_a_freshly_recorded_project_is_not_stale(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {}}))
        _registry_, project = _registered(tmp_path, bundle)

        assert review_stale_files(project, bundle, bundle) == ()

    def test_a_project_with_no_review_record_is_not_stale(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        registry = _registry(tmp_path)
        project = registry.add_local(bundle)

        assert project.reviewed_digest == ""
        assert review_stale_files(project, bundle, bundle) == ()

    def test_a_changed_surface_names_exactly_the_changed_files(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {}}))
        _registry_, project = _registered(tmp_path, bundle)

        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {"x": {"command": "sh"}}}))
        _write(bundle, f"{AGENTS_RELDIR}/new.json", "{}")
        _write(bundle, "README.md", "irrelevant")

        assert review_stale_files(project, bundle, bundle) == (
            f"{AGENTS_RELDIR}/new.json",
            MCP_SETTINGS_RELPATH,
        )

    def test_a_linked_surface_is_stale_even_when_its_marker_was_recorded(
        self, tmp_path: Path
    ) -> None:
        # A link to a file outside the checkout hashes to a stable marker (the
        # hardened reader refuses it), but kiro-cli follows the link, so the
        # TARGET is what a session loads. Recording the marker must not turn a
        # later change of the target into an accepted definition.
        bundle = _bundle(tmp_path)
        outside = tmp_path / "elsewhere" / "servers.json"
        outside.parent.mkdir(parents=True)
        outside.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        link = bundle / MCP_SETTINGS_RELPATH
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside)
        _registry_, project = _registered(tmp_path, bundle)
        assert project.reviewed_files[MCP_SETTINGS_RELPATH].startswith("unreadable")

        assert review_stale_files(project, bundle, bundle) == (MCP_SETTINGS_RELPATH,)

        outside.write_text(json.dumps({"mcpServers": {"x": {"command": "sh"}}}), encoding="utf-8")
        assert review_stale_files(project, bundle, bundle) == (MCP_SETTINGS_RELPATH,)

        link.unlink()
        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {}}))
        digest, hashes = compute_review_digest(bundle, bundle)
        project = _registry(tmp_path).record_review(project.id, digest, hashes)
        assert review_stale_files(project, bundle, bundle) == ()

    def test_the_review_record_survives_a_re_registration(self, tmp_path: Path) -> None:
        # ``sync`` re-registers the same clone to refresh its metadata; that must
        # not ratify the surfaces the pull just changed.
        bundle = _bundle(tmp_path)
        registry, project = _registered(tmp_path, bundle)
        recorded = project.reviewed_digest

        again = registry.add_local(bundle)

        assert again.reviewed_digest == recorded
        assert again.reviewed_files == project.reviewed_files

    def test_refresh_preserves_the_review_record(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        registry, project = _registered(tmp_path, bundle)

        refreshed = registry.refresh(project.id)

        assert refreshed.reviewed_digest == project.reviewed_digest

    def test_the_review_record_round_trips_through_the_registry_file(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        registry, project = _registered(tmp_path, bundle)

        reread = _registry(tmp_path).get(project.id)

        assert reread.reviewed_digest == project.reviewed_digest
        assert reread.reviewed_files == project.reviewed_files


class TestSessionRefusal:
    def test_attachment_refuses_a_review_stale_project(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        registry, _project = _registered(tmp_path, bundle)
        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {"x": {"command": "sh"}}}))

        with pytest.raises(ProjectSessionError) as exc_info:
            resolve_project_attachment(_PROJECT_ID, registry=registry)

        assert exc_info.value.code == "project_review_stale"
        assert MCP_SETTINGS_RELPATH in str(exc_info.value)

    def test_attachment_succeeds_once_the_surfaces_are_reviewed_again(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        registry, project = _registered(tmp_path, bundle)
        _write(bundle, f"{AGENTS_RELDIR}/added.json", "{}")

        digest, hashes = compute_review_digest(bundle, bundle)
        registry.record_review(project.id, digest, hashes)

        attachment = resolve_project_attachment(_PROJECT_ID, registry=registry)
        assert attachment.workspace_dir == bundle.resolve()

    @pytest.mark.asyncio
    async def test_the_next_turn_fails_loudly_on_a_review_stale_project(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bundle = _bundle(tmp_path)
        registry, _project = _registered(tmp_path, bundle)
        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {"x": {"command": "sh"}}}))
        monkeypatch.setattr(
            "kiro_crew.project_sessions.resolve_project_attachment",
            lambda project_id: resolve_project_attachment(project_id, registry=registry),
        )
        slot = _ChatSlot("attached")
        slot.project_id = _PROJECT_ID
        slot.project = str(bundle)

        with pytest.raises(ProjectSessionError) as exc_info:
            await chat_runner._refresh_project_attachment(slot)

        assert exc_info.value.code == "project_review_stale"


def _app(state, registry: ProjectRegistry) -> web.Application:
    @web.middleware
    async def identity(request: web.Request, handler):
        request["app"] = ""
        request["user"] = "local-app"
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app["state"] = state
    services = handlers_project._ProjectServices()
    services._registry = registry
    app[handlers_project.PROJECT_SERVICES_KEY] = services
    app.router.add_post("/api/chat/slots", api_chat_slot_create)
    app.router.add_get("/api/project-bundles/{id}", handlers_project.api_project_get)
    app.router.add_post("/api/project-bundles/{id}/review", handlers_project.api_project_review)
    return app


class TestReviewApi:
    @pytest.mark.asyncio
    async def test_create_on_a_review_stale_project_is_refused(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        registry, _project = _registered(tmp_path, bundle)
        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {"x": {"command": "sh"}}}))
        state = _make_state(tmp_path / "sessions")

        async with TestClient(TestServer(_app(state, registry))) as client:
            response = await client.post(
                "/api/chat/slots", json={"name": "stale-chat", "project_id": _PROJECT_ID}
            )
            assert response.status == 409
            assert (await response.json())["code"] == "project_review_stale"
        assert "stale-chat" not in state._slots

    @pytest.mark.asyncio
    async def test_health_reports_review_stale_with_the_changed_files(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        registry, _project = _registered(tmp_path, bundle)
        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {"x": {"command": "sh"}}}))
        state = _make_state(tmp_path / "sessions")

        async with TestClient(TestServer(_app(state, registry))) as client:
            response = await client.get(f"/api/project-bundles/{_PROJECT_ID}")
            assert response.status == 200
            payload = await response.json()

        assert payload["health"] == {
            "status": "review_stale",
            "code": "project_review_stale",
            "stale_files": [MCP_SETTINGS_RELPATH],
        }

    @pytest.mark.asyncio
    async def test_a_healthy_project_carries_no_stale_files_key(self, tmp_path: Path) -> None:
        bundle = _bundle(tmp_path)
        registry, _project = _registered(tmp_path, bundle)
        state = _make_state(tmp_path / "sessions")

        async with TestClient(TestServer(_app(state, registry))) as client:
            payload = await (await client.get(f"/api/project-bundles/{_PROJECT_ID}")).json()

        assert payload["health"] == {"status": "healthy", "code": "project_healthy"}
        assert "stale_files" not in payload["health"]

    @pytest.mark.asyncio
    async def test_review_clears_the_stale_state_and_lets_a_session_start(
        self, tmp_path: Path
    ) -> None:
        bundle = _bundle(tmp_path)
        registry, _project = _registered(tmp_path, bundle)
        _write(bundle, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {"x": {"command": "sh"}}}))
        state = _make_state(tmp_path / "sessions")

        async with TestClient(TestServer(_app(state, registry))) as client:
            reviewed = await client.post(f"/api/project-bundles/{_PROJECT_ID}/review")
            assert reviewed.status == 200
            payload = await reviewed.json()
            assert payload["health"] == {"status": "healthy", "code": "project_healthy"}

            started = await client.post(
                "/api/chat/slots", json={"name": "reviewed-chat", "project_id": _PROJECT_ID}
            )
            assert started.status == 200
        assert state._slots["reviewed-chat"].project_id == _PROJECT_ID

    @pytest.mark.asyncio
    async def test_review_of_an_unknown_project_is_not_found(self, tmp_path: Path) -> None:
        registry = _registry(tmp_path)
        state = _make_state(tmp_path / "sessions")

        async with TestClient(TestServer(_app(state, registry))) as client:
            response = await client.post(f"/api/project-bundles/{_PROJECT_ID}/review")
            assert response.status == 404
            assert (await response.json())["code"] == "project_not_found"

    @pytest.mark.asyncio
    async def test_review_is_owner_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bundle = _bundle(tmp_path)
        registry, _project = _registered(tmp_path, bundle)
        state = _make_state(tmp_path / "sessions")
        monkeypatch.setattr(
            handlers_project,
            "is_owner_dashboard_request",
            lambda _request: False,
        )
        monkeypatch.setattr(
            handlers_project,
            "_sel",
            lambda: SimpleNamespace(log_api_access=lambda **_event: None),
        )

        async with TestClient(TestServer(_app(state, registry))) as client:
            response = await client.post(f"/api/project-bundles/{_PROJECT_ID}/review")
            assert response.status == 403
            assert (await response.json())["code"] == "owner_only"


class TestTheAgentsLiteralIsCheckoutRelative:
    """Replaces what the global-agents-dir guard would have checked here.

    ``project_review.py`` is exempt from ``test_no_new_hardcoded_global_agents_dir``
    because its ``.kiro/agents`` literal names a directory inside a Project's
    CHECKOUT, not the owner's home. These pin that reading: the constants stay
    relative, and the module never reaches for the machine-wide resolver or the
    home directory.
    """

    def test_the_constants_are_relative_paths(self) -> None:
        for relpath in (MCP_SETTINGS_RELPATH, AGENTS_RELDIR):
            assert not Path(relpath).is_absolute()
            assert not relpath.startswith("~")

    def test_the_module_never_resolves_the_global_agents_dir(self) -> None:
        import kiro_crew.project_review as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("kiro_agents_dir", "Path.home()", "expanduser", "KIRO_HOME"):
            assert forbidden not in source, forbidden

    def test_the_digest_only_reads_below_the_root_it_is_given(self, tmp_path: Path) -> None:
        # The checkout root is the only thing that decides where the reviewed
        # surfaces are read from, so a Project cannot be made to digest (or
        # execute) the owner's own agents directory.
        outside = tmp_path / "outside"
        _write(outside, f"{AGENTS_RELDIR}/global.json", json.dumps({"mcpServers": {"g": {}}}))
        bundle = _bundle(tmp_path)

        _digest, hashes = compute_review_digest(bundle, bundle)

        assert all("global.json" not in key for key in hashes)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "PATH": "/usr/bin:/bin",
            "HOME": str(cwd),
        },
    )


@pytest.mark.skipif(
    subprocess.run(["which", "git"], capture_output=True).returncode != 0,
    reason="git not installed",
)
def test_sync_marks_a_pulled_executable_definition_review_stale(tmp_path: Path) -> None:
    # The whole point of the gate: a fast-forward can land an MCP definition the
    # owner never saw, and the digest is what notices.
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-b", "main")
    (remote / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "crew.kiro/v1",
                "kind": "Project",
                "id": _PROJECT_ID,
                "name": "Payments",
                "sources": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _git(remote, "add", "project.yaml")
    _git(remote, "commit", "-m", "init")

    registry = _registry(tmp_path)
    store = GitProjectStore(registry)
    try:
        project = store.add(f"file://{remote}")
    except ProjectGitError as exc:
        if "sandbox" in str(exc).lower():
            pytest.skip(f"sandbox unavailable in this environment: {exc}")
        raise
    clone = project.registrations[-1].path
    digest, hashes = compute_review_digest(clone, clone)
    registry.record_review(project.id, digest, hashes)
    assert review_stale_files(registry.get(project.id), clone, clone) == ()

    # A hostile commit upstream, then a plain sync.
    _write(remote, MCP_SETTINGS_RELPATH, json.dumps({"mcpServers": {"x": {"command": "curl"}}}))
    _git(remote, "add", "-A")
    _git(remote, "commit", "-m", "add mcp settings")

    synced = store.sync(project.id)

    assert review_stale_files(synced, clone, clone) == (MCP_SETTINGS_RELPATH,)
    with pytest.raises(ProjectSessionError) as exc_info:
        resolve_project_attachment(_PROJECT_ID, registry=registry)
    assert exc_info.value.code == "project_review_stale"
