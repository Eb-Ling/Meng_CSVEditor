"""Compare shipped code objects with existing source without importing either."""
from pathlib import Path
import hashlib
import json
import marshal
import shutil
import sys
import types

ROOT = Path(__file__).resolve().parent
FIELDS = ("co_name", "co_qualname", "co_filename", "co_argcount", "co_posonlyargcount",
          "co_kwonlyargcount", "co_nlocals", "co_flags", "co_names",
          "co_varnames", "co_freevars", "co_cellvars", "co_code",
          "co_stacksize", "co_firstlineno", "co_linetable", "co_exceptiontable")


def normalize(value):
    if isinstance(value, types.CodeType):
        result = {field: normalize(getattr(value, field)) for field in FIELDS}
        result["co_consts"] = [normalize(v) for v in value.co_consts]
        return result
    if isinstance(value, (list, tuple)):
        return [normalize(v) for v in value]
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    if isinstance(value, frozenset):
        return {"frozenset": sorted(normalize(v) for v in value)}
    return value


def differences(a, b, path):
    if type(a) is not type(b):
        return [path + ": types differ"]
    if isinstance(a, dict):
        results = []
        for key in a.keys() | b.keys():
            if key not in a or key not in b:
                results.append(path + "/" + key + ": missing")
            else:
                results.extend(differences(a[key], b[key], path + "/" + key))
        return results
    if isinstance(a, list):
        if len(a) != len(b):
            return [path + ": length differs"]
        return [item for i, (x, y) in enumerate(zip(a, b))
                for item in differences(x, y, path + "/" + str(i))]
    return [] if a == b else [path + ": " + repr(a) + " != " + repr(b)]


def main():
    backup = ROOT / "existing-source"
    backup.mkdir(exist_ok=True)
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Use Python 3.13 to compare shipped bytecode accurately")
    report = {"comparison_runtime": sys.version, "modules": {}}
    for name in ("csv_editor", "csv_model", "csv_commands"):
        source_path = ROOT.parent / (name + ".py")
        if not (backup / source_path.name).exists():
            shutil.copy2(source_path, backup / source_path.name)
        source = (backup / source_path.name).read_bytes()
        shipped = marshal.loads((ROOT / "extracted" / name).read_bytes())
        local = compile(source, source_path.name, "exec", optimize=0)
        normalized_shipped = normalize(shipped)
        normalized_local = normalize(local)
        diff = differences(normalized_shipped, normalized_local, name)
        def count_code_objects(code):
            return 1 + sum(count_code_objects(c) for c in code.co_consts
                           if isinstance(c, types.CodeType))
        report["modules"][name] = dict(source_sha256=hashlib.sha256(source).hexdigest(),
                            code_objects_equal=not diff,
                            compared_code_object_count=count_code_objects(shipped),
                            differences=diff,
                            shipped_firstlineno=shipped.co_firstlineno,
                            local_firstlineno=local.co_firstlineno)
        (ROOT / (name + ".shipped-code.json")).write_text(
            json.dumps(normalized_shipped, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "source_comparison.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
