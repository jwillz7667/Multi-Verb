"""Schema-export CLI tests.

The contract this tool enforces is strong: byte-for-byte deterministic
output that CI can git-diff for staleness. These tests assert that
contract instead of the contents of any one schema.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from verbio_engine.schema_export import EXPORTED_MODELS, export_all, main

if TYPE_CHECKING:
    from pathlib import Path


def test_export_writes_all_expected_files(tmp_path: Path) -> None:
    written = export_all(tmp_path)

    assert len(written) == len(EXPORTED_MODELS)
    for stem, _title, _model in EXPORTED_MODELS:
        assert (tmp_path / f"{stem}.generated.json").is_file()


def test_export_output_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    export_all(first)
    export_all(second)

    for stem, _title, _model in EXPORTED_MODELS:
        a = (first / f"{stem}.generated.json").read_text(encoding="utf-8")
        b = (second / f"{stem}.generated.json").read_text(encoding="utf-8")
        assert a == b, f"{stem} export changed between runs"


def test_export_output_is_valid_json_with_title(tmp_path: Path) -> None:
    export_all(tmp_path)
    for stem, title, _model in EXPORTED_MODELS:
        contents = (tmp_path / f"{stem}.generated.json").read_text(encoding="utf-8")
        assert contents.endswith("\n"), f"{stem} missing trailing newline"
        payload = json.loads(contents)
        assert payload["title"] == title


def test_check_mode_passes_when_in_sync(tmp_path: Path) -> None:
    export_all(tmp_path)
    rc = main(["--out-dir", str(tmp_path), "--check"])
    assert rc == 0


def test_check_mode_fails_when_drifted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export_all(tmp_path)
    target = tmp_path / "participant_state.generated.json"
    target.write_text("{}\n", encoding="utf-8")

    rc = main(["--out-dir", str(tmp_path), "--check"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "participant_state.generated.json" in err


def test_check_mode_fails_when_file_missing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["--out-dir", str(tmp_path), "--check"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "out of date" in err
