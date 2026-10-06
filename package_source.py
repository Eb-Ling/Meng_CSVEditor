"""Create a source-only archive, excluding executables and local dependencies."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parent / "Meng_CSVEditor_source_improved.zip"


def main():
    required = [
        "csv_editor.py", "csv_model.py", "csv_commands.py", "ui_theme.py", "crash_reporter.py",
        "README.md", "BOUNDARY_AUDIT.md", "RELEASE_NOTES.md", "LICENSE",
        "requirements.txt", "requirements-build.txt", "Meng_CSVEditor.spec",
        "assets/app.ico",
        "build.bat", "build.ps1", ".gitignore", "package_source.py",
        "recovery/RECOVERY_REPORT.md", "recovery/source_comparison.json",
        "recovery/boundary-tests.log", "recovery/boundary-validation.json",
        "recovery/archive_manifest.json", "recovery/inspect_archive.py",
        "recovery/compare_source.py",
    ]
    paths = [ROOT / relative for relative in required]
    paths.extend(sorted((ROOT / "tests").glob("*.py")))
    for name in ("csv_editor", "csv_model", "csv_commands"):
        paths.extend([
            ROOT / "recovery" / "existing-source" / f"{name}.py",
            ROOT / "recovery" / "extracted" / name,
            ROOT / "recovery" / "extracted" / f"{name}.pyc",
            ROOT / "recovery" / f"{name}.shipped-code.json",
        ])
    missing = [str(path.relative_to(ROOT)) for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing source archive inputs: {missing}")
    if not any(path.parent.name == "tests" for path in paths):
        raise FileNotFoundError("No regression tests found")
    with ZipFile(OUTPUT, "w", compression=ZIP_DEFLATED) as archive:
        for path in paths:
            archive.write(path, "Meng_CSVEditor/" + path.relative_to(ROOT).as_posix())
    with ZipFile(OUTPUT) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"Corrupt archive entry: {bad}")
    print(f"Source archive: {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
