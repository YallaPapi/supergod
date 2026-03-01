"""Tests for curated agent importer."""

from pathlib import Path

from supergod.skills import importer


def test_importer_writes_index_and_normalizes_paths(tmp_path, monkeypatch):
    source = tmp_path / "agents"
    source.mkdir(parents=True)
    (source / "prd-analyzer.md").write_text(
        "---\n"
        "name: prd-analyzer\n"
        "description: Parse requirements.\n"
        "---\n\n"
        "Use path C:\\Users\\asus\\Desktop\\projects\\i2v\\docs\\prd.md\n"
        "ALWAYS capture acceptance criteria.\n",
        encoding="utf-8",
    )

    library_dir = tmp_path / "library"
    agents_dir = library_dir / "agents"
    index_path = library_dir / "index.json"
    monkeypatch.setattr(importer, "LIBRARY_DIR", library_dir)
    monkeypatch.setattr(importer, "AGENTS_DIR", agents_dir)
    monkeypatch.setattr(importer, "INDEX_PATH", index_path)

    result = importer.import_curated_agents(
        source_dir=str(source),
        include_project_specific=False,
    )

    assert result["stats"]["total_skills"] == 1
    assert index_path.exists()
    imported_file = agents_dir / "prd-analyzer.md"
    assert imported_file.exists()
    text = imported_file.read_text(encoding="utf-8")
    assert "{PROJECT_ROOT}" in text
    assert "ALWAYS capture acceptance criteria." in text
