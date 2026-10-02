import threading
from unittest.mock import MagicMock

import pytest

from safety_gateway.registry import SGComponentRegistry, sg_registry


class TestSGComponentRegistry:

    def setup_method(self):
        self.registry = SGComponentRegistry()

    def test_register_and_get(self):
        self.registry.register("widget", lambda: "hello")
        assert self.registry.get("widget") == "hello"

    def test_lazy_initialization(self):
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return "created"

        self.registry.register("lazy", factory)
        assert call_count == 0
        self.registry.get("lazy")
        assert call_count == 1

    def test_repeated_get_returns_same_instance(self):
        self.registry.register("singleton", lambda: object())
        instance1 = self.registry.get("singleton")
        instance2 = self.registry.get("singleton")
        assert instance1 is instance2

    def test_reset_single(self):
        self.registry.register("a", lambda: object())
        self.registry.register("b", lambda: object())
        a1 = self.registry.get("a")
        b1 = self.registry.get("b")
        self.registry.reset("a")
        a2 = self.registry.get("a")
        b2 = self.registry.get("b")
        assert a1 is not a2
        assert b1 is b2

    def test_reset_all(self):
        self.registry.register("x", lambda: object())
        self.registry.register("y", lambda: object())
        x1 = self.registry.get("x")
        y1 = self.registry.get("y")
        self.registry.reset()
        x2 = self.registry.get("x")
        y2 = self.registry.get("y")
        assert x1 is not x2
        assert y1 is not y2

    def test_get_unregistered_raises_key_error(self):
        with pytest.raises(KeyError, match="not registered"):
            self.registry.get("nonexistent")

    def test_factory_receives_no_args(self):
        factory = MagicMock(return_value=42)
        self.registry.register("num", factory)
        self.registry.get("num")
        factory.assert_called_once_with()

    def test_register_replaces_factory_and_clears_instance(self):
        self.registry.register("val", lambda: "old")
        assert self.registry.get("val") == "old"
        self.registry.register("val", lambda: "new")
        assert self.registry.get("val") == "new"


class TestRegistryConcurrency:

    def test_concurrent_get(self):
        registry = SGComponentRegistry()
        registry.register("shared", lambda: object())
        results = []
        errors = []

        def worker():
            try:
                results.append(registry.get("shared"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(r is results[0] for r in results)

    def test_concurrent_register_and_get(self):
        registry = SGComponentRegistry()
        errors = []

        def register_worker():
            try:
                for i in range(10):
                    registry.register(f"comp_{i}", lambda i=i: f"val_{i}")
            except Exception as e:
                errors.append(e)

        def get_worker():
            try:
                for i in range(10):
                    try:
                        registry.get(f"comp_{i}")
                    except KeyError:
                        pass
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=register_worker),
            threading.Thread(target=get_worker),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0

    def test_concurrent_reset_and_get(self):
        registry = SGComponentRegistry()
        registry.register("resilient", lambda: "alive")
        errors = []

        def reset_worker():
            try:
                for _ in range(10):
                    registry.reset("resilient")
            except Exception as e:
                errors.append(e)

        def get_worker():
            try:
                for _ in range(10):
                    val = registry.get("resilient")
                    assert val == "alive"
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=reset_worker),
            threading.Thread(target=get_worker),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestRegistryFactoryOverride:

    def setup_method(self):
        self.registry = SGComponentRegistry()

    def test_override_returns_mock(self):
        self.registry.register("svc", lambda: "real")
        self.registry.override("svc", lambda: "mock")
        assert self.registry.get("svc") == "mock"

    def test_restore_returns_original(self):
        self.registry.register("svc", lambda: "real")
        self.registry.override("svc", lambda: "mock")
        assert self.registry.get("svc") == "mock"
        self.registry.restore("svc")
        assert self.registry.get("svc") == "real"

    def test_override_does_not_affect_other_components(self):
        self.registry.register("a", lambda: "real_a")
        self.registry.register("b", lambda: "real_b")
        self.registry.override("a", lambda: "mock_a")
        assert self.registry.get("a") == "mock_a"
        assert self.registry.get("b") == "real_b"

    def test_override_unregistered_raises_key_error(self):
        with pytest.raises(KeyError, match="Cannot override"):
            self.registry.override("ghost", lambda: "mock")


class TestRegistryIntrospection:

    def setup_method(self):
        self.registry = SGComponentRegistry()

    def test_is_registered(self):
        self.registry.register("known", lambda: None)
        assert self.registry.is_registered("known") is True
        assert self.registry.is_registered("unknown") is False

    def test_is_initialized(self):
        self.registry.register("lazy", lambda: 42)
        assert self.registry.is_initialized("lazy") is False
        self.registry.get("lazy")
        assert self.registry.is_initialized("lazy") is True

    def test_list_registered(self):
        self.registry.register("a", lambda: 1)
        self.registry.register("b", lambda: 2)
        assert sorted(self.registry.list_registered()) == ["a", "b"]


class TestGlobalRegistry:

    def test_sg_registry_is_instance(self):
        assert isinstance(sg_registry, SGComponentRegistry)


class TestSingletonMigration:

    def test_contraindication_via_registry(self):
        from safety_gateway.contraindication import _reset_contraindication_engine, get_contraindication_engine
        _reset_contraindication_engine()
        engine = get_contraindication_engine()
        assert engine is not None
        assert engine is get_contraindication_engine()
        _reset_contraindication_engine()

    def test_threshold_via_registry(self):
        from safety_gateway.threshold import _reset_threshold_engine, get_threshold_engine
        _reset_threshold_engine()
        engine = get_threshold_engine()
        assert engine is not None
        assert engine is get_threshold_engine()
        _reset_threshold_engine()

    def test_consent_checker_via_registry(self):
        from safety_gateway.consent_checker import _reset_consent_checker, get_consent_checker
        _reset_consent_checker()
        checker = get_consent_checker()
        assert checker is not None
        assert checker is get_consent_checker()
        _reset_consent_checker()

    def test_reset_creates_new_instance(self):
        from safety_gateway.contraindication import _reset_contraindication_engine, get_contraindication_engine
        _reset_contraindication_engine()
        engine1 = get_contraindication_engine()
        _reset_contraindication_engine()
        engine2 = get_contraindication_engine()
        assert engine1 is not engine2
        _reset_contraindication_engine()

    def test_registry_tracks_migrated_components(self):
        from safety_gateway.contraindication import _reset_contraindication_engine, get_contraindication_engine
        _reset_contraindication_engine()
        get_contraindication_engine()
        assert sg_registry.is_registered("contraindication_engine")
        assert sg_registry.is_initialized("contraindication_engine")
        _reset_contraindication_engine()
