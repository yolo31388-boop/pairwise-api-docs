"""
游戏内API文档与Mock服务系统 - 验收测试
运行方式：python -m pytest tests/test_api_docs.py -q
共 13 个测试用例
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.docs import APIDocs


PLAYER_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "level": {"type": "integer"},
    },
}


class TestAPIDocs:
    def setup_method(self):
        self.system = APIDocs()

    # 文档生成：基础生成
    def test_case_01(self):
        self.system.register_endpoint("/players", "GET",
                                      response_schema=PLAYER_SCHEMA)
        docs = self.system.generate_docs()
        assert "/players" in docs["endpoints"]
        assert docs["endpoints"]["/players"]["method"] == "GET"
        assert docs["doc_version"] == 1

    # 文档生成：代码变了自动更新
    def test_case_02(self):
        self.system.register_endpoint("/players", response_schema=PLAYER_SCHEMA)
        first = self.system.generate_docs()
        again = self.system.generate_docs()
        assert again["auto_updated"] is False
        self.system.register_endpoint("/items", response_schema={
            "type": "object",
            "properties": {"item_id": {"type": "integer"}},
        })
        updated = self.system.generate_docs()
        assert updated["auto_updated"] is True
        assert updated["doc_version"] == first["doc_version"] + 1
        assert "/items" in updated["endpoints"]
        assert updated["hash"] != first["hash"]

    # 文档生成：带请求/响应示例
    def test_case_03(self):
        request_schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        self.system.register_endpoint("/players", "POST",
                                      request_schema=request_schema,
                                      response_schema=PLAYER_SCHEMA)
        docs = self.system.generate_docs()
        endpoint = docs["endpoints"]["/players"]
        assert endpoint["example_request"] == {"name": "example"}
        assert endpoint["example_response"] == {
            "id": 1, "name": "example", "level": 1}

    # Mock 服务：按 schema 返回数据
    def test_case_04(self):
        self.system.register_endpoint("/players", response_schema=PLAYER_SCHEMA)
        resp = self.system.mock_server("/players")
        assert resp["status"] == 200
        data = resp["data"]
        assert isinstance(data["id"], int)
        assert isinstance(data["name"], str)
        assert isinstance(data["level"], int)

    # Mock 服务：动态数据（每次调用不同）
    def test_case_05(self):
        self.system.register_endpoint("/players", response_schema=PLAYER_SCHEMA)
        responses = [self.system.mock_server("/players")["data"]
                     for _ in range(5)]
        assert responses[0] != responses[-1]
        assert len({r["id"] for r in responses}) > 1

    # Mock 服务：延迟模拟
    def test_case_06(self):
        self.system.register_endpoint("/players", response_schema=PLAYER_SCHEMA)
        start = time.monotonic()
        resp = self.system.mock_server("/players", latency_ms=60)
        elapsed = (time.monotonic() - start) * 1000.0
        assert elapsed >= 55
        assert resp["simulated_latency_ms"] == 60
        assert resp["latency_ms"] >= 55

    # 契约测试：契约一致时通过（消费者驱动）
    def test_case_07(self):
        self.system.register_endpoint("/players", response_schema=PLAYER_SCHEMA,
                                      version="v1")
        self.system.add_contract("game-client", "/players",
                                 expected_fields=["id", "name"], version="v1")
        report = self.system.contract_test()
        assert report["consumer_driven"] is True
        assert report["ok"] is True
        assert report["total"] == 1 and report["failed"] == 0

    # 契约测试：消费者需要的字段后端缺失时失败
    def test_case_08(self):
        self.system.register_endpoint("/players", response_schema={
            "type": "object",
            "properties": {"id": {"type": "integer"}},
        })
        self.system.add_contract("game-client", "/players",
                                 expected_fields=["id", "name"])
        report = self.system.contract_test()
        assert report["ok"] is False
        assert report["failures"][0]["consumer"] == "game-client"
        assert any("missing field: name" in p
                   for p in report["failures"][0]["problems"])

    # 契约测试：版本不一致时失败
    def test_case_09(self):
        self.system.register_endpoint("/players", response_schema=PLAYER_SCHEMA,
                                      version="v2")
        self.system.add_contract("game-client", "/players",
                                 expected_fields=["id"], version="v1")
        report = self.system.contract_test()
        assert report["ok"] is False
        assert any("version mismatch" in p
                   for p in report["failures"][0]["problems"])

    # 接口测试：自动化执行全部用例
    def test_case_10(self):
        self.system.register_endpoint("/players", response_schema=PLAYER_SCHEMA)
        self.system.add_api_test("list players", "/players",
                                 required_fields=["id", "name"])
        report = self.system.api_test()
        assert report["automated"] is True
        assert report["total"] == 1
        assert report["passed"] == 1
        assert report["ok"] is True

    # 接口测试：覆盖率统计
    def test_case_11(self):
        self.system.register_endpoint("/players", response_schema=PLAYER_SCHEMA)
        self.system.register_endpoint("/items", response_schema={
            "type": "object",
            "properties": {"item_id": {"type": "integer"}},
        })
        self.system.add_api_test("list players", "/players",
                                 required_fields=["id"])
        report = self.system.api_test()
        assert report["coverage"] == 0.5
        assert report["covered_endpoints"] == ["/players"]
        assert report["uncovered_endpoints"] == ["/items"]

    # 版本管理：破坏性变更检测（不兼容）
    def test_case_12(self):
        self.system.register_endpoint("/players", response_schema=PLAYER_SCHEMA,
                                      version="v1")
        self.system.register_endpoint("/players", response_schema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
        }, version="v2")
        report = self.system.versioning()
        assert report["compatible"] is False
        changes = {(c["type"], c.get("field")) for c in report["breaking_changes"]}
        assert ("field_removed", "id") in changes
        assert ("field_removed", "name") in changes

    # 版本管理：废弃与下线日期
    def test_case_13(self):
        self.system.register_endpoint("/players", response_schema=PLAYER_SCHEMA,
                                      version="v1")
        info = self.system.deprecate_version("v1", sunset_days=30)
        assert info["deprecated"] is True
        assert info["sunset"] is not None
        report = self.system.versioning()
        deprecated = {v["version"]: v for v in report["deprecated"]}
        assert "v1" in deprecated
        assert deprecated["v1"]["sunset"] == info["sunset"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
