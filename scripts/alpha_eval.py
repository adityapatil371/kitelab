"""Kakushadze's 101 formulaic alphas, parsed and evaluated on our own bars.

WHAT THIS IS FOR. The 101 are CROSS-SECTIONAL RANKING functions: given every
stock on one day, each returns a number saying which to prefer. That is a
different kind of object from anything on the board -- our nine rules say WHEN
to buy and sell one symbol at a time. The only place a ranking can enter this
workbench is `portfolio.run`'s cash-priority: when more signals fire than there
is money for, which does the account take? So these are tested as candidate
PRIORITIES against `mom_hi`, not as strategies. scripts/alpha_priority.py does
that; this file only builds the numbers.

SOURCE. The formulas are the user's paste of Appendix A of Kakushadze (2016),
"101 Formulaic Alphas", arXiv:1601.00991, held verbatim in
scripts/data/alpha101_raw.txt and parsed into scripts/data/alpha101.json. They
are transcribed, NOT reconstructed -- the distinction matters, because a
plausible-looking wrong formula would silently test my recollection of the
paper instead of the paper.

WHAT WE CANNOT RUN, and why that is stated rather than patched.
  - `cap` (market capitalisation): we have no share-count series. Blocks 1
    alpha (#56).
  - `IndNeutralize(x, IndClass.*)`: demeans x within an industry or sector, and
    we have no classification for the 1,000 NSE names. Blocks 18.
  Those 19 are EXCLUDED, not approximated. Neutralising against a sector you
  guessed is not neutralising.
  - `vwap`: needs intraday prints; we hold daily bars only. 30 of the remaining
    82 use it. They are run with vwap := (high + low + close) / 3 and reported
    in a SEPARATE, labelled group, because that is a substitution and not the
    alpha. 52 alphas need no substitution at all -- those are the headline set.

TWO JUDGEMENT CALLS THE PAPER DOES NOT SETTLE. Both are flagged here rather
than buried, because either could change a result:
  1. `adv{d}`. The paper's prose says "average daily dollar volume", but 45
     formulas use it as `volume / adv20` or `adv20 < volume`, which is only
     dimensionally coherent if adv is in SHARES. Under the dollar reading
     `adv20 < volume` is false on essentially every bar and those alphas
     collapse to a constant. We take the arithmetic over the prose:
     adv{d} = d-day rolling mean of `volume`. ADV_DOLLAR flips it.
  2. `ts_argmax(x, d)` -- "which day the maximum fell on". Counted here as 1 =
     the most recent bar, rising to d = the oldest, which is the convention
     that makes a recent high score LOW. The opposite convention flips the sign
     of #1, #57, #60, #96 and #100.

POINT-IN-TIME. The panel is NOT forward-filled. A symbol that did not trade on
a session is NaN there, not carried at a stale price, and every rolling window
uses min_periods = its full length, so an alpha is NaN until it has real bars
behind it. That is this project's rule ("reading a value before it could exist
is a bug"), and it costs warmup rather than flattering anything.

Reads:  scripts/data/alpha101.json, and the daily bars via kitelab.frames.
Writes: output/alpha_panel_<n>.pkl (the OHLCV panel, checkpointed -- building
        it means opening ~1,000 parquet files). Nothing under kitelab/.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from kitelab import frames

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "output"
FORMULAS = json.loads((HERE / "data" / "alpha101.json").read_text())

ADV_DOLLAR = False          # see judgement call 1 in the module docstring
VWAP_SUBSTITUTE = True      # (high + low + close) / 3


# ------------------------------------------------------------- parsing ----
# A small recursive-descent parser rather than regex surgery on the text. The
# ternary `a ? b : c` is the reason: it nests three deep in #21, #24 and #46,
# and no regex reads that correctly. The parser emits Python source, which is
# then compiled once per alpha and evaluated against the panel.
_TOKEN = re.compile(r"""
    \s*(?:
      (?P<num>\d+\.\d*|\.\d+|\d+)
    | (?P<name>[A-Za-z_][A-Za-z_0-9.]*)
    | (?P<op><=|>=|==|!=|\|\||&&|[-+*/^<>?:(),])
    )""", re.VERBOSE)


def tokenise(src: str) -> list[tuple[str, str]]:
    out, i = [], 0
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m:
            if src[i:].strip() == "":
                break
            raise ValueError(f"cannot tokenise at {src[i:i+30]!r}")
        i = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


_CMP = {"<": "_lt", ">": "_gt", "<=": "_le",
        ">=": "_ge", "==": "_eq", "!=": "_ne"}


class Parser:
    """C-like precedence, exactly as the formulas are written."""

    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, val=None):
        k, v = self.peek()
        if val is not None and v != val:
            raise ValueError(f"expected {val!r}, found {v!r}")
        self.i += 1
        return v

    def parse(self):
        e = self.ternary()
        if self.i != len(self.t):
            raise ValueError(f"trailing tokens from {self.t[self.i]}")
        return e

    def ternary(self):
        c = self.or_()
        if self.peek()[1] == "?":
            self.take("?")
            a = self.ternary()
            self.take(":")
            b = self.ternary()
            return f"_iif({c}, {a}, {b})"
        return c

    def or_(self):
        e = self.and_()
        while self.peek()[1] == "||":
            self.take()
            e = f"_or({e}, {self.and_()})"
        return e

    def and_(self):
        e = self.cmp_()
        while self.peek()[1] == "&&":
            self.take()
            e = f"_and({e}, {self.cmp_()})"
        return e

    def cmp_(self):
        e = self.add()
        while self.peek()[1] in ("<", ">", "<=", ">=", "==", "!="):
            op = self.take()
            e = f"{_CMP[op]}({e}, {self.add()})"
        return e

    def add(self):
        e = self.mul()
        while self.peek()[1] in ("+", "-"):
            op = self.take()
            e = f"({e} {op} {self.mul()})"
        return e

    def mul(self):
        e = self.pow_()
        while self.peek()[1] in ("*", "/"):
            op = self.take()
            e = f"({e} {op} {self.pow_()})"
        return e

    def pow_(self):
        e = self.unary()
        if self.peek()[1] == "^":                 # right-associative
            self.take()
            return f"powr({e}, {self.pow_()})"
        return e

    def unary(self):
        if self.peek()[1] == "-":
            self.take()
            return f"(-{self.unary()})"
        if self.peek()[1] == "+":
            self.take()
            return self.unary()
        return self.primary()

    def primary(self):
        k, v = self.peek()
        if v == "(":
            self.take("(")
            e = self.ternary()
            self.take(")")
            return f"({e})"
        if k == "num":
            self.take()
            return v if "." in v else f"{v}.0"
        if k == "name":
            self.take()
            if self.peek()[1] == "(":
                self.take("(")
                args = []
                if self.peek()[1] != ")":
                    args.append(self.ternary())
                    while self.peek()[1] == ",":
                        self.take(",")
                        args.append(self.ternary())
                self.take(")")
                return f"{v.lower()}({', '.join(args)})"
            return f"VAR['{v.lower()}']"
        raise ValueError(f"unexpected token {v!r}")


def to_python(formula: str) -> str:
    return Parser(tokenise(formula)).parse()


# ---------------------------------------------------------- operators ----
# Every operand is a DataFrame indexed (session x symbol), or a plain number.
# "Cross-sectional" = across the symbols of one row, i.e. one trading day.
# "ts_" / rolling = down a column, i.e. through time for one symbol.

def _w(d) -> int:
    """Window length. The paper writes some as decimals (3.92795); it gives no
    rounding rule, so we round to nearest and never below 1."""
    return max(1, int(round(float(d))))


def _fr(x, like):
    """Broadcast a scalar to the panel's shape so ops stay frame-to-frame."""
    if isinstance(x, pd.DataFrame):
        return x
    return pd.DataFrame(float(x), index=like.index, columns=like.columns)


