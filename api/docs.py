"""
游戏内API文档与Mock服务系统 - 核心模块

功能：
- generate_docs()  文档生成：自动更新 + 请求/响应示例
- mock_server()    Mock服务：动态数据 + 延迟模拟
- contract_test()  契约测试：消费者驱动 + 版本
- api_test()       接口测试：自动化执行 + 覆盖率统计
- versioning()     版本管理：兼容性检查 + 废弃管理
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import hashlib
import json
import math
import random
import string
import time


@dataclass
class Config:
    pass


class APIDocs:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}
        self._state = {}
        self._history = []
        # 接口注册表：path -> endpoint 定义
        self._endpoints = {}
        # 消费者驱动的契约列表
        self._contracts = []
        # 自动化接口测试用例
        self._api_tests = []
        # 版本注册表：version -> {endpoints, schemas, deprecated, sunset}
        self._versions = {}
        # 文档自动更新所需的修订号与快照
        self._revision = 0
        self._docs_revision = -1
        self._docs_version = 0
        self._docs_snapshot = None
        # Mock 动态数据序列号，保证连续调用数据不同
        self._mock_seq = 0

    def update(self, dt: float):
        pass

    def reset(self):
        self._state = {}
        self._history = []
        self._endpoints = {}
        self._contracts = []
        self._api_tests = []
        self._versions = {}
        self._revision = 0
        self._docs_revision = -1
        self._docs_version = 0
        self._docs_snapshot = None
        self._mock_seq = 0

    # ------------------------------------------------------------------
    # 接口注册
    # ------------------------------------------------------------------
    def register_endpoint(self, path, method="GET", request_schema=None,
                          response_schema=None, example_request=None,
                          example_response=None, version="v1"):
        """注册接口；未提供示例时根据 schema 自动生成示例。"""
        endpoint = {
            "path": path,
            "method": method.upper(),
            "request_schema": request_schema or {},
            "response_schema": response_schema or {},
            "example_request": example_request if example_request is not None
                               else self._generate_example(request_schema),
            "example_response": example_response if example_response is not None
                                else self._generate_example(response_schema),
            "version": version,
        }
        self._endpoints[path] = endpoint
        self._revision += 1
        info = self._versions.setdefault(version, {
            "version": version,
            "endpoints": set(),
            "schemas": {},
            "deprecated": False,
            "sunset": None,
        })
        info["endpoints"].add(path)
        info["schemas"][path] = endpoint["response_schema"]
        return endpoint

    # ------------------------------------------------------------------
    # 文档生成：自动更新 + 示例
    # ------------------------------------------------------------------
    def generate_docs(self):
        """生成文档。接口定义变化时自动重新生成（版本号递增），
        每个接口都带请求/响应示例。"""
        auto_updated = self._docs_revision != self._revision
        if auto_updated:
            self._docs_version += 1
            self._docs_revision = self._revision
            endpoints = {}
            for path in sorted(self._endpoints):
                ep = self._endpoints[path]
                endpoints[path] = {
                    "path": ep["path"],
                    "method": ep["method"],
                    "version": ep["version"],
                    "request_schema": ep["request_schema"],
                    "response_schema": ep["response_schema"],
                    "example_request": ep["example_request"],
                    "example_response": ep["example_response"],
                }
            digest = hashlib.sha256(
                json.dumps(endpoints, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            self._docs_snapshot = {
                "doc_version": self._docs_version,
                "revision": self._revision,
                "generated_at": time.time(),
                "endpoints": endpoints,
                "hash": digest,
            }
            self._history.append(("generate_docs", self._docs_version))
        docs = dict(self._docs_snapshot)
        docs["auto_updated"] = auto_updated
        return docs

    # ------------------------------------------------------------------
    # Mock 服务：动态数据 + 延迟模拟
    # ------------------------------------------------------------------
    def mock_server(self, path, latency_ms=None, dynamic=True):
        """按接口的 response_schema 返回 Mock 响应。
        dynamic=True 时每次生成不同的数据；latency_ms 模拟网络延迟。"""
        if path not in self._endpoints:
            return {"status": 404, "error": "no mock for path: %s" % path}
        endpoint = self._endpoints[path]
        if latency_ms is None:
            latency_ms = self.config.get("mock_latency_ms", 0)
        start = time.monotonic()
        if latency_ms > 0:
            time.sleep(latency_ms / 1000.0)
        elapsed_ms = (time.monotonic() - start) * 1000.0
        if dynamic:
            self._mock_seq += 1
            data = self._generate_dynamic(endpoint["response_schema"])
        else:
            data = endpoint["example_response"]
        return {
            "status": 200,
            "path": path,
            "data": data,
            "dynamic": dynamic,
            "latency_ms": round(elapsed_ms, 3),
            "simulated_latency_ms": latency_ms,
        }

    # ------------------------------------------------------------------
    # 契约测试：消费者驱动 + 版本
    # ------------------------------------------------------------------
    def add_contract(self, consumer, path, expected_fields=None, version=None):
        """消费者（前端）登记自己依赖的接口字段与版本。"""
        contract = {
            "consumer": consumer,
            "path": path,
            "expected_fields": list(expected_fields or []),
            "version": version,
        }
        self._contracts.append(contract)
        return contract

    def contract_test(self):
        """逐条校验消费者契约与提供者（后端）接口是否一致。"""
        results = []
        failures = []
        for contract in self._contracts:
            problems = []
            endpoint = self._endpoints.get(contract["path"])
            if endpoint is None:
                problems.append("provider missing endpoint: %s" % contract["path"])
            else:
                props = endpoint["response_schema"].get("properties", {})
                for name in contract["expected_fields"]:
                    if name not in props:
                        problems.append("missing field: %s" % name)
                if (contract["version"] is not None
                        and endpoint["version"] != contract["version"]):
                    problems.append(
                        "version mismatch: consumer=%s provider=%s"
                        % (contract["version"], endpoint["version"]))
            result = {
                "consumer": contract["consumer"],
                "path": contract["path"],
                "version": contract["version"],
                "passed": not problems,
                "problems": problems,
            }
            results.append(result)
            if problems:
                failures.append(result)
        return {
            "consumer_driven": True,
            "total": len(results),
            "passed": len(results) - len(failures),
            "failed": len(failures),
            "ok": not failures,
            "results": results,
            "failures": failures,
        }

    # ------------------------------------------------------------------
    # 接口测试：自动化 + 覆盖率
    # ------------------------------------------------------------------
    def add_api_test(self, name, path, expect_status=200, required_fields=None):
        """登记一条自动化接口测试用例。"""
        case = {
            "name": name,
            "path": path,
            "expect_status": expect_status,
            "required_fields": list(required_fields or []),
        }
        self._api_tests.append(case)
        return case

    def api_test(self):
        """自动执行所有已登记的接口测试，并统计接口覆盖率。"""
        results = []
        for case in self._api_tests:
            resp = self.mock_server(case["path"])
            problems = []
            if resp["status"] != case["expect_status"]:
                problems.append("status %s != %s"
                                % (resp["status"], case["expect_status"]))
            data = resp.get("data") or {}
            for name in case["required_fields"]:
                if name not in data:
                    problems.append("missing field: %s" % name)
            results.append({
                "name": case["name"],
                "path": case["path"],
                "passed": not problems,
                "problems": problems,
            })
        failed_cases = [r for r in results if not r["passed"]]
        tested = {c["path"] for c in self._api_tests}
        all_paths = set(self._endpoints)
        covered = tested & all_paths
        coverage = len(covered) / len(all_paths) if all_paths else 1.0
        return {
            "automated": True,
            "total": len(results),
            "passed": len(results) - len(failed_cases),
            "failed": len(failed_cases),
            "ok": not failed_cases,
            "coverage": coverage,
            "covered_endpoints": sorted(covered),
            "uncovered_endpoints": sorted(all_paths - tested),
            "results": results,
        }

    # ------------------------------------------------------------------
    # 版本管理：兼容性 + 废弃
    # ------------------------------------------------------------------
    def deprecate_version(self, version, sunset_days=90):
        """废弃一个版本，并给出下线（sunset）日期。"""
        info = self._versions.setdefault(version, {
            "version": version,
            "endpoints": set(),
            "schemas": {},
            "deprecated": False,
            "sunset": None,
        })
        info["deprecated"] = True
        info["sunset"] = time.strftime(
            "%Y-%m-%d", time.gmtime(time.time() + sunset_days * 86400))
        return {"version": version, "deprecated": True, "sunset": info["sunset"]}

    def versioning(self):
        """版本报告：检测相邻版本间的破坏性变更，并列出已废弃版本。"""
        ordered = sorted(self._versions)
        breaking = []
        for prev, curr in zip(ordered, ordered[1:]):
            pinfo = self._versions[prev]
            cinfo = self._versions[curr]
            for path in sorted(pinfo["endpoints"] - cinfo["endpoints"]):
                breaking.append({
                    "type": "endpoint_removed",
                    "path": path,
                    "from_version": prev,
                    "to_version": curr,
                })
            for path in sorted(pinfo["endpoints"] & cinfo["endpoints"]):
                prev_props = pinfo["schemas"].get(path, {}).get("properties", {})
                curr_props = cinfo["schemas"].get(path, {}).get("properties", {})
                for name in sorted(set(prev_props) - set(curr_props)):
                    breaking.append({
                        "type": "field_removed",
                        "path": path,
                        "field": name,
                        "from_version": prev,
                        "to_version": curr,
                    })
                for name in sorted(set(prev_props) & set(curr_props)):
                    if prev_props[name].get("type") != curr_props[name].get("type"):
                        breaking.append({
                            "type": "field_type_changed",
                            "path": path,
                            "field": name,
                            "from_version": prev,
                            "to_version": curr,
                        })
        versions = [{
            "version": v,
            "endpoints": sorted(self._versions[v]["endpoints"]),
            "deprecated": self._versions[v]["deprecated"],
            "sunset": self._versions[v]["sunset"],
        } for v in ordered]
        deprecated = [v for v in versions if v["deprecated"]]
        return {
            "versions": versions,
            "breaking_changes": breaking,
            "compatible": not breaking,
            "deprecated": deprecated,
        }

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _generate_example(self, schema):
        """根据 schema 生成静态示例数据（用于文档展示）。"""
        if not schema:
            return {}
        return self._value_from_schema(schema, static=True)

    def _generate_dynamic(self, schema):
        """根据 schema 生成动态数据（每次调用不同，用于 Mock）。"""
        if not schema:
            return {}
        return self._value_from_schema(schema, static=False)

    def _value_from_schema(self, schema, static):
        if not isinstance(schema, dict):
            return None
        if "enum" in schema:
            choices = schema["enum"]
            return choices[0] if static else random.choice(list(choices))
        stype = schema.get("type", "object")
        if stype == "object":
            return {name: self._value_from_schema(sub, static)
                    for name, sub in schema.get("properties", {}).items()}
        if stype == "array":
            item = self._value_from_schema(schema.get("items", {}), static)
            return [item]
        if stype == "integer":
            if static:
                return 1
            return self._mock_seq * 1000000 + random.randint(0, 999999)
        if stype == "number":
            if static:
                return 1.0
            return round(random.uniform(0, 1000) + self._mock_seq, 6)
        if stype == "boolean":
            return True if static else bool(random.getrandbits(1))
        if stype == "string":
            if static:
                return "example"
            suffix = "".join(random.choices(string.ascii_lowercase, k=8))
            return "mock_%d_%s" % (self._mock_seq, suffix)
        return None
