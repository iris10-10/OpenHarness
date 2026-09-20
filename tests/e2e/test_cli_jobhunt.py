"""Exercise the documented job-hunt CLI chain in an isolated workspace."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from openharness.cli import app

SAMPLE_DIR = Path(__file__).parents[2] / "examples" / "sample_data"


def test_cli_setup_import_search_and_match(tmp_path: Path, monkeypatch) -> None:
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    rag_dir = tmp_path / "rag"
    jobhunt_dir = data_dir / "jobhunt"
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("OPENHARNESS_JOBHUNT_DIR", str(jobhunt_dir))
    monkeypatch.setenv("OPENHARNESS_RAG_DATA_DIR", str(rag_dir))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENHARNESS_EMBEDDING_API_KEY", raising=False)

    runner = CliRunner()
    setup = runner.invoke(
        app,
        [
            "job-hunt",
            "setup",
            "--yes",
            "--rag-enabled",
            "--embedding-provider",
            "hash",
            "--cities",
            "杭州",
            "--positions",
            "Python 后端工程师",
            "--skills",
            "Python,FastAPI,MySQL,Redis",
        ],
        env={"NO_COLOR": "1", "COLUMNS": "160"},
    )
    assert setup.exit_code == 0, setup.output
    assert "Job-hunt setup complete" in setup.output

    imported = runner.invoke(
        app,
        [
            "job-hunt",
            "import",
            "--dir",
            str(SAMPLE_DIR / "jobs"),
            "--collection",
            "jobs",
            "--category",
            "后端开发",
        ],
        env={"NO_COLOR": "1", "COLUMNS": "160"},
    )
    assert imported.exit_code == 0, imported.output
    assert "processed=2" in imported.output
    assert "chunks=" in imported.output

    searched = runner.invoke(
        app,
        ["job-hunt", "search", "--query", "Python 后端", "--top", "3"],
        env={"NO_COLOR": "1", "COLUMNS": "160"},
    )
    assert searched.exit_code == 0, searched.output
    assert "云帆科技" in searched.output
    assert "Python 后端工程师" in searched.output

    matched = runner.invoke(
        app,
        [
            "job-hunt",
            "match",
            "--resume",
            str(SAMPLE_DIR / "resume.md"),
            "--jd",
            str(SAMPLE_DIR / "jobs" / "python-backend.md"),
        ],
        env={"NO_COLOR": "1", "COLUMNS": "160"},
    )
    assert matched.exit_code == 0, matched.output
    assert "Python 后端工程师" in matched.output
    assert "云帆科技" in matched.output or "杭州星辰科技有限公司" in matched.output
