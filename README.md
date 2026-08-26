# Meng_CSVEditor

A lightweight CSV editor built with PyQt5, designed for viewing and editing game configuration files and other CSV data.

## Features

- **Three-layer architecture**: Clean separation of GUI (`csv_editor.py`), business logic (`csv_model.py`), and undo/redo commands (`csv_commands.py`)
- **Cell auto-expansion**: When entering edit mode, the editor automatically expands both horizontally (max 700px) and vertically (max 500px) to display full text content without scrolling
- **Tab for newlines**: Press `Tab` inside a cell to insert line breaks; `Ctrl+Enter` to commit changes
- **Row height state machine**: Automatically expands row height during editing and restores it when exiting edit mode
- **BOM-safe encoding**: Precisely detects file BOM to avoid corrupting game CSV files when saving
- **Find & Replace**: Full-featured find and replace dialog
- **Undo / Redo**: Complete undo/redo support powered by `QUndoStack`
- **Row & Column operations**: Insert/delete rows and columns
- **Sorting**: Click column headers to sort
- **Recent files**: Remembers recently opened files
- **Dark theme**: Modern dark Fusion UI out of the box

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Tab` (in cell) | Insert newline |
| `Ctrl+Enter` | Commit cell edit |
| `Esc` | Cancel cell edit |
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `Ctrl+F` | Find & Replace |
| `Ctrl+S` | Save |
| `Ctrl+Shift+S` | Save As |
| `Ctrl+O` | Open file |

## Build from Source

### Requirements

- Python 3.8+
- PyQt5

```bash
pip install PyQt5 pyinstaller
```

### Run directly

```bash
python csv_editor.py
```

### Build executable

```bash
python -m PyInstaller --onefile --windowed --name Meng_CSVEditor --hidden-import csv_model --hidden-import csv_commands csv_editor.py
```

Or use the included batch script on Windows:

```
build.bat
```

The executable will be generated at `dist/Meng_CSVEditor.exe`.

## Project Structure

```
Meng_CSVEditor/
├── csv_editor.py          # GUI layer (main window, delegate, dialogs)
├── csv_model.py           # Business logic layer (CSV I/O, table model)
├── csv_commands.py        # Command layer (undo/redo commands)
├── Meng_CSVEditor.spec    # PyInstaller configuration
├── build.bat              # One-click build script (Windows)
└── README.md
```

## License

MIT License — see [LICENSE](LICENSE) for details.
