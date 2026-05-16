"""CLI: dump JSON Schema for every canonical domain type.

`packages/shared-types` re-runs this in CI to keep the TypeScript types in
lock-step with Pydantic. Output is deterministic (sorted keys, fixed
indent, trailing newline) so `git diff` on the generated files is signal,
not noise.

Usage:
    uv run verbio-export-schemas                       # default ../../schemas
    uv run verbio-export-schemas --out-dir custom/path
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter

from verbio_engine.domain import (
    ModeratorDecision,
    ParticipantState,
    QuietnessBudget,
    ResearcherCommand,
    RuleEvaluation,
    RulesConfig,
    SessionState,
)
from verbio_engine.realtime import TranscriptEvent, TranscriptEventAdapter

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

# Exported entries are either a Pydantic model class (`type[BaseModel]`)
# or a `TypeAdapter` instance. We carry the canonical title alongside
# each entry so json-schema-to-typescript pulls a stable TS type name
# regardless of whether the source is a class or a discriminated-union
# adapter (which Pydantic does not auto-title).
_Schemable = type["BaseModel"] | TypeAdapter[object]

EXPORTED_MODELS: tuple[tuple[str, str, _Schemable], ...] = cast(
    "tuple[tuple[str, str, _Schemable], ...]",
    (
        ("participant_state", "ParticipantState", ParticipantState),
        ("session_state", "SessionState", SessionState),
        ("moderator_decision", "ModeratorDecision", ModeratorDecision),
        ("rule_evaluation", "RuleEvaluation", RuleEvaluation),
        ("rules_config", "RulesConfig", RulesConfig),
        ("researcher_command", "ResearcherCommand", ResearcherCommand),
        ("quietness_budget", "QuietnessBudget", QuietnessBudget),
        # TranscriptEvent is a discriminated union (TypeAlias), so its schema
        # comes from the TypeAdapter rather than a class method, and the
        # title is injected explicitly below.
        ("transcript_event", "TranscriptEvent", TranscriptEventAdapter),
    ),
)
"""(filename stem, JSON Schema title, model/adapter). Order is stable for deterministic CI."""

# Suppress unused-import lint: TranscriptEvent is re-exported via this
# module so external consumers can keep the historical import path.
_ = TranscriptEvent

DEFAULT_OUT_DIR: Path = Path(__file__).resolve().parents[3] / "schemas"
"""Repo-root `schemas/` directory."""


def _render_schema(model: _Schemable, title: str) -> str:
    """Serialise the model's JSON Schema deterministically.

    Pydantic exposes two near-identical APIs:
      * `BaseModel.model_json_schema(mode=...)` for classes.
      * `TypeAdapter.json_schema(mode=...)`     for adapters.
    Both accept `mode="serialization"`, which gives us the wire shape
    (no defaults stripped).

    Adapters for discriminated unions don't carry a `title`, so we
    inject the caller-supplied one — json-schema-to-typescript uses
    that to name the generated TS type, and we want stable names.
    """
    if isinstance(model, TypeAdapter):
        schema = model.json_schema(mode="serialization")
    else:
        schema = model.model_json_schema(mode="serialization")
    # Force-set so the title we ship matches the canonical name even if
    # a future Pydantic auto-derives a different one.
    schema["title"] = title
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def export_all(out_dir: Path) -> list[Path]:
    """Write one `<stem>.generated.json` per registered model."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for stem, title, model in EXPORTED_MODELS:
        target = out_dir / f"{stem}.generated.json"
        target.write_text(_render_schema(model, title), encoding="utf-8")
        written.append(target)
    return written


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="verbio-export-schemas",
        description="Dump JSON Schema for the canonical domain types.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUT_DIR}).",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any file would change instead of writing.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir: Path = args.out_dir

    if args.check:
        changed: list[str] = []
        for stem, title, schemable in EXPORTED_MODELS:
            target = out_dir / f"{stem}.generated.json"
            rendered = _render_schema(schemable, title)
            current = target.read_text(encoding="utf-8") if target.exists() else ""
            if current != rendered:
                changed.append(target.name)
        if changed:
            sys.stderr.write(
                "schemas out of date — re-run `uv run verbio-export-schemas`:\n  - "
                + "\n  - ".join(changed)
                + "\n",
            )
            return 1
        return 0

    written = export_all(out_dir)
    cwd = Path.cwd()
    for path in written:
        display = path.relative_to(cwd) if path.is_relative_to(cwd) else path
        sys.stdout.write(f"wrote {display}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised via CLI.
    raise SystemExit(main())