def _pair(a, b):
    if isinstance(a, pd.DataFrame):
        return a, _fr(b, a)
    if isinstance(b, pd.DataFrame):
        return _fr(a, b), b
    return a, b


def _cmp(a, b, op):
    """Comparison that PROPAGATES NaN as NaN rather than answering False.
    Returns 1.0 / 0.0 / NaN. See the note in the parser."""
    a, b = _pair(a, b)
    if not isinstance(a, pd.DataFrame):
        return float(op(a, b))
    out = op(a, b).astype("float64")
    return out.where(a.notna() & b.notna())


def _lt(a, b): return _cmp(a, b, lambda x, y: x < y)
def _gt(a, b): return _cmp(a, b, lambda x, y: x > y)
def _le(a, b): return _cmp(a, b, lambda x, y: x <= y)
def _ge(a, b): return _cmp(a, b, lambda x, y: x >= y)
def _eq(a, b): return _cmp(a, b, lambda x, y: x == y)
def _ne(a, b): return _cmp(a, b, lambda x, y: x != y)


def _and(a, b):
    a, b = _pair(a, b)
    return (a * b).clip(0, 1) if isinstance(a, pd.DataFrame) else float(bool(a and b))


def _or(a, b):
    a, b = _pair(a, b)
    return (a + b).clip(0, 1) if isinstance(a, pd.DataFrame) else float(bool(a or b))


