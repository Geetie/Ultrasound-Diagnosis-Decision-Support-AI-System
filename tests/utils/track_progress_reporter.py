"""通用轨道进度报告器基类

基于 SEC 的 SECProgressReporter 提取的通用版本，
各轨道继承此类并定义自己的 PHASE_ICONS。
"""
import sys
import time


class TrackProgressReporter:
    """通用轨道测试进度报告器

    子类需覆盖 PHASE_ICONS 字典。
    在 conftest.py 的 pytest_configure 中注册：
        config.pluginmanager.register(OBSProgressReporter(), "obs_progress")
    """

    PHASE_ICONS = {"default": "●"}

    def __init__(self, track_name="TEST"):
        self.track_name = track_name
        self.total = 0
        self.completed = 0
        self.start_time = None
        self.current_phase = "default"
        self.phase_counts = {}
        self._last_print_len = 0

    def _classify_phase(self, name):
        lower = name.lower()
        for key in self.PHASE_ICONS:
            if key in lower:
                return key
        return "default"

    def pytest_collection_modifyitems(self, session, config, items):
        self.total = len(items)
        for item in items:
            phase = self._classify_phase(item.nodeid)
            self.phase_counts[phase] = self.phase_counts.get(phase, 0) + 1

    def pytest_runtest_logstart(self, nodeid, location):
        if self.start_time is None:
            self.start_time = time.time()
        self.current_phase = self._classify_phase(nodeid)

    def pytest_runtest_logfinish(self, nodeid, location):
        self.completed += 1
        if self.start_time is None:
            return

        elapsed = time.time() - self.start_time
        if self.completed > 0:
            avg = elapsed / self.completed
            remaining = avg * (self.total - self.completed)
        else:
            remaining = 0

        pct = self.completed / self.total * 100 if self.total > 0 else 0
        icon = self.PHASE_ICONS.get(self.current_phase, "●")
        test_name = location[2][:35] if len(location) > 2 else nodeid[:35]

        line = (
            f"  {icon} [{self.completed}/{self.total}] {pct:5.1f}% | "
            f"{test_name:<35} | "
            f"ETA: {remaining:6.1f}s | "
            f"\u23f1 {elapsed:6.1f}s"
        )

        pad = max(self._last_print_len - len(line), 0)
        sys.stderr.write(f"\r{line}{' ' * pad}")
        sys.stderr.flush()
        self._last_print_len = len(line)

    def pytest_sessionfinish(self, session, exitstatus):
        if self.start_time:
            total_time = time.time() - self.start_time
            sys.stderr.write(
                f"\n\n  \u2713 {self.track_name}\u6d4b\u8bd5\u5b8c\u6210: "
                f"{self.completed}/{self.total} \u901a\u8fc7, "
                f"\u603b\u8017\u65f6: {total_time:.1f}s\n"
            )

            if self.phase_counts:
                sys.stderr.write("  \u9636\u6bb5\u5206\u5e03:\n")
                for phase, count in sorted(
                    self.phase_counts.items(), key=lambda x: -x[1]
                ):
                    icon = self.PHASE_ICONS.get(phase, "●")
                    bar = "\u2588" * min(count, 40)
                    sys.stderr.write(f"    {icon} {phase:<20} {count:>4} {bar}\n")
            sys.stderr.write("\n")
            sys.stderr.flush()


class SourceChangeDetector:
    """通用源码变更检测器

    基于 SEC 的变更检测机制提取的通用版本。
    在 conftest.py 中使用：
        detector = SourceChangeDetector(cache_dir, source_files_map)
        detector.check_and_skip(items, config)
    """

    def __init__(self, cache_dir, source_files_map):
        self._cache_dir = cache_dir
        self._source_files_map = source_files_map
        self._project_root = self._find_project_root()

    def _find_project_root(self):
        import os
        d = os.path.dirname(os.path.abspath(self._cache_dir))
        while d != os.path.dirname(d):
            if os.path.exists(os.path.join(d, "pyproject.toml")):
                return d
            d = os.path.dirname(d)
        return d

    def _compute_file_hash(self, filepath):
        import hashlib
        h = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()
        except FileNotFoundError:
            return None

    def _compute_source_hash(self, source_files):
        import hashlib
        import os
        combined = ""
        for fp in source_files:
            full_path = fp if os.path.isabs(fp) else os.path.join(self._project_root, fp)
            h = self._compute_file_hash(full_path)
            if h is None:
                return None
            combined += h + ":"
        return hashlib.sha256(combined.encode()).hexdigest()

    def _load_hash_cache(self):
        import json
        import os
        cache_file = os.path.join(self._cache_dir, "source_hashes.json")
        try:
            if os.path.exists(cache_file):
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
        return {}

    def _save_hash_cache(self, cache):
        import json
        import os
        os.makedirs(self._cache_dir, exist_ok=True)
        cache_file = os.path.join(self._cache_dir, "source_hashes.json")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)

    def has_source_changed(self, key, source_files):
        current_hash = self._compute_source_hash(source_files)
        if current_hash is None:
            return True
        cache = self._load_hash_cache()
        cached_hash = cache.get(key)
        if cached_hash is None:
            cache[key] = current_hash
            self._save_hash_cache(cache)
            return True
        if cached_hash != current_hash:
            cache[key] = current_hash
            self._save_hash_cache(cache)
            return True
        return False

    def update_source_hash(self, key, source_files):
        current_hash = self._compute_source_hash(source_files)
        if current_hash is not None:
            cache = self._load_hash_cache()
            cache[key] = current_hash
            self._save_hash_cache(cache)

    def check_and_skip(self, items, config, env_var, marker_name, key, source_files):
        import os
        import pytest
        skip_enabled = os.environ.get(env_var, "false").lower() == "true"
        if not skip_enabled:
            return
        if not self.has_source_changed(key, source_files):
            self.update_source_hash(key, source_files)
            for item in items:
                if item.get_closest_marker(marker_name):
                    item.add_marker(
                        pytest.mark.skip(
                            reason=f"Source unchanged, set {env_var}=false to force run"
                        )
                    )
