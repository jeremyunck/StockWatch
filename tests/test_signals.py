"""Unit tests for the deterministic (non-LLM) logic: signal derivation,
the LLM-call gate, and embed formatting. No network required."""

from discord_out import build_embed, COLORS
from indicators import derive_signal
from llm import should_call_llm


# --- derive_signal -----------------------------------------------------------

def test_derive_signal_strong_buy():
    ind = {
        "price_above_sma200": True,
        "ema20_rising": True,
        "rsi14": 55, "rsi_rising": True,
        "macd_cross": "bullish",
        "volume_ratio": 1.5,
        "obv_trend": "rising",
        "bb_position": "lower",
    }
    assert derive_signal(ind, price=100.0) == "LEAN_BUY"


def test_derive_signal_strong_sell():
    ind = {
        "price_above_sma200": False,
        "ema20_rising": False,
        "rsi14": 75, "rsi_rising": False,
        "macd_cross": "bearish",
        "volume_ratio": 1.5,
        "obv_trend": "falling",
        "bb_position": "upper",
    }
    assert derive_signal(ind, price=100.0) == "LEAN_SELL"


def test_derive_signal_defaults_to_hold():
    assert derive_signal({}, price=100.0) == "HOLD"


# --- should_call_llm gate ----------------------------------------------------

def test_gate_always_when_disabled():
    assert should_call_llm("X", "HOLD", {"label": "HOLD"}, False, only_on_new_signal=False)


def test_gate_skips_when_nothing_changed():
    last = {"label": "HOLD"}
    assert not should_call_llm("X", "HOLD", last, has_new_news=False, only_on_new_signal=True)


def test_gate_fires_on_new_news():
    last = {"label": "HOLD"}
    assert should_call_llm("X", "HOLD", last, has_new_news=True, only_on_new_signal=True)


def test_gate_fires_on_signal_change():
    last = {"label": "HOLD"}
    assert should_call_llm("X", "LEAN_BUY", last, has_new_news=False, only_on_new_signal=True)


def test_gate_fires_when_no_history():
    assert should_call_llm("X", "HOLD", None, has_new_news=False, only_on_new_signal=True)


# --- build_embed: only the AI section is dynamic -----------------------------

QUOTE = {"price": 100.0, "change": 1.5, "change_pct": 1.52}
INDICATORS = {"sma50": 95.0, "sma200": 90.0, "price_above_sma200": True, "rsi14": 55.0}


def test_embed_has_consistent_structure_without_llm():
    embed = build_embed("AMD", "Advanced Micro Devices", QUOTE, INDICATORS,
                        "LEAN_BUY", news=[], llm_read=None)
    names = [f["name"] for f in embed["fields"]]
    assert any(n.startswith("Signal:") for n in names)
    assert "Trend" in names and "Momentum" in names and "Recent News" in names
    # No AI field when the LLM was not called.
    assert not any("AI Read" in n for n in names)
    assert embed["color"] == COLORS["LEAN_BUY"]


def test_embed_appends_ai_section_when_present():
    embed = build_embed("AMD", "Advanced Micro Devices", QUOTE, INDICATORS,
                        "LEAN_BUY", news=[], llm_read="Summary text here.")
    assert any("AI Read" in f["name"] for f in embed["fields"])
