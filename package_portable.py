"""Deliver the GUI and stdio companion together, with relocatable examples."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    root = Path(__file__).resolve().parent
    paths = [root / 'dist/Meng_CSVEditor.exe', root / 'dist/Meng_CSVEditor_MCP.exe',
             root / 'README.md', root / 'LICENSE', root / 'assets/editor-preview.png']
    paths.extend(sorted((root / 'examples').glob('mcp-*.json')))
    paths.extend(sorted((root / 'examples').glob('mcp-*.toml')))
    paths.append(root / 'examples/demo.csv')
    output = root.parent / 'Meng_CSVEditor_with_MCP.zip'
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(path)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for path in paths:
            relative = path.name if path.parent.name == 'dist' else path.relative_to(root).as_posix()
            archive.write(path, 'Meng_CSVEditor/' + relative)
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise RuntimeError('Portable archive verification failed')
    print(f'Portable package: {output} ({output.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
