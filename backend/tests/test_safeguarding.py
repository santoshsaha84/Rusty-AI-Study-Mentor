"""
Safeguarding is a non-negotiable safety feature — every phrase must trigger,
every clean query must pass. These tests are the gating condition before pilot launch.
"""
import json
import pytest
from pathlib import Path

from app.middleware.safeguarding import tier1_scan

PHRASES_PATH = Path(__file__).parent.parent.parent / "safeguarding" / "phrases_v1.json"
CASES_PATH = Path(__file__).parent.parent.parent / "safeguarding" / "test_cases.json"


def test_phrases_file_exists():
    assert PHRASES_PATH.exists(), "phrases_v1.json is missing — app cannot start"


def test_phrases_file_not_empty():
    data = json.loads(PHRASES_PATH.read_text(encoding="utf-8"))
    assert len(data.get("tier1_phrases", [])) > 0, "Phrase list is empty"


def test_tier1_scan_triggers_on_known_phrases():
    """All phrases in phrases_v1.json must trigger tier1_scan."""
    data = json.loads(PHRASES_PATH.read_text(encoding="utf-8"))
    phrases = data["tier1_phrases"]
    failures = []
    for phrase in phrases:
        if not tier1_scan(phrase):
            failures.append(phrase)
    assert not failures, f"These phrases did NOT trigger scan: {failures}"


def test_tier1_scan_case_insensitive():
    assert tier1_scan("SUICIDE")
    assert tier1_scan("Suicide")
    assert tier1_scan("suICIde")


def test_tier1_scan_partial_match():
    assert tier1_scan("I am thinking about suicide today")
    assert tier1_scan("mujhe maarna hai abhi")


def test_tier1_scan_clean_queries_pass():
    clean = [
        "What is photosynthesis?",
        "Solve x^2 + 5x + 6 = 0",
        "What is the capital of Bihar?",
        "Explain the water cycle",
        "Who wrote the Ramayana?",
        "What are the properties of a triangle?",
    ]
    for q in clean:
        assert not tier1_scan(q), f"Clean query incorrectly flagged: {q!r}"


def test_test_cases_file():
    """Run all must_trigger / must_not_trigger cases from test_cases.json."""
    data = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    failures = []
    for case in data.get("cases", []):
        text = case["text"]
        must_trigger = case["must_trigger"]
        result = tier1_scan(text)
        if result != must_trigger:
            failures.append(
                f"[{case['label']}] text={text!r} must_trigger={must_trigger} got={result}"
            )
    assert not failures, "Safeguarding test case failures:\n" + "\n".join(failures)


def test_tier1_does_not_raise_on_empty_string():
    assert tier1_scan("") is False


def test_tier1_does_not_raise_on_very_long_string():
    long_text = "a" * 10_000
    assert tier1_scan(long_text) is False
