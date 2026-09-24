"""通用轨道进度报告器和源码变更检测器的单元测试

覆盖：
1. TrackProgressReporter - 进度可视化报告器
2. SourceChangeDetector - 源码变更检测器
"""
import json
import os
import tempfile
import time

from tests.utils.track_progress_reporter import SourceChangeDetector, TrackProgressReporter


class TestTrackProgressReporter:
    def test_init_defaults(self):
        reporter = TrackProgressReporter(track_name="TEST")
        assert reporter.track_name == "TEST"
        assert reporter.total == 0
        assert reporter.completed == 0
        assert reporter.start_time is None
        assert reporter.phase_counts == {}

    def test_classify_phase_default(self):
        reporter = TrackProgressReporter(track_name="TEST")
        assert reporter._classify_phase("some_random_test") == "default"

    def test_classify_phase_matching(self):
        class TestReporter(TrackProgressReporter):
            PHASE_ICONS = {"auth": "\U0001f510", "hsm": "\U0001f511", "default": "\u25cf"}

        reporter = TestReporter(track_name="TEST")
        assert reporter._classify_phase("test_auth_login") == "auth"
        assert reporter._classify_phase("test_hsm_key_generation") == "hsm"
        assert reporter._classify_phase("test_something_else") == "default"

    def test_classify_phase_first_match_wins(self):
        class TestReporter(TrackProgressReporter):
            PHASE_ICONS = {"auth": "\U0001f510", "auth_token": "\U0001f511", "default": "\u25cf"}

        reporter = TestReporter(track_name="TEST")
        result = reporter._classify_phase("test_auth_token_refresh")
        assert result == "auth"

    def test_classify_phase_case_insensitive(self):
        class TestReporter(TrackProgressReporter):
            PHASE_ICONS = {"auth": "\U0001f510", "default": "\u25cf"}

        reporter = TestReporter(track_name="TEST")
        assert reporter._classify_phase("test_Auth_Login") == "auth"
        assert reporter._classify_phase("TEST_AUTH_UPPER") == "auth"

    def test_pytest_collection_modifyitems(self):
        reporter = TrackProgressReporter(track_name="TEST")

        class FakeItem:
            def __init__(self, nodeid):
                self.nodeid = nodeid

        items = [FakeItem("test_a"), FakeItem("test_b"), FakeItem("test_c")]
        reporter.pytest_collection_modifyitems(None, None, items)
        assert reporter.total == 3
        assert reporter.phase_counts.get("default") == 3

    def test_pytest_collection_modifyitems_phase_classification(self):
        class TestReporter(TrackProgressReporter):
            PHASE_ICONS = {"auth": "\U0001f510", "token": "\U0001f511", "default": "\u25cf"}

        reporter = TestReporter(track_name="TEST")

        class FakeItem:
            def __init__(self, nodeid):
                self.nodeid = nodeid

        items = [
            FakeItem("test_auth_login"),
            FakeItem("test_token_refresh"),
            FakeItem("test_auth_logout"),
            FakeItem("test_other"),
        ]
        reporter.pytest_collection_modifyitems(None, None, items)
        assert reporter.total == 4
        assert reporter.phase_counts.get("auth") == 2
        assert reporter.phase_counts.get("token") == 1
        assert reporter.phase_counts.get("default") == 1

    def test_pytest_runtest_logstart_sets_start_time(self):
        reporter = TrackProgressReporter(track_name="TEST")
        assert reporter.start_time is None
        reporter.pytest_runtest_logstart("test_a", ("test_file.py", None, "test_a"))
        assert reporter.start_time is not None

    def test_pytest_runtest_logstart_only_sets_once(self):
        reporter = TrackProgressReporter(track_name="TEST")
        reporter.pytest_runtest_logstart("test_a", ("f.py", None, "test_a"))
        first_time = reporter.start_time
        time.sleep(0.01)
        reporter.pytest_runtest_logstart("test_b", ("f.py", None, "test_b"))
        assert reporter.start_time == first_time

    def test_pytest_runtest_logfinish_increments_completed(self):
        reporter = TrackProgressReporter(track_name="TEST")
        reporter.total = 3
        reporter.pytest_runtest_logstart("test_a", ("f.py", None, "test_a"))
        reporter.pytest_runtest_logfinish("test_a", ("f.py", None, "test_a"))
        assert reporter.completed == 1
        reporter.pytest_runtest_logfinish("test_b", ("f.py", None, "test_b"))
        assert reporter.completed == 2

    def test_pytest_runtest_logfinish_no_start_time(self):
        reporter = TrackProgressReporter(track_name="TEST")
        reporter.total = 3
        reporter.pytest_runtest_logfinish("test_a", ("f.py", None, "test_a"))
        assert reporter.completed == 1


