"""Review launches must reach the real skill resolver without short-name collisions."""

from typing import Any, cast

import pytest

from agent.skill_commands import build_preloaded_skills_prompt
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as dispatch
from hermes_cli.kanban_db_review_skills import _review_launch_skills


@pytest.mark.parametrize(
    "configured,explicit,duplicate,review,expected",
    [
        ("software-development/sdlc-review", [], True, True, "alternate review"),
        ("devops/sdlc-review", [], True, True, "upstream review"),
        (None, ["software-development/sdlc-review"], True, True, "alternate review"),
        ("devops/sdlc-review", ["software-development/sdlc-review"], True, True, "alternate review"),
        ("software-development/sdlc-review", ["sdlc-review"], True, True, "alternate review"),

        (None, [], False, True, "upstream review"),
        ("software-development/sdlc-review", [], True, False, None),
    ],
)
def test_review_launch_resolves_selected_skill(
    tmp_path, monkeypatch, configured, explicit, duplicate, review, expected,
):
    home = tmp_path / "home"
    profile = home / "profiles" / "reviewer"
    profile.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    auto_load = f"[{configured}]" if configured else "[]"
    (profile / "config.yaml").write_text(
        f"skills:\n  auto_load: {auto_load}\n", encoding="utf-8",
    )
    (home / "config.yaml").write_text(
        "kanban:\n  review_dispatch: true\nskills:\n  auto_load: [wrong/sdlc-review]\n",
        encoding="utf-8",
    )
    for name, body in [
        ("domain", "domain guidance"),
        ("devops/sdlc-review", "upstream review"),
        *([("software-development/sdlc-review", "alternate review")] if duplicate else []),
    ]:
        directory = profile / "skills" / name
        directory.mkdir(parents=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name.rsplit('/', 1)[-1]}\ndescription: test guidance\n---\n{body}\n",
            encoding="utf-8",
        )
    captured = []
    unresolved = []

    def spawn(task, workspace):
        from hermes_constants import reset_hermes_home_override, set_hermes_home_override
        token = set_hermes_home_override(str(profile))
        try:
            prompt, loaded, missing = build_preloaded_skills_prompt(task.skills)
        finally:
            reset_hermes_home_override(token)
        unresolved.extend(missing)
        assert "domain guidance" in prompt
        if expected:
            assert expected in prompt
        else:
            assert "alternate review" not in prompt
            assert "upstream review" not in prompt
        captured.append(task.skills)

    kb.init_db()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="review skill", assignee="reviewer", skills=["domain", *explicit])
        if review:
            kb.claim_task(conn, tid)
            assert kb.request_review(conn, tid, summary="ready")
        result = dispatch.dispatch_once(conn, spawn_fn=spawn, reconcile_orphans=False)
        assert not unresolved, unresolved
        assert [item[0] for item in result.spawned] == [tid]
    assert len(captured) == 1
    assert captured[0][0] == "domain"
    assert all(skill != "sdlc-review" and not skill.endswith(":sdlc-review") for skill in captured[0])


def test_review_launch_rejects_bare_and_ambiguous_review_references():
    class Task:
        skills = ["domain", "sdlc-review", "devops:sdlc-review", "devops/sdlc-review"]
        assignee = None

    assert _review_launch_skills(cast(Any, Task())) == ["domain", "devops/sdlc-review"]
