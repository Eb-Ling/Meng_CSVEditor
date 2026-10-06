"""Read PyInstaller archive without running any code from the executable."""
from pathlib import Path
import argparse
import ctypes
import hashlib
import json
import marshal
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parent
EXE = ROOT.parent / "Meng_CSVEditor.exe"
MAGIC = b"MEI\x0c\x0b\x0a\x0b\x0e"


def windows_file_version(path):
    """Read PE version resources via Windows; never load the target DLL."""
    api = ctypes.WinDLL("version", use_last_error=True)
    api.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
    api.GetFileVersionInfoSizeW.restype = ctypes.c_ulong
    api.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong,
                                        ctypes.c_ulong, ctypes.c_void_p]
    api.GetFileVersionInfoW.restype = ctypes.c_int
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                   ctypes.POINTER(ctypes.c_void_p),
                                   ctypes.POINTER(ctypes.c_uint)]
    api.VerQueryValueW.restype = ctypes.c_int
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buf = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, buf):
        raise ctypes.WinError(ctypes.get_last_error())
    value = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not api.VerQueryValueW(buf, "\\", ctypes.byref(value), ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    fields = struct.unpack("<13I", ctypes.string_at(value, 52))
    result = {"fixed_file_version": ".".join(map(str, (
        fields[2] >> 16, fields[2] & 0xFFFF, fields[3] >> 16, fields[3] & 0xFFFF)))}
    if api.VerQueryValueW(buf, "\\VarFileInfo\\Translation",
                         ctypes.byref(value), ctypes.byref(length)):
        lang, codepage = struct.unpack("<HH", ctypes.string_at(value, 4))
        for key in ("FileVersion", "ProductVersion"):
            resource = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\{key}"
            if api.VerQueryValueW(buf, resource, ctypes.byref(value), ctypes.byref(length)):
                result[key] = ctypes.wstring_at(value)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-metadata", action="store_true",
                        help="Also extract python313.dll and read PE version on Windows")
    args = parser.parse_args()
    data = EXE.read_bytes()
    cookie_offset = data.rfind(MAGIC)
    if cookie_offset < 0:
        raise ValueError("No PyInstaller cookie found")
    magic, package_size, toc_offset, toc_size, python_version, python_library = (
        struct.unpack("!8sIIII64s", data[cookie_offset:cookie_offset + 88])
    )
    archive_start = cookie_offset + 88 - package_size
    toc_start = archive_start + toc_offset
    toc = data[toc_start:toc_start + toc_size]
    entries = []
    pos = 0
    out = ROOT / "extracted"
    out.mkdir(exist_ok=True)
    while pos < len(toc):
        size, offset, compressed, uncompressed, compression_flag, typecode = (
            struct.unpack("!IIIIBc", toc[pos:pos + 18])
        )
        name = toc[pos + 18:pos + size].rstrip(b"\x00").decode("utf-8")
        entry = dict(name=name, typecode=typecode.decode(), offset=offset,
                     compressed_size=compressed, size=uncompressed,
                     compressed=bool(compression_flag))
        entries.append(entry)
        extract_names = ("csv_editor", "PYZ.pyz")
        if args.runtime_metadata:
            extract_names += ("python313.dll",)
        if name in extract_names:
            raw = data[archive_start + offset:archive_start + offset + compressed]
            if compression_flag:
                raw = zlib.decompress(raw)
            if len(raw) != uncompressed:
                raise ValueError(f"Invalid entry length: {name}")
            (out / name).write_bytes(raw)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
        pos += size
    report = dict(executable=str(EXE), sha256=hashlib.sha256(data).hexdigest(),
                  size=len(data), cookie_offset=cookie_offset,
                  archive_start=archive_start, python_version=python_version,
                  python_library=python_library.rstrip(b"\x00").decode("utf-8"),
                  entries=entries)
    if args.runtime_metadata:
        report["python_runtime_version_resources"] = windows_file_version(out / "python313.dll")
    pyz_path = out / "PYZ.pyz"
    if pyz_path.exists():
        pyz = pyz_path.read_bytes()
        report["pyz_magic"] = pyz[4:8].hex()
        pyz_toc_offset = struct.unpack("!i", pyz[8:12])[0]
        # TOC contains only strings and integers; marshal never executes code.
        pyz_toc = dict(marshal.loads(pyz[pyz_toc_offset:]))
        report["application_modules"] = {}
        for name in ("csv_model", "csv_commands"):
            if name not in pyz_toc:
                continue
            typecode, offset, length = pyz_toc[name]
            raw = zlib.decompress(pyz[offset:offset + length])
            (out / name).write_bytes(raw)
            report["application_modules"][name] = dict(
                typecode=typecode, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        for name in ("csv_editor", "csv_model", "csv_commands"):
            raw_path = out / name
            if raw_path.exists():
                (out / (name + ".pyc")).write_bytes(pyz[4:8] + bytes(12) + raw_path.read_bytes())
    (ROOT / "archive_manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items()
                      if key != "entries"}, ensure_ascii=False, indent=2))
    print("entry_count", len(entries))


if __name__ == "__main__":
    main()
