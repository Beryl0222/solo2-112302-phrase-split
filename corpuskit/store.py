"""JSON 文件存储。

目录结构::

    <root>/
      terms.json  fragments.json  questions.json  edges.json
      coverage.json  plans.json  packages.json  annotations.json
      revisions.json  counters.json
      events.log            # 仅追加审计日志（JSON Lines）

所有写操作先写临时文件再原子替换，并向 events.log 追加一条事件。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .models import (
    Annotation,
    CoverageCell,
    DeliveryPackage,
    EvidenceEdge,
    Fragment,
    Plan,
    Question,
    SenseRevision,
    Term,
    now_iso,
)

COLLECTIONS: dict[str, type] = {
    "terms": Term,
    "fragments": Fragment,
    "questions": Question,
    "edges": EvidenceEdge,
    "coverage": CoverageCell,
    "plans": Plan,
    "packages": DeliveryPackage,
    "annotations": Annotation,
    "revisions": SenseRevision,
}

# 实体 ID 前缀
ID_PREFIX = {
    "terms": "t",
    "fragments": "f",
    "questions": "q",
    "edges": "e",
    "coverage": "c",
    "plans": "p",
    "packages": "d",
    "annotations": "a",
    "revisions": "r",
}


class Store:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, list[Any]] = {}
        for name, cls in COLLECTIONS.items():
            self._data[name] = self._load(name, cls)
        self._counters: dict[str, int] = self._read_json(
            self.root / "counters.json", default={}
        )

    # ------------------------------------------------------------------ IO
    def _path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def _load(self, name: str, cls: type) -> list[Any]:
        raw = self._read_json(self._path(name), default=[])
        return [cls.from_dict(item) for item in raw]

    def _atomic_write(self, path: Path, payload: Any) -> None:
        fd, tmp = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _persist(self, name: str) -> None:
        items = self._data[name]
        self._atomic_write(self._path(name), [item.to_dict() for item in items])

    def log_event(self, event_type: str, payload: dict[str, Any]) -> None:
        entry = {"at": now_iso(), "event": event_type, **payload}
        with (self.root / "events.log").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ---------------------------------------------------------------  ID 发放
    def next_id(self, collection: str) -> str:
        prefix = ID_PREFIX[collection]
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        self._atomic_write(self.root / "counters.json", self._counters)
        return f"{prefix}-{n}"

    # ------------------------------------------------------------- 集合访问
    @staticmethod
    def _key(collection: str, item: Any) -> str:
        # 标注按片段ID唯一，其余实体按自身 id
        if collection == "annotations":
            return item.fragment_id
        return item.id

    def all(self, collection: str) -> list[Any]:
        return list(self._data[collection])

    def get(self, collection: str, obj_id: str) -> Any:
        if collection == "annotations":
            hits = [x for x in self._data[collection]
                    if x.fragment_id == obj_id]
            if hits:
                return hits[0]
            raise KeyError(f"{collection} 中不存在 {obj_id}")
        for item in self._data[collection]:
            if item.id == obj_id:
                return item
        raise KeyError(f"{collection} 中不存在 {obj_id}")

    def find(self, collection: str, predicate) -> list[Any]:
        return [item for item in self._data[collection] if predicate(item)]

    def add(self, collection: str, item: Any, event: str | None = None) -> Any:
        key = self._key(collection, item)
        if any(self._key(collection, x) == key for x in self._data[collection]):
            raise ValueError(f"{collection} 已存在 {key}")
        self._data[collection].append(item)
        self._persist(collection)
        if event:
            self.log_event(event, {"id": key, "collection": collection})
        return item

    def upsert(self, collection: str, item: Any) -> None:
        items = self._data[collection]
        key = self._key(collection, item)
        for i, existing in enumerate(items):
            if self._key(collection, existing) == key:
                items[i] = item
                self._persist(collection)
                return
        items.append(item)
        self._persist(collection)

    def update(self, collection: str, obj_id: str, mutate, event: str | None = None) -> Any:
        item = self.get(collection, obj_id)
        mutate(item)
        self._persist(collection)
        if event:
            self.log_event(event, {"id": obj_id, "collection": collection})
        return item

    def replace_all(self, collection: str, items: Iterable[Any]) -> None:
        self._data[collection] = list(items)
        self._persist(collection)

    # ------------------------------------------------------------ 便捷查询
    def term_by_surface(self, surface: str) -> list[tuple[Term, str]]:
        """返回 (词目, 词义ID) 列表；同一字面命中多个词义时全部返回。"""
        out: list[tuple[Term, str]] = []
        for term in self.all("terms"):
            if surface in term.all_surfaces():
                for sense in term.senses:
                    out.append((term, sense.sense_id))
        return out

    def annotation_for(self, fragment_id: str) -> Annotation | None:
        hits = self.find("annotations", lambda a: a.fragment_id == fragment_id)
        return hits[0] if hits else None

    def packages_for(self, college: str, kind: str | None = None) -> list[DeliveryPackage]:
        return self.find(
            "packages",
            lambda p: p.college == college and (kind is None or p.kind == kind),
        )

    def active_revision(self, term_id: str) -> SenseRevision | None:
        hits = self.find(
            "revisions",
            lambda r: r.term_id == term_id and r.status == "active",
        )
        return hits[-1] if hits else None
