"""Content-addressable cache — hash-based dedup for downloaded media."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("mediaforge.cache")


class MediaCache:
    """SHA256-keyed file cache with metadata sidecar and TTL eviction."""

    def __init__(self, root: Path, max_gb: float = 10.0, ttl_days: int = 30):
        self.root = root
        self.max_bytes = int(max_gb * 1024 ** 3)
        self.ttl_seconds = ttl_days * 86400
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()

    def _shard(self, key: str) -> Path:
        return self.root / key[:2] / key[2:4]

    def _path(self, key: str, ext: str = "") -> Path:
        d = self._shard(key)
        return d / f"{key}{ext}"

    def has(self, url: str) -> bool:
        key = self._key(url)
        meta = self._path(key, ".json")
        if not meta.exists():
            return False
        try:
            m = json.loads(meta.read_text())
            if time.time() - m.get("ts", 0) > self.ttl_seconds:
                self._evict(key)
                return False
            return self._path(key, m.get("ext", "")).exists()
        except Exception:
            return False

    def get(self, url: str) -> Optional[str]:
        key = self._key(url)
        meta = self._path(key, ".json")
        if not meta.exists():
            return None
        try:
            m = json.loads(meta.read_text())
            p = self._path(key, m.get("ext", ""))
            if p.exists():
                meta.write_text(json.dumps({**m, "ts": time.time()}))
                return str(p)
        except Exception:
            pass
        return None

    def put(self, url: str, src_path: str, metadata: Optional[dict] = None) -> str:
        key = self._key(url)
        ext = Path(src_path).suffix or ".bin"
        d = self._shard(key)
        d.mkdir(parents=True, exist_ok=True)
        dest = self._path(key, ext)
        shutil.copy2(src_path, dest)
        meta = {
            "url": url,
            "ext": ext,
            "ts": time.time(),
            "size": os.path.getsize(str(dest)),
            **(metadata or {}),
        }
        self._path(key, ".json").write_text(json.dumps(meta))
        return str(dest)

    def put_bytes(self, url: str, data: bytes, ext: str = ".bin",
                  metadata: Optional[dict] = None) -> str:
        key = self._key(url)
        d = self._shard(key)
        d.mkdir(parents=True, exist_ok=True)
        dest = self._path(key, ext)
        dest.write_bytes(data)
        meta = {
            "url": url,
            "ext": ext,
            "ts": time.time(),
            "size": len(data),
            **(metadata or {}),
        }
        self._path(key, ".json").write_text(json.dumps(meta))
        return str(dest)

    def _evict(self, key: str):
        d = self._shard(key)
        for f in d.glob(f"{key}*"):
            f.unlink(missing_ok=True)

    def cleanup(self):
        now = time.time()
        total = 0
        files = []
        for meta_path in self.root.rglob("*.json"):
            try:
                m = json.loads(meta_path.read_text())
                data_path = meta_path.with_suffix(m.get("ext", ".bin"))
                age = now - m.get("ts", 0)
                if age > self.ttl_seconds:
                    meta_path.unlink(missing_ok=True)
                    data_path.unlink(missing_ok=True)
                    continue
                sz = m.get("size", 0)
                total += sz
                files.append((m.get("ts", 0), sz, meta_path, data_path))
            except Exception:
                continue

        if total > self.max_bytes:
            files.sort()
            for ts, sz, mp, dp in files:
                if total <= self.max_bytes * 0.8:
                    break
                mp.unlink(missing_ok=True)
                dp.unlink(missing_ok=True)
                total -= sz
            log.info("cache cleanup: %d MB remaining", total // (1024 * 1024))
