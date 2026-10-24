from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_all_in_one_script_avoids_bash4_only_builtins():
    script = (REPO_ROOT / "scripts" / "tdnet_all_in_one.sh").read_text(encoding="utf-8")

    assert "mapfile" not in script
    assert "readarray" not in script


def test_all_in_one_is_parked_and_refuses_even_with_legacy_opt_in(tmp_path):
    """The --legacy-local pipeline is parked (2026-10-25): it no longer runs
    at all, with or without the opt-in flag, and never reaches Docker/venv
    setup or any `tdnet` subcommand."""
    scripts_dir = tmp_path / "scripts"
    venv_bin = tmp_path / ".venv" / "bin"
    scripts_dir.mkdir()
    venv_bin.mkdir(parents=True)
    shutil.copy(REPO_ROOT / "scripts" / "tdnet_all_in_one.sh", scripts_dir / "tdnet_all_in_one.sh")
    (venv_bin / "python").symlink_to(sys.executable)
    tdnet = venv_bin / "tdnet"
    tdnet.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    tdnet.chmod(0o755)

    bash = "/bin/bash" if Path("/bin/bash").exists() else "bash"
    env = {
        **os.environ,
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }
    result = subprocess.run(
        [
            bash,
            str(scripts_dir / "tdnet_all_in_one.sh"),
            "--legacy-local",
            "--dry-run",
            "--skip-postgres",
            "--skip-install",
            "--days",
            "1",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    assert result.returncode != 0
    assert "retired" in result.stdout
    assert "raw_tdnet_history" in result.stdout
    assert "Root issue #50" in result.stdout
    assert "TDNET_LEGACY_LOCAL_ENABLED" in result.stdout
    assert "(1 days)" not in result.stdout
    assert "COMMAND_DRY_RUN" not in result.stdout


def _fake_python(path: Path, record: Path, exit_code: int = 0) -> None:
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > {record}\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_daily_metadata_validates_date_and_forwards_exact_controller_argv(tmp_path):
    script = REPO_ROOT / "scripts" / "tdnet_daily_metadata.sh"
    research_root = tmp_path / "research"
    python = research_root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    record = tmp_path / "argv"
    _fake_python(python, record)

    env = {**os.environ, "RESEARCH_ROOT": str(research_root), "TDNET_PYTHON": str(python)}
    result = subprocess.run(
        ["/bin/bash", str(script), "--date", "2026-09-10"],
        env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert result.returncode == 0
    assert record.read_text(encoding="utf-8").splitlines() == [
        "-m", "hedge_research.disclosure_environment", "exec", "tdnet", "--",
        str(python), "-m", "tdnet.research_ingest", "--date", "2026-09-10"
    ]
    assert subprocess.run(["/bin/bash", str(script), "--date", "2026-9-10"], env=env).returncode != 0
    assert subprocess.run(["/bin/bash", str(script), "--date", "2026-02-30"], env=env).returncode != 0


def test_daily_metadata_propagates_child_failure_and_requires_dependency(tmp_path):
    script = REPO_ROOT / "scripts" / "tdnet_daily_metadata.sh"
    root = tmp_path / "research"
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    _fake_python(python, tmp_path / "argv", exit_code=37)
    env = {**os.environ, "RESEARCH_ROOT": str(root), "TDNET_PYTHON": str(python)}
    assert subprocess.run(["/bin/bash", str(script), "--date", "2026-09-10"], env=env).returncode == 37
    missing = {**env, "TDNET_PYTHON": str(tmp_path / "missing-python")}
    assert subprocess.run(["/bin/bash", str(script), "--date", "2026-09-10"], env=missing).returncode != 0


def test_daily_metadata_rejects_duplicate_singleton_options(tmp_path):
    script = REPO_ROOT / "scripts" / "tdnet_daily_metadata.sh"
    root = tmp_path / "research"
    python = root / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    record = tmp_path / "argv"
    _fake_python(python, record)
    env = {**os.environ, "RESEARCH_ROOT": str(root), "TDNET_PYTHON": str(python)}
    for args in (
        ["--date", "2026-09-10", "--date", "2026-09-11"],
        ["--date", "2026-09-10", "--output", "a.json", "--output", "b.json"],
    ):
        result = subprocess.run(["/bin/bash", str(script), *args], env=env)
        assert result.returncode == 2
        assert not record.exists()


def test_all_in_one_refuses_regardless_of_legacy_opt_in(tmp_path):
    """Parked (2026-10-25): the opt-in flag used to gate this pipeline no
    longer un-parks it; both forms now refuse with the same retirement
    message and a non-zero exit code."""
    script = REPO_ROOT / "scripts" / "tdnet_all_in_one.sh"
    no_opt_in = subprocess.run(["/bin/bash", str(script), "--dry-run"], cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    opted_in = subprocess.run(
        ["/bin/bash", str(script), "--legacy-local", "--dry-run", "--skip-postgres", "--skip-install", "--skip-scrape", "--skip-download", "--skip-parse", "--skip-parse-text", "--skip-tag"],
        cwd=tmp_path, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    for result in (no_opt_in, opted_in):
        assert result.returncode != 0
        assert "retired" in result.stdout
        assert "Root issue #50" in result.stdout


def test_all_in_one_with_ocr_refuses_regardless_of_legacy_opt_in(tmp_path):
    """Parked (2026-10-25): the wrapper delegates to tdnet_all_in_one.sh
    --legacy-local, which now always refuses, so no `tdnet` subcommand is
    ever reached by either script."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    wrapper = scripts_dir / "tdnet_all_in_one_with_ocr.sh"
    shutil.copy(REPO_ROOT / "scripts" / "tdnet_all_in_one_with_ocr.sh", wrapper)
    shutil.copy(REPO_ROOT / "scripts" / "tdnet_all_in_one.sh", scripts_dir / "tdnet_all_in_one.sh")

    for args in ([], ["--dry-run"], ["--legacy-local", "--days", "7"]):
        result = subprocess.run(
            ["/bin/bash", str(wrapper), *args],
            cwd=tmp_path,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert result.returncode != 0
        assert "retired" in result.stdout
        assert "Root issue #50" in result.stdout


def test_daily_metadata_default_research_root_is_canonical():
    script = (REPO_ROOT / "scripts" / "tdnet_daily_metadata.sh").read_text(encoding="utf-8")

    assert 'RESEARCH_ROOT="${RESEARCH_ROOT:-/Users/nrm176p/GitHub2/hedge-fund-takedo}"' in script
    assert "input-observations" not in script
