"""时长适配：语速取值、静音缓冲分配、多 take 择优。"""
import pytest

from backend.audio import fitting


class TestChooseSpeed:
    def test_精确命中时保持原速(self):
        assert fitting.choose_speed(1.80, 1.80) == 1.0

    def test_语音偏长则加快(self):
        # 语音 1.95s 要压到 1.90s → 1.026，仍在边界内
        assert fitting.choose_speed(1.95, 1.90) == round(1.95 / 1.90, 4)

    def test_语音偏短则放慢(self):
        assert fitting.choose_speed(1.65, 1.70) == round(1.65 / 1.70, 4)

    def test_超出上限被夹紧(self):
        assert fitting.choose_speed(5.0, 1.0) == 1.10

    def test_低于下限被夹紧(self):
        assert fitting.choose_speed(0.2, 1.0) == 0.95

    def test_非法输入退回原速(self):
        assert fitting.choose_speed(0.0, 1.8) == 1.0
        assert fitting.choose_speed(1.8, 0.0) == 1.0
        assert fitting.choose_speed(-1.0, 1.8) == 1.0

    def test_自定义边界生效(self):
        assert fitting.choose_speed(3.0, 1.0, lo=0.9, hi=1.2) == 1.2


class TestPlanPads:
    """语音偏长吃静音、偏短多垫静音；side 决定优先方向。"""

    def test_偏长时优先吃尾部(self):
        p = fitting.plan_pads(1.80, 1.70, 0.20, 0.20, side="tail")
        assert p.head_pad == 0.20 and p.tail_pad == 0.10
        assert p.overflow is False

    def test_偏长时优先吃头部(self):
        p = fitting.plan_pads(1.80, 1.70, 0.20, 0.20, side="head")
        assert p.head_pad == 0.10 and p.tail_pad == 0.20

    def test_偏长时两侧均分(self):
        p = fitting.plan_pads(1.80, 1.70, 0.20, 0.20, side="split")
        assert p.head_pad == 0.15 and p.tail_pad == 0.15

    def test_尾部不够时继续吃头部(self):
        p = fitting.plan_pads(1.90, 1.70, 0.20, 0.05, side="tail")
        assert p.tail_pad == 0.0 and p.head_pad == 0.05

    def test_缓冲用尽标记溢出(self):
        p = fitting.plan_pads(2.50, 1.70, 0.20, 0.20)
        assert p.overflow is True
        assert p.head_pad == 0.0 and p.tail_pad == 0.0

    def test_偏短时优先在尾部补静音(self):
        p = fitting.plan_pads(1.50, 1.70, 0.10, 0.10, side="tail")
        assert p.head_pad == 0.10 and p.tail_pad == 0.30
        assert p.overflow is False

    def test_偏短时优先在头部补静音(self):
        p = fitting.plan_pads(1.50, 1.70, 0.10, 0.10, side="head")
        assert p.head_pad == 0.30 and p.tail_pad == 0.10

    def test_偏短时两侧均分(self):
        p = fitting.plan_pads(1.50, 1.70, 0.10, 0.10, side="split")
        assert p.head_pad == 0.20 and p.tail_pad == 0.20

    @pytest.mark.parametrize("side", ["tail", "head", "split"])
    def test_语音加静音总长守恒(self, side):
        """无论怎么分配，语音本体 + 两端静音必须等于目标 + 原缓冲。"""
        target, head, tail, speech = 1.70, 0.20, 0.20, 1.80
        p = fitting.plan_pads(speech, target, head, tail, side=side)
        assert abs((p.head_pad + p.tail_pad + speech) - (target + head + tail)) < 1e-6

    def test_零缓冲时纯按语音长度判定(self):
        assert fitting.plan_pads(1.70, 1.70, 0, 0).overflow is False
        assert fitting.plan_pads(1.80, 1.70, 0, 0).overflow is True


class TestPickBest:
    @staticmethod
    def _fit(no, delta, status="ok"):
        return fitting.TakeFit(take_no=no, raw_dur=1.0, speed=1.0, stretch=1.0,
                               final_dur=1.0 + delta, status=status, delta=delta)

    def test_优先取ok中偏差最小者(self):
        fits = [self._fit(1, 0.20), self._fit(2, 0.03), self._fit(3, -0.10)]
        assert fitting.pick_best(fits).take_no == 2

    def test_全溢出时取偏差最小者(self):
        fits = [self._fit(1, 0.60, "overflow"), self._fit(2, 0.20, "overflow")]
        assert fitting.pick_best(fits).take_no == 2

    def test_有空列表时报错(self):
        with pytest.raises(ValueError):
            fitting.pick_best([])
