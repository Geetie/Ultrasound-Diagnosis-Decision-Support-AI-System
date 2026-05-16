# SG安全网关模块

## 概述

SG安全网关（Safety Gateway）是超声诊疗决策支持系统的独立安全校验进程，负责在输出诊断结果前进行多重安全校验，确保患者隐私保护和诊断安全性。

## 核心功能

### 1. 脱敏校验
- 检测并拦截结构化身份信息（身份证号、手机号、姓名、病历号等）
- 支持规则异构冗余，独立规则集不复制主规则源
- 支持规则热更新，失败时自动回滚

### 2. 禁忌校验
- 根据患者病情和临床上下文，检测禁忌诊断结果
- 支持禁用规则过滤
- 多条件匹配逻辑

### 3. 同意状态校验
- 两阶段校验：入口快速拒绝 + 输出前实时校验
- 直连PostgreSQL获取最新患者同意状态
- 支持数据库连接失败重试和熔断器保护

### 4. 一致性校验
- 验证专科结论与融合输出的一致性
- 支持按器官分类比较

### 5. 阈值校验
- 动态加载病种级置信度阈值
- 支持从PostgreSQL动态读取配置
- 缓存机制减少数据库查询

## 文件结构

```
safety_gateway/
├── __init__.py
├── main.py           # FastAPI服务入口，定义API端点
├── checker.py        # 核心校验逻辑，协调各校验引擎
├── desensitization.py # 脱敏检测引擎
├── contraindication.py # 禁忌校验引擎
├── threshold.py      # 阈值校验引擎
├── health.py         # 健康检查模块
└── circuit_breaker.py # 熔断器模式实现
```

## API端点

### POST /check
安全校验主端点，执行完整校验流程

**请求体：**
```json
{
  "patient_id": "string",
  "consent_status": "granted|denied",
  "data": {...},
  "clinical_context": {...}
}
```

**响应：**
```json
{
  "status": "passed|failed|degraded",
  "consent_status": "granted|denied",
  "sensitive_data_detected": [],
  "contraindication_matches": [],
  "threshold_results": [],
  "degraded_mode": false,
  "elapsed_ms": 1.5
}
```

### GET /health
健康检查端点，返回服务状态和延迟统计

**响应：**
```json
{
  "status": "healthy",
  "timestamp": "2024-01-01T00:00:00Z",
  "uptime_seconds": 3600,
  "latency_stats": {
    "samples": 1000,
    "p50_ms": 1.2,
    "p95_ms": 2.5,
    "p99_ms": 4.8
  },
  "components": {
    "redis": "healthy",
    "postgresql": "healthy",
    "rules_engine": "healthy"
  }
}
```

### POST /reload-rules
触发脱敏规则热更新

**响应：**
```json
{
  "success": true,
  "message": "Rules reloaded successfully"
}
```

## 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| SAFETY_GATEWAY_PORT | 服务端口 | 9001 |
| SAFETY_GATEWAY_HOST | 绑定地址 | 0.0.0.0 |
| DB_QUERY_TIMEOUT | 数据库查询超时（秒） | 5 |
| DB_POOL_SIZE | 数据库连接池大小 | 10 |
| DESENSITIZATION_RULES_SAFETY_PATH | 脱敏规则文件路径 | - |

### OOM保护

安全网关作为安全关键组件，需在Docker Compose中配置：
```yaml
services:
  safety-gateway:
    ...
    init: true
    user: root
    command: ["bash", "-c", "echo -1000 > /proc/self/oom_score_adj && exec uvicorn ..."]
```

## 安全特性

### 进程隔离
- 独立uvicorn实例运行
- 主服务崩溃时可独立存活并返回E4005错误码
- 延迟基准目标：P99 < 5ms

### 降级机制
- 数据库连接失败时触发熔断器
- 阈值加载失败时使用默认值
- 规则热更新失败时保留旧规则

### 日志规范
- 使用structlog进行结构化日志
- 关键操作记录详细上下文
- 错误信息包含请求ID便于追踪

## 测试说明

### 测试覆盖范围
- 脱敏引擎：正则匹配、人名检测、规则验证
- 禁忌校验：规则匹配、禁用规则过滤、多条件匹配
- 阈值校验：默认阈值、动态加载、缓存机制
- 一致性校验：结论比较、降级路径
- 熔断器：状态转换、重试机制

### 运行测试

```bash
# 运行所有安全网关测试
python -m pytest tests/unit/test_safety_gateway.py -v

# 运行特定测试类
python -m pytest tests/unit/test_safety_gateway.py::TestDesensitizationEngine -v
```

## 开发规范

### 代码风格
- 命名：snake_case（函数/变量）、PascalCase（类）
- 类型注解：所有函数参数和返回值必须有类型注解
- 异常处理：关键路径必须有异常捕获和日志记录
- 注释：模块级文档、复杂逻辑说明

### 提交规范
- 遵循conventional commit格式
- 测试用例与代码同步提交
- 修改配置文件需同步更新文档
