# 常用开发命令（与 CI 保持一致，避免"本地过、CI 挂"）
#
# 注意：pytest 必须带 --dist=loadfile（见 pyproject.toml 注释）——本仓库多个
# 测试类存在链式依赖，按用例随机分发会把链条拆到不同 worker 造成假失败。

PY ?= python
TRACKS_UNIT := tests/unit
TRACKS_INT := tests/integration/int
PYTEST_FLAGS := -q -p no:cacheprovider -n 8 --dist=loadfile --timeout=300

.PHONY: help install lint lint-fix test test-unit test-int test-all test-perf cov bench bench-all frontend fe-test fe-lint fe-build edge compose-up

help:
	@echo "make install     - 安装后端依赖（含开发依赖）"
	@echo "make lint        - ruff 全量检查（含历史债，仅参考）"
	@echo "make test        - 后端单元 + INT 集成测试"
	@echo "make cov         - 覆盖率（整体 80 / 核心 90 门禁）"
	@echo "make frontend    - 前端 lint + 类型检查 + 测试 + 构建"
	@echo "make edge        - 本地以开发机模式启动边缘端"

install:
	$(PY) -m pip install -r requirements.txt -r requirements-dev.txt

lint:
	$(PY) -m ruff check app safety_gateway edge tests scripts

lint-fix:
	$(PY) -m ruff check --fix --select RUF100,F401,W291,W292,W293 app tests

test: test-unit test-int

test-unit:
	$(PY) -m pytest $(TRACKS_UNIT) $(PYTEST_FLAGS) --tb=short

test-int:
	$(PY) -m pytest $(TRACKS_INT) $(PYTEST_FLAGS) --tb=short

# perf 标记的用例断言墙钟 P99，必须串行跑（-n0 关闭 xdist 并行；
# 不能用 -p no:xdist，否则 addopts 里的 --dist 会变成未识别参数）
test-perf:
	$(PY) -m pytest -m perf -n0 --tb=short

# Gate 1 目标：整体 80%、核心 90%；当前基线 整体 76%、核心 75%，
# 故先按棘轮设门（75/74）防止倒退，达标后再提高。
cov:
	$(PY) -m pytest $(TRACKS_UNIT) $(TRACKS_INT) $(PYTEST_FLAGS) --tb=short \
		--cov=app --cov-report=term-missing
	$(PY) -m coverage report --fail-under=75
	$(PY) -m coverage report \
		--include='app/security/*,app/services/*,app/fusion/*,app/agents/*' \
		--fail-under=74

# 性能基准统一入口（真机基准需现场硬件）
bench:
	$(PY) scripts/bench_suite.py --suite inline --json bench_report.json

bench-all:
	$(PY) scripts/bench_suite.py --suite all --json bench_report.json

frontend fe-test:
	cd frontend && npm run lint && npx tsc -b && npm run test:run && npm run build

fe-lint:
	cd frontend && npm run lint

fe-build:
	cd frontend && npm run build

# 开发机（无 TensorRT）启动边缘端：自动退回空导航后端，链路仍可跑通
edge:
	cd . && $(PY) -m edge

compose-up:
	docker compose -f docker-compose.dev.yml up -d --build