def _iif(c, a, b):
    """Ternary. NaN in the condition gives NaN out -- a bar with no data must
    not be handed whichever branch happens to be a constant."""
    if not isinstance(c, pd.DataFrame):
        return a if c else b
    a, b = _fr(a, c), _fr(b, c)
    return a.where(c > 0.5, b).where(c.notna())


def powr(x, p):
    """`^`. A negative base with a fractional exponent is NaN, not an error."""
    if isinstance(p, pd.DataFrame) or isinstance(x, pd.DataFrame):
        x, p = _pair(x, p)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.power(x, p) if not isinstance(x, pd.DataFrame) else \
            pd.DataFrame(np.power(x.to_numpy(), np.asarray(p if not isinstance(p, pd.DataFrame) else p.to_numpy())),
                         index=x.index, columns=x.columns)
    return out.replace([np.inf, -np.inf], np.nan) if isinstance(out, pd.DataFrame) else out


# --- cross-sectional (one trading day at a time) --------------------------
def rank(x):
    """Percentile rank across symbols on each day, in (0, 1]. This is the
    operator that makes an alpha a RANKING rather than a prediction."""
    return x.rank(axis=1, pct=True)


def scale(x, a=1.0):
    """Rescale so the absolute values across one day sum to `a`."""
    denom = x.abs().sum(axis=1).replace(0.0, np.nan)
    return x.div(denom, axis=0) * float(a)


def indneutralize(x, *_):
    raise NotImplementedError("no industry classification -- alpha is excluded")


# --- elementwise ----------------------------------------------------------
def signedpower(x, p):
    return powr(x.abs(), p) * np.sign(x)


def sign(x): return np.sign(x)
def log(x): return np.log(x.where(x > 0))
def abs_(x): return x.abs()
def min_(a, b): a, b = _pair(a, b); return np.minimum(a, b)
def max_(a, b): a, b = _pair(a, b); return np.maximum(a, b)


# --- time series (down each symbol's own column) --------------------------
def delay(x, d): return x.shift(_w(d))
def delta(x, d): return x - x.shift(_w(d))


def _roll(x, d):
    """min_periods = the full window: an alpha is NaN until it has real bars
    behind it, rather than computed off two observations."""
    n = _w(d)
    return x.rolling(n, min_periods=n)


def ts_min(x, d): return _roll(x, d).min()
def ts_max(x, d): return _roll(x, d).max()
def ts_sum(x, d): return _roll(x, d).sum()
def sum_(x, d): return ts_sum(x, d)
def stddev(x, d): return _roll(x, d).std()
def product(x, d): return _roll(x, d).apply(np.prod, raw=True)


def ts_argmax(x, d):
    """Days since the window's maximum: 1 = today, rising to d = the oldest
    bar. See judgement call 2 in the module docstring."""
    n = _w(d)
    return _roll(x, d).apply(lambda v: n - int(np.argmax(v)), raw=True)


def ts_argmin(x, d):
    n = _w(d)
    return _roll(x, d).apply(lambda v: n - int(np.argmin(v)), raw=True)


def ts_rank(x, d):
    """Where today's value sits within the trailing d values, in (0, 1]."""
    n = _w(d)
    return _roll(x, d).apply(lambda v: (v <= v[-1]).sum() / n, raw=True)


def correlation(x, y, d):
    n = _w(d)
    out = x.rolling(n, min_periods=n).corr(y)
    return out.replace([np.inf, -np.inf], np.nan)


def covariance(x, y, d):
    n = _w(d)
    return x.rolling(n, min_periods=n).cov(y)


def decay_linear(x, d):
    """Weighted mean over d days, weights d, d-1, ... 1 normalised to sum 1 --
    so the most recent bar counts most."""
    n = _w(d)
    # A rolling window arrives OLDEST-first, so the weights ascend: the last
    # slot (today) gets n, the first (n-1 days ago) gets 1. Writing them the
    # other way round silently reverses the operator's meaning.
    wts = np.arange(1, n + 1, dtype="float64")
    wts /= wts.sum()
    return x.rolling(n, min_periods=n).apply(lambda v: float(v @ wts), raw=True)


