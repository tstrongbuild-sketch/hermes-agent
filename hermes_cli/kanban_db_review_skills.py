"""Review-lane skill selection for kanban workers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_cli.kanban_db import Task


_QUALIFIED_REVIEW_SKILL = re.compile(r"^[^/\s:]+/sdlc-review$")


def _qualified_review_skill(value: object) -> str | None:
    """Return *value* only when it is an unambiguous qualified skill path."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _QUALIFIED_REVIEW_SKILL.fullmatch(value) else None


def _review_launch_skills(task: Task) -> list[str]:
    """Select review skills without allowing short-name or fallback lookup.

    The bundled reviewer is deliberately named by its full path.  In
    particular, passing ``sdlc-review`` to the general resolver is unsafe when
    multiple skill roots contain a skill with that basename.
    """
    from agent.skill_commands import resolve_auto_load_skills
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.profiles import normalize_profile_name, resolve_profile_env
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    skills = list(task.skills or [])
    selected = [qualified for name in skills if (qualified := _qualified_review_skill(name))]
    if not selected and task.assignee:
        try:
            home = resolve_profile_env(normalize_profile_name(task.assignee))
        except FileNotFoundError:
            home = None
        if home is not None:
            token = set_hermes_home_override(home)
            try:
                config = load_user_config_effective()
            finally:
                reset_hermes_home_override(token)
            selected = [
                qualified
                for name in resolve_auto_load_skills(config)
                if (qualified := _qualified_review_skill(name))
            ]
    # An explicit qualified selection takes precedence over profile defaults;
    # passing two same-basename paths to the general resolver is ambiguous.
    # Never pass an unqualified alias to the resolver.  The canonical bundled
    # path is explicit, so this fallback cannot silently select another skill.
    return list(dict.fromkeys([
        *(name for name in skills if _qualified_review_skill(name) or not _is_review_alias(name)),
        *(selected or ["devops/sdlc-review"]),
    ]))


def _is_review_alias(value: object) -> bool:
    """Identify review aliases that must not reach the general resolver."""
    return isinstance(value, str) and (
        value.strip() == "sdlc-review" or value.strip().endswith(":sdlc-review")
    )