class TestSourceChangeDetector:
    def test_compute_file_hash_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("print('hello')")
            f.flush()
            path = f.name

        try:
            detector = SourceChangeDetector(
                os.path.dirname(path), {"test": [path]}
            )
            h = detector._compute_file_hash(path)
            assert h is not None
            assert len(h) == 64
        finally:
            os.unlink(path)

    def test_compute_file_hash_nonexistent_file(self):
        detector = SourceChangeDetector("/tmp", {"test": ["/nonexistent/file.py"]})
        h = detector._compute_file_hash("/nonexistent/file.py")
        assert h is None

    def test_compute_source_hash_all_exist(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f1:
            f1.write("a = 1")
            f1.flush()
            path1 = f1.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f2:
            f2.write("b = 2")
            f2.flush()
            path2 = f2.name

        try:
            detector = SourceChangeDetector(
                os.path.dirname(path1), {"test": [path1, path2]}
            )
            h = detector._compute_source_hash([path1, path2])
            assert h is not None
            assert len(h) == 64
        finally:
            os.unlink(path1)
            os.unlink(path2)

    def test_compute_source_hash_one_missing(self):
        detector = SourceChangeDetector("/tmp", {"test": ["/nonexistent.py"]})
        h = detector._compute_source_hash(["/nonexistent.py"])
        assert h is None

    def test_has_source_changed_first_run(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write("x = 1")
                f.flush()
                path = f.name

            try:
                detector = SourceChangeDetector(cache_dir, {"test": [path]})
                assert detector.has_source_changed("test", [path]) is True
            finally:
                os.unlink(path)

    def test_has_source_changed_no_change(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write("x = 1")
                f.flush()
                path = f.name

            try:
                detector = SourceChangeDetector(cache_dir, {"test": [path]})
                detector.has_source_changed("test", [path])
                assert detector.has_source_changed("test", [path]) is False
            finally:
                os.unlink(path)

    def test_has_source_changed_file_modified(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write("x = 1")
                f.flush()
                path = f.name

            try:
                detector = SourceChangeDetector(cache_dir, {"test": [path]})
                detector.has_source_changed("test", [path])

                with open(path, "w") as f:
                    f.write("x = 2")

                assert detector.has_source_changed("test", [path]) is True
            finally:
                os.unlink(path)

    def test_has_source_changed_file_deleted(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write("x = 1")
                f.flush()
                path = f.name

            try:
                detector = SourceChangeDetector(cache_dir, {"test": [path]})
                detector.has_source_changed("test", [path])

                os.unlink(path)
                assert detector.has_source_changed("test", [path]) is True
            except:
                if os.path.exists(path):
                    os.unlink(path)

    def test_update_source_hash(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write("x = 1")
                f.flush()
                path = f.name

            try:
                detector = SourceChangeDetector(cache_dir, {"test": [path]})
                detector.update_source_hash("test", [path])

                cache_file = os.path.join(cache_dir, "source_hashes.json")
                assert os.path.exists(cache_file)
                with open(cache_file) as cf:
                    data = json.load(cf)
                assert "test" in data
            finally:
                os.unlink(path)

    def test_check_and_skip_no_env_var(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            detector = SourceChangeDetector(cache_dir, {"test": []})

            class FakeItem:
                def __init__(self):
                    self._markers = []

                def get_closest_marker(self, name):
                    return None

                def add_marker(self, marker):
                    self._markers.append(marker)

            items = [FakeItem()]
            original_env = os.environ.get("SKIP_OBS_UNCHANGED")
            try:
                os.environ.pop("SKIP_OBS_UNCHANGED", None)
                detector.check_and_skip(
                    items, None,
                    env_var="SKIP_OBS_UNCHANGED",
                    marker_name="obs_heavy",
                    key="test",
                    source_files=[],
                )
                assert len(items[0]._markers) == 0
            finally:
                if original_env is not None:
                    os.environ["SKIP_OBS_UNCHANGED"] = original_env

    def test_cache_persistence(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write("x = 1")
                f.flush()
                path = f.name

            try:
                detector1 = SourceChangeDetector(cache_dir, {"test": [path]})
                detector1.has_source_changed("test", [path])

                detector2 = SourceChangeDetector(cache_dir, {"test": [path]})
                assert detector2.has_source_changed("test", [path]) is False
            finally:
                os.unlink(path)

    def test_corrupted_cache_handled(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            cache_file = os.path.join(cache_dir, "source_hashes.json")
            with open(cache_file, "w") as f:
                f.write("not valid json{{{")

            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write("x = 1")
                f.flush()
                path = f.name

            try:
                detector = SourceChangeDetector(cache_dir, {"test": [path]})
                assert detector.has_source_changed("test", [path]) is True
            finally:
                os.unlink(path)
