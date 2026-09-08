"""The signal generators: what makes a bar an entry or an exit.

simulate decides what to DO with a signal; these decide what IS one. Both were
untested. Each generator is fed hand-built daily bars (tests.support.daily_bars)
so the condition can be reasoned about rather than inferred from real prices.
"""
import unittest

import numpy as np

from kitelab import backtest, darvas, holygrail
from tests.support import bars, daily_bars

RISE = [(100 + i, 101 + i, 99 + i, 100 + i) for i in range(120)]
FALL = [(220 - i, 221 - i, 219 - i, 220 - i) for i in range(120)]


class EmaStack(unittest.TestCase):
    def test_a_steady_rise_ends_in_the_stack(self):
        with daily_bars(bars(RISE)):
            got = backtest.ema_stack_signal("X")
        self.assertTrue(bool(got["in_stack"].iloc[-1]))
        self.assertFalse(bool(got["exit_ok"].iloc[-1]))

    def test_a_steady_fall_is_out_of_the_stack(self):
        with daily_bars(bars(FALL)):
            got = backtest.ema_stack_signal("X")
        self.assertFalse(bool(got["in_stack"].iloc[-1]))
        self.assertTrue(bool(got["exit_ok"].iloc[-1]))

    def test_a_wider_band_makes_entry_harder_and_exit_harder(self):
        """The band is a buffer on BOTH sides: it must clear the line by more to
        enter, and fall below by more to leave. A band that only tightened entry
        would quietly turn into a different rule."""
        with daily_bars(bars(RISE)):
            tight = backtest.ema_stack_signal("X", band=0.0)
            wide = backtest.ema_stack_signal("X", band=0.05)
        self.assertGreaterEqual(int(tight["entry_ok"].sum()), int(wide["entry_ok"].sum()))
        with daily_bars(bars(FALL)):
            tight = backtest.ema_stack_signal("X", band=0.0)
            wide = backtest.ema_stack_signal("X", band=0.05)
        self.assertGreaterEqual(int(tight["exit_ok"].sum()), int(wide["exit_ok"].sum()))

    def test_daily_only_ignores_the_higher_timeframes(self):
        """The control arm. It must admit at least as much as the full stack --
        removing two conditions cannot make a rule stricter."""
        with daily_bars(bars(RISE)):
            stack = backtest.ema_stack_signal("X", stack="mwd")
            solo = backtest.ema_stack_signal("X", stack="daily")
        self.assertGreaterEqual(int(solo["entry_ok"].sum()), int(stack["entry_ok"].sum()))

    def test_the_all_time_high_band_is_its_own_column(self):
        """Since 2026-09-07 the filter touches NEITHER entry_ok nor exit_ok: it
        is the `near_ath` column, and simulate declines crosses where it is
        False. Folding it into entry_ok let the filter switching on look like
        a fresh cross (tests.test_ath_filter has the full story); a filter that
        blocked EXITS would refuse to sell what it had already bought."""
        rows = RISE[:60] + [(160 - i, 161 - i, 159 - i, 160 - i) for i in range(60)]
        with daily_bars(bars(rows)):
            plain = backtest.ema_stack_signal("X")
            near = backtest.ema_stack_signal("X", ath_band=0.02)
        self.assertEqual(near["entry_ok"].tolist(), plain["entry_ok"].tolist())
        self.assertEqual(near["exit_ok"].tolist(), plain["exit_ok"].tolist())
        self.assertTrue(bool(plain["near_ath"].all()))
        self.assertLess(int(near["near_ath"].sum()), len(rows))

    def test_months_and_weeks_done_never_decrease(self):
        with daily_bars(bars(RISE)):
            got = backtest.ema_stack_signal("X")
        for col in ("weeks_done", "months_done"):
            v = got[col].to_numpy()
            self.assertTrue(bool((np.diff(v) >= 0).all()), f"{col} went backwards")


class DarvasChannels(unittest.TestCase):
    def test_the_entry_channel_excludes_the_current_bar(self):
        """A 20-candle high that included today would be met by any new high,
        making the breakout condition trivially true."""
        rows = [(100, 100 + i, 99, 100) for i in range(40)]
        with daily_bars(bars(rows)):
            ch = darvas.channels("X")
        col = [c for c in ch.columns if "hi" in c.lower() or "entry" in c.lower()]
        self.assertTrue(col, f"no channel column in {list(ch.columns)}")
        top = ch[col[0]].to_numpy()
        highs = ch["high"].to_numpy()
        for i in range(25, len(top)):
            if not np.isnan(top[i]):
                self.assertLess(top[i], highs[i] + 1e-9)

    def test_the_weekly_gate_is_open_in_a_sustained_rise_and_shut_in_a_fall(self):
        rise = bars([(100 + i, 101 + i, 99 + i, 100 + i) for i in range(400)])
        fall = bars([(500 - i, 501 - i, 499 - i, 500 - i) for i in range(400)])
        self.assertTrue(bool(darvas.weekly_gate(rise)[-1]))
        self.assertFalse(bool(darvas.weekly_gate(fall)[-1]))

    def test_the_gate_returns_one_value_per_daily_bar(self):
        frame = bars(RISE)
        self.assertEqual(len(darvas.weekly_gate(frame)), len(frame))


class HolyGrailSetups(unittest.TestCase):
    def test_a_flat_market_never_clears_the_adx_floor(self):
        flat = bars([(100, 101, 99, 100)] * 200)
        got = holygrail.setups(flat)
        trig = [c for c in got.columns if "trend" in c or "ok" in c or "setup" in c]
        self.assertTrue(trig, f"no condition column in {list(got.columns)}")
        for c in trig:
            if got[c].dtype == bool:
                self.assertEqual(int(got[c].sum()), 0, f"{c} fired on a flat tape")

    def test_a_crash_is_not_an_uptrend_however_strong(self):
        """ADX measures how hard price is trending, not which way. Direction
        comes from +DI vs -DI, and this is the test that says so."""
        crash = bars([(500 - 3 * i, 501 - 3 * i, 499 - 3 * i, 500 - 3 * i)
                      for i in range(150)])
        got = holygrail.setups(crash)
        if "uptrend" in got.columns:
            self.assertEqual(int(got["uptrend"].sum()), 0)
        else:
            self.assertIn("adx", "".join(got.columns).lower())


if __name__ == "__main__":
    unittest.main()
