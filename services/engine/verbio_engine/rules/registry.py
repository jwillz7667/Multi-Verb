"""`RulesRegistry` — the lookup table for the engine's rule set.

The tick loop never imports concrete rules directly; it asks the
registry "give me every rule for `rules_version=v1.0`" and gets back
a stable, name-ordered sequence. Concrete rules attach themselves to
a registry at construction time so test code can build a registry
with a subset (e.g., only `silence_gap`) without touching globals.

Design notes:
  * Registries are immutable after construction — you build one with
    the full rule set, freeze it, and pass it to the runtime. There is
    no "add another rule mid-session" affordance because rule sets are
    snapshotted on the session (`config_snapshot.rules_version`).
  * Name collisions raise at build time, not at lookup time. We'd
    rather crash on import than silently shadow a rule with another
    of the same name.
  * Versions: a registry pins exactly one `rules_version`. Loading a
    session whose snapshotted `rules_version` differs is the runtime's
    job to detect — the registry just exposes the version it carries.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from verbio_engine.rules.protocol import Rule


class DuplicateRuleError(ValueError):
    """Two rules registered under the same `name`."""


class UnknownRuleError(KeyError):
    """Looked up a rule name not present in the registry."""


class RulesRegistry:
    """Immutable, name-keyed collection of `Rule` instances."""

    __slots__ = ("_by_name", "_rules_version")

    def __init__(self, rules: Iterable[Rule], *, rules_version: str) -> None:
        if not rules_version:
            msg = "rules_version must be a non-empty string"
            raise ValueError(msg)

        by_name: dict[str, Rule] = {}
        for rule in rules:
            if rule.name in by_name:
                msg = (
                    f"duplicate rule name {rule.name!r}: "
                    f"already registered as {type(by_name[rule.name]).__name__}, "
                    f"cannot also register {type(rule).__name__}"
                )
                raise DuplicateRuleError(msg)
            by_name[rule.name] = rule

        # Frozen storage. We dict-copy and sort by name so iteration order
        # is deterministic across processes (Python dict preserves insertion
        # order but a process that builds the registry in a different
        # order would otherwise produce a different evaluation sequence
        # — which we'd see as different reason_codes ordering in audit
        # rows for what is otherwise an identical session).
        self._by_name: dict[str, Rule] = dict(sorted(by_name.items()))
        self._rules_version: str = rules_version

    @property
    def rules_version(self) -> str:
        """The version string this registry was built for."""
        return self._rules_version

    def __len__(self) -> int:
        return len(self._by_name)

    def __iter__(self) -> Iterator[Rule]:
        return iter(self._by_name.values())

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._by_name

    def names(self) -> tuple[str, ...]:
        """Stable, sorted tuple of registered rule names."""
        return tuple(self._by_name.keys())

    def get(self, name: str) -> Rule:
        """Look up a rule by name. Raises `UnknownRuleError` if missing."""
        try:
            return self._by_name[name]
        except KeyError as exc:
            msg = f"no rule named {name!r}; known: {sorted(self._by_name)}"
            raise UnknownRuleError(msg) from exc

    def all(self) -> tuple[Rule, ...]:
        """Snapshot of every rule, in name-sorted order. Use this for
        `evaluate_all` to keep the tick loop's iteration deterministic.
        """
        return tuple(self._by_name.values())
