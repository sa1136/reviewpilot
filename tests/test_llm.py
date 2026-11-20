import pytest
from langchain_core.messages import AIMessage

from reviewpilot.config import estimate_cost
from reviewpilot.llm import FakeModel, call_model, run_totals, usage_entry
from reviewpilot.models import AgentOutput


def raw_with_usage(input_tokens, output_tokens, cache_read=0, cache_creation=0):
    return AIMessage(
        content="",
        usage_metadata={
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "input_token_details": {"cache_read": cache_read, "cache_creation": cache_creation},
        },
    )


def test_estimate_cost_haiku():
    # 50k uncached input at $0.10/M + 50k output at $0.50/M (prompt under the 100k tier)
    assert estimate_cost("claude-haiku-5-5", 50_000, 50_000) == pytest.approx(0.03)


def test_estimate_cost_with_cache():
    # 10k prompt: 6k read from cache, 2k written, 2k uncached; 1k output (Sonnet 5.5)
    cost = estimate_cost(
        "claude-sonnet-5-5", 10_000, 1_000, cache_read_tokens=6_000, cache_write_tokens=2_000
    )
    expected = (2_000 * 2.00 + 6_000 * 0.10 + 2_000 * 2.50 + 1_000 * 10.00) / 1_000_000
    assert cost == pytest.approx(expected)


def test_estimate_cost_haiku_long_prompt_tier():
    cost = estimate_cost("claude-haiku-5-5", 200_000, 0)
    assert cost == pytest.approx(200_000 * 0.50 / 1_000_000)


def test_estimate_cost_unknown_model():
    assert estimate_cost("some-other-model", 100, 100) is None


def test_usage_entry_reads_usage_metadata():
    raw = raw_with_usage(3_000, 500, cache_read=1_000, cache_creation=800)
    entry = usage_entry("security", 2, "claude-haiku-5-5", raw, latency_ms=1234)
    assert entry == {
        "node": "security",
        "round": 2,
        "model": "claude-haiku-5-5",
        "input_tokens": 3_000,
        "output_tokens": 500,
        "cache_read_tokens": 1_000,
        "cache_write_tokens": 800,
        "latency_ms": 1234,
        "cost_usd": pytest.approx(
            (1_200 * 0.10 + 1_000 * 0.01 + 800 * 0.125 + 500 * 0.50) / 1_000_000
        ),
    }


def test_call_model_with_raw_output_records_usage():
    model = FakeModel(
        [{"raw": raw_with_usage(100, 20), "parsed": AgentOutput(), "parsing_error": None}]
    )
    result = call_model(model, [], "quality", 1, "claude-haiku-5-5")
    assert result.error is None
    assert isinstance(result.parsed, AgentOutput)
    assert result.usage["input_tokens"] == 100 and result.usage["output_tokens"] == 20


def test_call_model_unparseable_output_is_an_error_but_usage_is_kept():
    raw = raw_with_usage(100, 20)
    raw.response_metadata = {"stop_reason": "max_tokens"}
    model = FakeModel([{"raw": raw, "parsed": None, "parsing_error": ValueError("bad json")}])
    result = call_model(model, [], "quality", 1, "claude-haiku-5-5")
    assert result.parsed is None
    assert "max_tokens" in result.error and "bad json" in result.error
    assert result.usage["output_tokens"] == 20


def test_call_model_plain_fake_has_zero_usage():
    result = call_model(FakeModel([AgentOutput()]), [], "quality", 1, "claude-haiku-5-5")
    assert result.usage["input_tokens"] == 0
    assert result.usage["cost_usd"] == 0


def test_run_totals():
    usage = [
        {"model": "m", "input_tokens": 10, "output_tokens": 1, "cost_usd": 0.5},
        {"model": "m", "input_tokens": 5, "output_tokens": 2, "cost_usd": 0.25},
    ]
    assert run_totals(usage) == {
        "input_tokens": 15,
        "output_tokens": 3,
        "cost_usd": 0.75,
        "cost_complete": True,
    }
    usage.append({"model": "unknown", "input_tokens": 1, "output_tokens": 1, "cost_usd": None})
    assert run_totals(usage)["cost_complete"] is False
