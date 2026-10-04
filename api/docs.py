"""
游戏内API文档与Mock服务系统 - 核心模块

功能：
- generate_docs(): 自动从已注册接口生成文档，接口变更后自动更新，并附带请求/响应示例
- mock_server(): 进程内 Mock 服务，支持动态数据与网络延迟模拟
- contract_test(): 消费者驱动的契约测试，校验前后端接口与版本一致性
- api_test(): 自动化接口测试并统计覆盖率
- versioning(): 接口版本管理，破坏性变更检测与版本废弃
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
import random
import re
import time


@dataclass
class Config:
    pass


@dataclass
class Endpoint:
    path: str
    method: str = "GET"
    version: str = "v1"
    description: str = ""
    request_schema: Dict = field(default_factory=dict)
    response_schema: Dict = field(default_factory=dict)
    handler: Optional[Callable[[Dict], Any]] = None


def _version_key(version: str) -> Tuple[int, ...]:
    nums = tuple(int(x) for x in re.findall(r"\d+", str(version)))
    return nums or (0,)


def _example_value(name: str, schema_type: Any, sequence: int) -> Any:
    t = str(schema_type).lower()
    if t in ("int", "integer"):
        return sequence + 1
    if t in ("float", "number"):
        return round(sequence + 0.5, 2)
    if t in ("bool", "boolean"):
        return sequence % 2 == 0
    return "%s_%d" % (name, sequence + 1)


def _generate_example(schema: Any, sequence: int = 0, name: str = "value") -> Any:
    """根据 schema 生成示例数据。sequence 不同则数据不同，用于 Mock 动态数据。"""
    if isinstance(schema, dict):
        return {k: _generate_example(v, sequence, k) for k, v in schema.items()}
    if isinstance(schema, (list, tuple)):
        if schema:
            return [_generate_example(schema[0], sequence, name)]
        return []
    return _example_value(name, schema, sequence)


def _matches_type(value: Any, schema_type: Any) -> bool:
    t = str(schema_type).lower()
    if t in ("int", "integer"):
        return isinstance(value, int) and not isinstance(value, bool)
    if t in ("float", "number"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t in ("bool", "boolean"):
        return isinstance(value, bool)
    if t in ("str", "string"):
        return isinstance(value, str)
    if t in ("list", "array"):
        return isinstance(value, list)
    if t in ("dict", "object"):
        return isinstance(value, dict)
    return True


def _validate_against_schema(data: Any, schema: Any, prefix: str = "") -> List[str]:
    """校验数据是否满足 schema，返回问题列表（空列表表示通过）。"""
    problems = []
    if isinstance(schema, dict):
        if not isinstance(data, dict):
            return ["%sexpected object, got %s" % (prefix, type(data).__name__)]
        for key, sub in schema.items():
            location = "%s%s" % (prefix, key)
            if key not in data:
                problems.append("missing field: %s" % location)
            else:
                problems.extend(_validate_against_schema(data[key], sub, location + "."))
        return problems
    if isinstance(schema, (list, tuple)):
        if not isinstance(data, list):
            return ["%sexpected array, got %s" % (prefix, type(data).__name__)]
        if schema and data:
            problems.extend(_validate_against_schema(data[0], schema[0], prefix + "[0]."))
        return problems
    if not _matches_type(data, schema):
        problems.append(
            "type mismatch at %s: expected %s, got %s"
            % (prefix.rstrip("."), schema, type(data).__name__)
        )
    return problems


def _schema_breaking_changes(old: Any, new: Any, prefix: str = "") -> List[str]:
    """对比新旧 schema，找出破坏性变更（字段删除、类型变更）。"""
    breaking = []
    if isinstance(old, dict) and isinstance(new, dict):
        for key, old_sub in old.items():
            location = "%s%s" % (prefix, key)
            if key not in new:
                breaking.append("field removed: %s" % location)
            else:
                breaking.extend(_schema_breaking_changes(old_sub, new[key], location + "."))
        return breaking
    if isinstance(old, (list, tuple)) and isinstance(new, (list, tuple)):
        if old and new:
            breaking.extend(_schema_breaking_changes(old[0], new[0], prefix + "[0]."))
        return breaking
    if str(old).lower() != str(new).lower():
        breaking.append(
            "field type changed at %s: %s -> %s" % (prefix.rstrip("."), old, new)
        )
    return breaking


class MockServer:
    """进程内 Mock 服务：动态数据 + 延迟模拟。"""

    def __init__(self, docs: "APIDocs", latency_ms: Any = 0):
        self._docs = docs
        self.latency_ms = latency_ms

    def set_latency(self, latency_ms: Any):
        """设置模拟延迟，支持固定毫秒数或 (min_ms, max_ms) 区间。"""
        self.latency_ms = latency_ms

    def _resolve_latency(self) -> float:
        lat = self.latency_ms
        if isinstance(lat, (tuple, list)) and len(lat) == 2:
            return random.uniform(float(lat[0]), float(lat[1]))
        return float(lat or 0)

    def handle_request(self, path: str, method: str = "GET",
                       params: Optional[Dict] = None,
                       version: Optional[str] = None) -> Dict:
        start = time.perf_counter()
        latency = self._resolve_latency()
        if latency > 0:
            time.sleep(latency / 1000.0)

        endpoint = self._docs._find_endpoint(path, method, version)
        self._docs._mock_sequence += 1
        sequence = self._docs._mock_sequence

        response = {
            "request_id": sequence,
            "mock": True,
        }
        if endpoint is None:
            response["code"] = 404
            response["error"] = "no mock endpoint for %s %s" % (method.upper(), path)
            response["data"] = None
        else:
            response["code"] = 0
            response["data"] = _generate_example(endpoint.response_schema, sequence)
            response["version"] = endpoint.version

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        response["latency_ms"] = round(elapsed_ms, 3)
        return response


class APIDocs:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._state = {}
        self._history = []
        self._endpoints: Dict[Tuple[str, str, str], Endpoint] = {}
        self._contracts: List[Dict] = []
        self._deprecated: Dict[str, Dict] = {}
        self._state_version = 0
        self._docs_cache: Optional[Dict] = None
        self._docs_cache_version = -1
        self._docs_generation = 0
        self._mock_sequence = 0

    def update(self, dt: float):
        pass

    def reset(self):
        self._state = {}
        self._history = []
        self._endpoints = {}
        self._contracts = []
        self._deprecated = {}
        self._state_version = 0
        self._docs_cache = None
        self._docs_cache_version = -1
        self._docs_generation = 0
        self._mock_sequence = 0

    # ------------------------------------------------------------------
    # 接口注册
    # ------------------------------------------------------------------
    def register_endpoint(self, path: str, method: str = "GET", version: str = "v1",
                          description: str = "",
                          request_schema: Optional[Dict] = None,
                          response_schema: Optional[Dict] = None,
                          handler: Optional[Callable[[Dict], Any]] = None) -> Endpoint:
        endpoint = Endpoint(
            path=path,
            method=method.upper(),
            version=version,
            description=description,
            request_schema=dict(request_schema or {}),
            response_schema=dict(response_schema or {}),
            handler=handler,
        )
        self._endpoints[(endpoint.path, endpoint.method, endpoint.version)] = endpoint
        self._state_version += 1
        return endpoint

    def _find_endpoint(self, path: str, method: str = "GET",
                       version: Optional[str] = None) -> Optional[Endpoint]:
        method = method.upper()
        if version is not None:
            return self._endpoints.get((path, method, version))
        candidates = [
            ep for (p, m, _v), ep in self._endpoints.items()
            if p == path and m == method
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda ep: _version_key(ep.version))

    def _known_versions(self) -> List[str]:
        versions = {ep.version for ep in self._endpoints.values()}
        versions.update(self._deprecated.keys())
        return sorted(versions, key=_version_key)

    # ------------------------------------------------------------------
    # 文档生成：自动更新 + 请求/响应示例
    # ------------------------------------------------------------------
    def generate_docs(self) -> Dict:
        """生成文档。接口注册/变更后再次调用会自动重新生成，无需手动刷新。"""
        if self._docs_cache is not None and self._docs_cache_version == self._state_version:
            return self._docs_cache

        self._docs_generation += 1
        endpoints = []
        for key in sorted(self._endpoints):
            ep = self._endpoints[key]
            endpoints.append({
                "path": ep.path,
                "method": ep.method,
                "version": ep.version,
                "description": ep.description,
                "deprecated": ep.version in self._deprecated,
                "request_schema": ep.request_schema,
                "response_schema": ep.response_schema,
                "request_example": _generate_example(ep.request_schema),
                "response_example": _generate_example(ep.response_schema),
            })

        self._docs_cache = {
            "title": "游戏内API文档",
            "generation": self._docs_generation,
            "generated_at": time.time(),
            "versions": self._known_versions(),
            "endpoints": endpoints,
        }
        self._docs_cache_version = self._state_version
        return self._docs_cache

    # ------------------------------------------------------------------
    # Mock 服务：动态数据 + 延迟模拟
    # ------------------------------------------------------------------
    def mock_server(self, latency_ms: Any = None) -> MockServer:
        if latency_ms is None:
            latency_ms = self.config.get("mock_latency_ms", 0)
        return MockServer(self, latency_ms=latency_ms)

    # ------------------------------------------------------------------
    # 契约测试：消费者驱动 + 版本校验
    # ------------------------------------------------------------------
    def register_contract(self, consumer: str, path: str, method: str = "GET",
                          version: Optional[str] = None,
                          required_fields: Optional[List[str]] = None) -> Dict:
        contract = {
            "consumer": consumer,
            "path": path,
            "method": method.upper(),
            "version": version,
            "required_fields": list(required_fields or []),
        }
        self._contracts.append(contract)
        self._state_version += 1
        return contract

    def contract_test(self) -> Dict:
        """运行所有消费者契约，校验提供方接口是否满足前端需求及版本一致性。"""
        results = []
        for contract in self._contracts:
            failures = []
            endpoint = self._find_endpoint(
                contract["path"], contract["method"], contract["version"]
            )
            if endpoint is None and contract["version"] is not None:
                other = self._find_endpoint(contract["path"], contract["method"])
                if other is not None:
                    failures.append(
                        "version mismatch: consumer expects %s, provider has %s"
                        % (contract["version"], other.version)
                    )
                    endpoint = other
            if endpoint is None:
                failures.append(
                    "provider missing endpoint: %s %s (version=%s)"
                    % (contract["method"], contract["path"], contract["version"])
                )
            else:
                for field_name in contract["required_fields"]:
                    if field_name not in endpoint.response_schema:
                        failures.append("missing field in response: %s" % field_name)
            results.append({
                "consumer": contract["consumer"],
                "path": contract["path"],
                "method": contract["method"],
                "version": contract["version"],
                "passed": not failures,
                "failures": failures,
            })
        failed = [r for r in results if not r["passed"]]
        return {
            "passed": not failed,
            "total": len(results),
            "failed": len(failed),
            "results": results,
        }

    # ------------------------------------------------------------------
    # 接口测试：自动化 + 覆盖率
    # ------------------------------------------------------------------
    def api_test(self, paths: Optional[List[str]] = None) -> Dict:
        """自动遍历已注册接口：生成示例请求、调用 handler、校验响应 schema，并统计覆盖率。"""
        targets = [
            ep for key, ep in sorted(self._endpoints.items())
            if paths is None or ep.path in paths
        ]
        total = len(self._endpoints)
        results = []
        for ep in targets:
            error = None
            try:
                request = _generate_example(ep.request_schema)
                if ep.handler is not None:
                    response = ep.handler(request)
                else:
                    response = _generate_example(ep.response_schema)
                problems = _validate_against_schema(response, ep.response_schema)
                if problems:
                    error = "; ".join(problems)
            except Exception as exc:  # handler 抛错视为测试失败
                error = "%s: %s" % (type(exc).__name__, exc)
            results.append({
                "path": ep.path,
                "method": ep.method,
                "version": ep.version,
                "passed": error is None,
                "error": error,
            })
        tested = len(results)
        passed = sum(1 for r in results if r["passed"])
        return {
            "total": total,
            "tested": tested,
            "passed": passed,
            "failed": tested - passed,
            "coverage": (tested / total) if total else 1.0,
            "results": results,
        }

    # ------------------------------------------------------------------
    # 版本管理：兼容性检查 + 废弃
    # ------------------------------------------------------------------
    def deprecate_version(self, version: str, sunset_days: int = 90) -> Dict:
        """废弃某个版本，记录废弃时间与预计下线时间。"""
        now = time.time()
        self._deprecated[version] = {
            "version": version,
            "deprecated_at": now,
            "sunset_days": sunset_days,
            "sunset_at": now + sunset_days * 86400,
        }
        self._state_version += 1
        return self._deprecated[version]

    def check_compatibility(self, old_version: str, new_version: str) -> Dict:
        """对比两个版本，检测破坏性变更（接口删除、字段删除、字段类型变更、新增必填请求字段）。"""
        breaking = []
        changes = []

        def by_path(ver):
            grouped = {}
            for (path, method, v), ep in self._endpoints.items():
                if v == ver:
                    grouped[(path, method)] = ep
            return grouped

        old_eps = by_path(old_version)
        new_eps = by_path(new_version)

        for key, old_ep in old_eps.items():
            new_ep = new_eps.get(key)
            label = "%s %s" % (old_ep.method, old_ep.path)
            if new_ep is None:
                breaking.append("endpoint removed: %s" % label)
                continue
            breaking.extend(
                _schema_breaking_changes(
                    old_ep.response_schema, new_ep.response_schema,
                    prefix="response %s." % label,
                )
            )
            for field_name in new_ep.request_schema:
                if field_name not in old_ep.request_schema:
                    breaking.append(
                        "request field added: %s.%s" % (label, field_name)
                    )
        for key, ep in new_eps.items():
            if key not in old_eps:
                changes.append("endpoint added: %s %s" % (ep.method, ep.path))

        return {
            "old_version": old_version,
            "new_version": new_version,
            "compatible": not breaking,
            "breaking_changes": breaking,
            "changes": changes,
        }

    def versioning(self) -> Dict:
        """版本总览：版本列表、当前版本、废弃信息、相邻版本兼容性报告。"""
        versions = self._known_versions()
        compatibility = {}
        for old, new in zip(versions, versions[1:]):
            compatibility["%s->%s" % (old, new)] = self.check_compatibility(old, new)
        return {
            "versions": versions,
            "current": versions[-1] if versions else None,
            "deprecated": dict(self._deprecated),
            "compatibility": compatibility,
        }
