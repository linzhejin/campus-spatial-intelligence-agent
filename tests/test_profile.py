"""用户画像（EMA 权重先验）单测。"""

import pytest

from agents import planner, profile


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """每个用例用独立存储文件 + 清空缓存。"""
    monkeypatch.setattr(profile, "_PROFILES_PATH", tmp_path / "profiles.json")
    monkeypatch.setattr(profile, "_cache", None)
    yield


W = {"distance": 0.7, "slope": 0.2, "scenery": 0.1}


class TestRecordFeedback:
    def test_first_accept_creates_profile(self):
        p = profile.record_route_feedback("u1", W)
        assert p["accepted_count"] == 1
        # EMA: 0.7*0.9 + 0.3*0.7 = 0.84
        assert p["weights"]["distance"] == pytest.approx(0.84, abs=1e-3)

    def test_ema_converges_toward_accepted(self):
        for _ in range(10):
            p = profile.record_route_feedback("u1", W)
        assert p["weights"]["distance"] == pytest.approx(0.7, abs=0.02)

    def test_rejected_does_not_update_weights(self):
        p1 = profile.record_route_feedback("u1", W, accepted=True)
        p2 = profile.record_route_feedback("u1", {"distance": 0.1, "slope": 0.1, "scenery": 0.8},
                                           accepted=False)
        assert p2["weights"] == p1["weights"]
        assert p2["exposure_count"] == p1["exposure_count"] + 1
        assert p2["accepted_count"] == p1["accepted_count"]

    def test_unnormalized_weights_are_normalized(self):
        p = profile.record_route_feedback("u1", {"distance": 7, "slope": 2, "scenery": 1})
        assert p["weights"]["distance"] == pytest.approx(0.84, abs=1e-3)

    def test_invalid_inputs_return_none(self):
        assert profile.record_route_feedback("", W) is None
        assert profile.record_route_feedback("u1", {"distance": "x"}) is None
        assert profile.record_route_feedback("u1", {"distance": 0, "slope": 0, "scenery": 0}) is None

    def test_persists_across_cache_reset(self, monkeypatch):
        profile.record_route_feedback("u1", W)
        monkeypatch.setattr(profile, "_cache", None)  # 模拟进程重启
        p = profile.get_profile("u1")
        assert p["accepted_count"] == 1


class TestBuildProfileMessage:
    def test_none_below_min_samples(self):
        profile.record_route_feedback("u1", W)
        assert profile.build_profile_message("u1") is None

    def test_message_after_min_samples(self):
        for _ in range(profile.MIN_SAMPLES):
            profile.record_route_feedback("u1", W)
        msg = profile.build_profile_message("u1")
        assert "distance=" in msg and "3 次采纳" in msg

    def test_none_for_unknown_uid(self):
        assert profile.build_profile_message("ghost") is None
        assert profile.build_profile_message(None) is None


class TestPlannerInjection:
    def test_profile_not_injected_for_commute(self):
        for _ in range(profile.MIN_SAMPLES):
            profile.record_route_feedback("u1", {"distance": 0.1, "slope": 0.1, "scenery": 0.8})
        msgs = planner._build_messages("从教五到图书馆", uid="u1")
        assert not any("历史偏好" in m["content"] for m in msgs if m["role"] == "system")

    def test_profile_injected_when_mature(self):
        for _ in range(profile.MIN_SAMPLES):
            profile.record_route_feedback("u1", W)
        msgs = planner._build_messages("随便走走", uid="u1")
        sys_texts = [m["content"] for m in msgs if m["role"] == "system"]
        assert any("历史偏好" in t for t in sys_texts)

    def test_profile_not_injected_when_cold(self):
        msgs = planner._build_messages("随便走走", uid="newbie")
        sys_texts = [m["content"] for m in msgs if m["role"] == "system"]
        assert not any("历史偏好" in t for t in sys_texts)
