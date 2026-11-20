"""Per-session launch environment: allowlisted variables a session's CLI starts with (LEX-2045).

A session row carries ``workload_config.launch_env`` ({name: value}). Every start and restart
hands it to the broker, whose environment the CLI inherits. Only names the operator allowlists
(``session_launch_env`` in Volundr's config) are accepted, each value checked against its rule,
so a session cannot set arbitrary process environment.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from pydantic import BaseModel, Field, model_validator

LAUNCH_ENV_KEY = "launch_env"


class LaunchEnvRule(BaseModel):
    """What one allowlisted variable may hold: a whole-value pattern, or an integer range."""

    pattern: str | None = Field(
        default=None, description="Regular expression the whole value must match."
    )
    min_int: int | None = Field(default=None, description="Smallest accepted integer value.")
    max_int: int | None = Field(default=None, description="Largest accepted integer value.")

    @model_validator(mode="after")
    def _one_kind(self) -> LaunchEnvRule:
        integer = self.min_int is not None or self.max_int is not None
        if self.pattern is not None and integer:
            raise ValueError("a launch env rule is a pattern or an integer range, not both")
        if self.pattern is None and not integer:
            raise ValueError("a launch env rule needs a pattern or an integer range")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"launch env pattern {self.pattern!r} is invalid: {exc}") from exc
        if self.min_int is not None and self.max_int is not None and self.min_int > self.max_int:
            raise ValueError("min_int is greater than max_int")
        return self

    def check(self, name: str, value: str) -> str:
        if self.pattern is not None:
            if re.fullmatch(self.pattern, value) is None:
                raise ValueError(f"launch_env {name}={value!r} does not match {self.pattern!r}")
            return value
        try:
            number = int(value)
        except ValueError:
            raise ValueError(f"launch_env {name} must be an integer, got {value!r}") from None
        if self.min_int is not None and number < self.min_int:
            raise ValueError(f"launch_env {name}={number} is below {self.min_int}")
        if self.max_int is not None and number > self.max_int:
            raise ValueError(f"launch_env {name}={number} is above {self.max_int}")
        return str(number)


def validate_launch_env(raw: object, rules: Mapping[str, LaunchEnvRule]) -> dict[str, str]:
    """Return the checked {name: value} map, or raise ValueError naming the first problem.

    Values may arrive as strings or integers (JSON numbers); they are stored as strings,
    the form an environment holds.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("launch_env must be an object of variable names to values")
    checked: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or name not in rules:
            allowed = ", ".join(sorted(rules)) or "none configured"
            raise ValueError(
                f"launch_env {name!r} is not allowed (allowed: {allowed}; "
                "the list is session_launch_env in Volundr's config)"
            )
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise ValueError(f"launch_env {name} must be a string or an integer")
        checked[name] = rules[name].check(name, str(value).strip())
    return checked