# `min(x, d)` / `max(x, d)`: elementwise, NOT rolling. The paper spells
# `ts_min` / `ts_max` out at all 14 sites where it means the rolling kind, and
# 9 of the 10 bare calls take a DataFrame as the second argument, which a
# window cannot be. The lone scalar case is Alpha#29's `min(product(...), 1), 5)`,
# which is degenerate under EITHER reading: the inner value is a product of
# percentile ranks and so never exceeds 1.
NAMESPACE = {
    "rank": rank, "scale": scale, "signedpower": signedpower,
    "sign": sign, "log": log, "abs": abs_, "min": min_, "max": max_,
    "delay": delay, "delta": delta, "ts_min": ts_min, "ts_max": ts_max,
    "ts_sum": ts_sum, "sum": sum_, "stddev": stddev, "product": product,
    "ts_argmax": ts_argmax, "ts_argmin": ts_argmin, "ts_rank": ts_rank,
    "correlation": correlation, "covariance": covariance,
    "decay_linear": decay_linear, "indneutralize": indneutralize,
    "powr": powr, "_iif": _iif, "_and": _and, "_or": _or,
    "_lt": _lt, "_gt": _gt, "_le": _le, "_ge": _ge, "_eq": _eq, "_ne": _ne,
    "np": np, "pd": pd,
}


class Vars(dict):
    """Panel lookup that names what is missing instead of raising KeyError."""

    def __missing__(self, key):
        if key == "cap":
            raise NotImplementedError("no share counts -- market cap unavailable")
        if key.startswith("indclass"):
            raise NotImplementedError("no industry classification")
        raise NotImplementedError(f"no data field {key!r}")


# ------------------------------------------------------------- panel -----
def build_panel(symbols, start="2005-01-01"):
    """Five aligned (session x symbol) frames from the daily bars.

    NOT forward-filled: a symbol that did not trade on a session is NaN there,
    not carried at yesterday's price. Adds `returns` (close-to-close) and, if
    VWAP_SUBSTITUTE, a typical-price stand-in for `vwap`.
    """
    cols = {}
    kept, skipped = 0, 0
    for sym in symbols:
        try:
            df = frames.daily(sym)
        except Exception:
            skipped += 1
            continue
        if df is None or df.empty:
            skipped += 1
            continue
        df = df[df["ts"] >= pd.Timestamp(start)]
        if df.empty:
            skipped += 1
            continue
        cols[sym] = df.set_index("ts")[["open", "high", "low", "close", "volume"]]
        kept += 1
    print(f"  symbols with daily bars: {kept} (skipped {skipped})")

    panel = Vars()
    for field in ("open", "high", "low", "close", "volume"):
        wide = pd.DataFrame({s: d[field] for s, d in cols.items()})
        wide = wide.sort_index()
        wide = wide[~wide.index.duplicated(keep="last")]
        panel[field] = wide.astype("float64")
    panel["returns"] = panel["close"].pct_change()
    if VWAP_SUBSTITUTE:
        panel["vwap"] = (panel["high"] + panel["low"] + panel["close"]) / 3.0
    print(f"  panel: {panel['close'].shape[0]:,} sessions x "
          f"{panel['close'].shape[1]:,} symbols, "
          f"{panel['close'].index.min().date()} to {panel['close'].index.max().date()}")
    return panel


_ADV_CACHE: dict = {}


def _install_adv(panel):
    """adv{d} = d-day rolling mean of VOLUME (shares). See judgement call 1."""
    base = panel["close"] * panel["volume"] if ADV_DOLLAR else panel["volume"]

    def make(d):
        if d not in _ADV_CACHE:
            _ADV_CACHE[d] = base.rolling(d, min_periods=d).mean()
        return _ADV_CACHE[d]

    for d in sorted({int(m) for f in FORMULAS.values()
                     for m in re.findall(r"\badv(\d+)\b", f)}):
        panel[f"adv{d}"] = make(d)


def evaluate(num: str, panel) -> pd.DataFrame:
    """Compute one alpha over the whole panel. Returns (session x symbol)."""
    src = to_python(FORMULAS[str(num)])
    ns = dict(NAMESPACE)
    ns["VAR"] = panel
    _install_adv(panel)                 # adv{d} are panel FIELDS -- bare names, not calls
    out = eval(compile(src, f"<alpha{num}>", "eval"), ns)
    if not isinstance(out, pd.DataFrame):
        out = _fr(out, panel["close"])
    return out.where(panel["close"].notna()).replace([np.inf, -np.inf], np.nan)
