import subprocess
from pathlib import Path

from scripts import build_submission


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def test_included_files_only_returns_tracked_working_tree_files(
    tmp_path: Path, monkeypatch
) -> None:
    repo = tmp_path / "repo"
    artifacts = repo / "benchmarks" / "artifacts"
    artifacts.mkdir(parents=True)
    (repo / ".gitignore").write_text(
        "benchmarks/artifacts/latest.*\n",
        encoding="utf-8",
    )
    readme = repo / "README.md"
    readme.write_text("indexed\n", encoding="utf-8")
    canonical = artifacts / "canonical.json"
    canonical.write_text("{}\n", encoding="utf-8")

    _git(repo, "init", "-q")
    _git(repo, "add", ".gitignore", "README.md", "benchmarks/artifacts/canonical.json")

    # The package must use the current bytes of a tracked dirty file, not the
    # staged blob, while excluding both ignored and ordinary untracked files.
    readme.write_text("dirty tracked working tree\n", encoding="utf-8")
    (artifacts / "latest.json").write_text("ignored\n", encoding="utf-8")
    (artifacts / "notes.md").write_text("untracked\n", encoding="utf-8")
    monkeypatch.setattr(build_submission, "ROOT", repo)

    included = set(build_submission.included_files())

    assert readme in included
    assert readme.read_text(encoding="utf-8") == "dirty tracked working tree\n"
    assert canonical in included
    assert artifacts / "latest.json" not in included
    assert artifacts / "notes.md" not in included
