import pytest

from app import limits


@pytest.mark.parametrize("speed,name,tr", [
    (100_000, "Standard-mode", 1000e-9),
    (400_000, "Fast-mode", 300e-9),
    (250_000, "Fast-mode", 300e-9),
    (1_000_000, "Fast-mode Plus", 120e-9),
])
def test_i2c_mode(speed, name, tr):
    _, mode, rise, _ = limits.i2c_mode(speed)
    assert mode == name
    assert rise == tr
    assert limits.limits_for("i2c", "SCL", speed)["rise_30_70"].max == tr


def test_judge():
    lim = limits.Limit(-2.0, 2.0)
    assert limits.judge(1.0, lim) is True
    assert limits.judge(2.5, lim) is False
    assert limits.judge(-2.5, lim) is False
    assert limits.judge(None, lim) is False      # 有規格但量不到 = 不合格
    assert limits.judge(1.0, None) is None       # 沒有規格 = 不判定
    assert limits.judge(5.0, limits.Limit(None, 10.0)) is True


def test_override(tmp_path, monkeypatch):
    f = tmp_path / "limits_override.json"
    f.write_text('{"uart": {"TX": {"baud_error_pct": [-3, 3]}}}', encoding="utf-8")
    monkeypatch.setattr(limits, "OVERRIDE_FILE", f)
    lim = limits.limits_for("uart", "TX", 115200)["baud_error_pct"]
    assert (lim.min, lim.max) == (-3, 3)
