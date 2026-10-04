"""
游戏内API文档与Mock服务系统 - 验收测试
运行方式：python -m pytest tests/test_api_docs.py -q
共 13 个测试用例
"""
import pytest
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.docs import APIDocs


class TestAPIDocs:
    def setup_method(self):
        self.system = APIDocs()

    def _register_player(self, version="v1"):
        return self.system.register_endpoint(
            "/api/player/info",
            method="GET",
            version=version,
            description="查询玩家信息",
            request_schema={"player_id": "int"},
            response_schema={
                "player_id": "int",
                "nickname": "string",
                "level": "int",
            },
        )

    # ---- 文档生成 -----------------------------------------------------
    def test_case_01(self):
        """文档生成：注册的接口自动出现在文档中，无需翻代码。"""
        self._register_player()
        self.system.register_endpoint(
            "/api/shop/list", method="GET", version="v1",
            response_schema={"items": ["string"]},
        )
        docs = self.system.generate_docs()
        paths = {ep["path"] for ep in docs["endpoints"]}
        assert paths == {"/api/player/info", "/api/shop/list"}
        player_doc = next(ep for ep in docs["endpoints"] if ep["path"] == "/api/player/info")
        assert player_doc["method"] == "GET"
        assert player_doc["description"] == "查询玩家信息"

    def test_case_02(self):
        """文档自动更新：接口变更后再次生成，文档自动反映最新代码。"""
        self._register_player()
        docs1 = self.system.generate_docs()
        assert len(docs1["endpoints"]) == 1
        gen1 = docs1["generation"]

        self.system.register_endpoint(
            "/api/rank/top", method="GET", version="v1",
            response_schema={"ranks": ["string"]},
        )
        docs2 = self.system.generate_docs()
        assert len(docs2["endpoints"]) == 2
        assert docs2["generation"] > gen1
        assert docs1["generation"] == gen1  # 旧文档快照不变

    def test_case_03(self):
        """文档示例：每个接口都带请求/响应示例，且类型与 schema 一致。"""
        self._register_player()
        docs = self.system.generate_docs()
        endpoint = docs["endpoints"][0]
        assert endpoint["request_example"]["player_id"] == 1
        assert isinstance(endpoint["response_example"]["player_id"], int)
        assert isinstance(endpoint["response_example"]["nickname"], str)
        assert isinstance(endpoint["response_example"]["level"], int)

    # ---- Mock 服务 ----------------------------------------------------
    def test_case_04(self):
        """Mock 服务：前端无需等后端，按 response schema 返回数据。"""
        self._register_player()
        mock = self.system.mock_server()
        response = mock.handle_request("/api/player/info", params={"player_id": 1})
        assert response["code"] == 0
        assert response["mock"] is True
        assert response["version"] == "v1"
        data = response["data"]
        assert set(data.keys()) == {"player_id", "nickname", "level"}
        assert isinstance(data["nickname"], str)

    def test_case_05(self):
        """Mock 动态数据：每次请求数据不同，不再是固定返回。"""
        self._register_player()
        mock = self.system.mock_server()
        resp1 = mock.handle_request("/api/player/info")
        resp2 = mock.handle_request("/api/player/info")
        assert resp1["data"] != resp2["data"]
        assert resp1["request_id"] != resp2["request_id"]

    def test_case_06(self):
        """Mock 延迟模拟：可以模拟网络延迟，响应耗时与配置一致。"""
        self._register_player()
        mock = self.system.mock_server(latency_ms=50)
        start = time.perf_counter()
        response = mock.handle_request("/api/player/info")
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms >= 45
        assert response["latency_ms"] >= 45

    # ---- 契约测试 -----------------------------------------------------
    def test_case_07(self):
        """契约测试：提供方满足消费者契约时通过。"""
        self._register_player()
        self.system.register_contract(
            consumer="game-frontend",
            path="/api/player/info",
            method="GET",
            version="v1",
            required_fields=["player_id", "nickname"],
        )
        report = self.system.contract_test()
        assert report["passed"] is True
        assert report["total"] == 1
        assert report["failed"] == 0
        assert report["results"][0]["consumer"] == "game-frontend"

    def test_case_08(self):
        """消费者驱动：前端需要但后端没有的字段会让契约失败。"""
        self._register_player()
        self.system.register_contract(
            consumer="game-frontend",
            path="/api/player/info",
            required_fields=["avatar"],
        )
        report = self.system.contract_test()
        assert report["passed"] is False
        assert report["failed"] == 1
        failure = report["results"][0]["failures"][0]
        assert "avatar" in failure

    def test_case_09(self):
        """契约版本：前后端接口版本不一致时契约失败。"""
        self._register_player(version="v2")
        self.system.register_contract(
            consumer="game-frontend",
            path="/api/player/info",
            version="v1",
        )
        report = self.system.contract_test()
        assert report["passed"] is False
        failure = report["results"][0]["failures"][0]
        assert "version mismatch" in failure
        assert "v1" in failure and "v2" in failure

    # ---- 接口测试 -----------------------------------------------------
    def test_case_10(self):
        """接口测试自动化：一次调用自动遍历全部接口并真正执行 handler。"""
        called = []

        def player_handler(request):
            called.append(request)
            return {"player_id": request["player_id"], "nickname": "hero", "level": 9}

        self.system.register_endpoint(
            "/api/player/info",
            request_schema={"player_id": "int"},
            response_schema={"player_id": "int", "nickname": "string", "level": "int"},
            handler=player_handler,
        )
        self.system.register_endpoint(
            "/api/shop/list", response_schema={"items": ["string"]},
        )
        report = self.system.api_test()
        assert report["total"] == 2
        assert report["tested"] == 2
        assert report["passed"] == 2
        assert report["failed"] == 0
        assert len(called) == 1
        assert called[0]["player_id"] == 1

    def test_case_11(self):
        """接口测试覆盖率：自动统计测试了多少接口，响应不符 schema 计入失败。"""
        self.system.register_endpoint(
            "/api/player/info",
            response_schema={"player_id": "int"},
        )
        self.system.register_endpoint(
            "/api/shop/list",
            response_schema={"items": ["string"]},
        )
        self.system.register_endpoint(
            "/api/bad/endpoint",
            response_schema={"score": "int"},
            handler=lambda request: {"score": "not-an-int"},
        )
        full = self.system.api_test()
        assert full["coverage"] == pytest.approx(1.0)
        assert full["failed"] == 1
        assert full["passed"] == 2

        partial = self.system.api_test(paths=["/api/player/info"])
        assert partial["coverage"] == pytest.approx(1 / 3)
        assert partial["tested"] == 1

    # ---- 版本管理 -----------------------------------------------------
    def test_case_12(self):
        """版本兼容：破坏性变更（删除字段）有提示，非破坏变更（加字段）兼容。"""
        self.system.register_endpoint(
            "/api/shop/list", version="v1",
            response_schema={"items": ["string"]},
        )
        self.system.register_endpoint(
            "/api/shop/list", version="v2",
            response_schema={"items": ["string"], "total": "int"},
        )
        compatible = self.system.check_compatibility("v1", "v2")
        assert compatible["compatible"] is True
        assert compatible["breaking_changes"] == []

        self.system.register_endpoint(
            "/api/player/info", version="v1",
            response_schema={"player_id": "int", "nickname": "string"},
        )
        self.system.register_endpoint(
            "/api/player/info", version="v2",
            response_schema={"player_id": "int"},
        )
        report = self.system.check_compatibility("v1", "v2")
        assert report["compatible"] is False
        assert any("nickname" in change for change in report["breaking_changes"])

        overview = self.system.versioning()
        assert overview["versions"] == ["v1", "v2"]
        assert overview["current"] == "v2"
        assert overview["compatibility"]["v1->v2"]["compatible"] is False

    def test_case_13(self):
        """版本废弃：旧版本可标记废弃并给出下线时间，文档同步标注。"""
        self._register_player(version="v1")
        self._register_player(version="v2")
        info = self.system.deprecate_version("v1", sunset_days=30)
        assert info["sunset_at"] > info["deprecated_at"]
        assert info["sunset_days"] == 30

        overview = self.system.versioning()
        assert "v1" in overview["deprecated"]
        assert overview["current"] == "v2"

        docs = self.system.generate_docs()
        deprecated_flags = {
            ep["version"]: ep["deprecated"] for ep in docs["endpoints"]
            if ep["path"] == "/api/player/info"
        }
        assert deprecated_flags == {"v1": True, "v2": False}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
