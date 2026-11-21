"""Load a MOCK/SAMPLE dataset directory and apply labeled eval patches in memory.

A dataset is a directory with manifest.json naming its files. Eval cases are written as
patches against a clean dataset (set a cell, delete rows, append a row, rename or add a
column, edit JSON), so each planted defect is visible in samples/evals/cases.json.
"""

from __future__ import annotations

import copy
import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Dataset:
    name: str
    manifest: dict
    files: dict[str, str] = field(default_factory=dict)  # filename -> text

    def filename(self, key: str) -> str:
        return self.manifest["files"][key]

    def text(self, key: str) -> str:
        return self.files[self.filename(key)]

    def json(self, key: str) -> dict:
        return json.loads(self.text(key))

    def copy(self, name: str | None = None) -> "Dataset":
        return Dataset(name or self.name, copy.deepcopy(self.manifest), dict(self.files))


def load_dataset(path: str | Path) -> Dataset:
    p = Path(path)
    manifest = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
    files = {}
    for fn in manifest["files"].values():
        files[fn] = (p / fn).read_text(encoding="utf-8")
    for fn in manifest.get("extra_files", {}).values():
        files[fn] = (p / fn).read_text(encoding="utf-8")
    return Dataset(p.name, manifest, files)


class PatchError(ValueError):
    pass


def _csv_load(text: str) -> tuple[list[str], list[dict[str, str]]]:
    rows = list(csv.reader(io.StringIO(text)))
    headers = rows[0]
    return headers, [dict(zip(headers, r + [""] * (len(headers) - len(r)))) for r in rows[1:] if any(r)]


def _csv_dump(headers: list[str], rows: list[dict[str, str]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(headers)
    for r in rows:
        w.writerow([r.get(h, "") for h in headers])
    return buf.getvalue()


def _matches(row: dict[str, str], where: dict[str, str]) -> bool:
    return all(row.get(k, "") == v for k, v in where.items())


def apply_patches(ds: Dataset, patches: list[dict], name: str) -> Dataset:
    out = ds.copy(name)
    for p in patches:
        op, fn = p["op"], p["file"]
        if fn not in out.files:
            raise PatchError(f"unknown file {fn}")
        if op.startswith("json_"):
            doc = json.loads(out.files[fn])
            node = doc
            for k in p["path"][:-1]:
                node = node[k]
            last = p["path"][-1]
            if op == "json_set":
                node[last] = p["value"]
            elif op == "json_append":
                node[last].append(p["value"])
            else:
                raise PatchError(op)
            out.files[fn] = json.dumps(doc, indent=2, ensure_ascii=False)
            continue
        headers, rows = _csv_load(out.files[fn])
        if op == "set":
            hit = [r for r in rows if _matches(r, p["where"])]
            if not hit:
                raise PatchError(f"set: no row in {fn} matches {p['where']}")
            for r in hit:
                r.update(p["set"])
        elif op == "delete":
            before = len(rows)
            rows = [r for r in rows if not _matches(r, p["where"])]
            if len(rows) == before:
                raise PatchError(f"delete: no row in {fn} matches {p['where']}")
        elif op == "append":
            rows.append(dict(p["row"]))
        elif op == "add_column":
            headers.append(p["column"])
            for r in rows:
                r[p["column"]] = p.get("default", "")
        elif op == "drop_column":
            if p["column"] not in headers:
                raise PatchError(f"drop_column: {p['column']} not in {fn}")
            headers.remove(p["column"])
            for r in rows:
                r.pop(p["column"], None)
        elif op == "rename_column":
            i = headers.index(p["old"])
            headers[i] = p["new"]
            for r in rows:
                r[p["new"]] = r.pop(p["old"])
        else:
            raise PatchError(f"unknown op {op}")
        out.files[fn] = _csv_dump(headers, rows)
    return out
