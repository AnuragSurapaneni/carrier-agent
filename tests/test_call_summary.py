"""
Post-call structured summary test — the spec's one micro-seam.

The summary-extraction function accepts an injected chat model
(a function parameter, not an architectural seam). One fake model —
a minimal stand-in exposing the structured-output interface the
extraction function uses — returns a fixed CallSummary, and this
test pins the resulting CallSummary -> SQLite call_log shape with
no LLM call and no network: the table exists with the unchanged
schema, and the saved row's columns and values match the
CallSummary, including the agreed_rate parsing.
"""

import datetime
import sqlite3

from agent import inbound_carrier_sales as agent

# A finished call, condensed to the shape the extractor sees.
TRANSCRIPT = (
    "Nico: Hey, this is Nico with carrier sales. What's your MC number?\n"
    "Carrier: It's MC 1234567.\n"
    "Nico: You're verified. I've got a reefer from Chicago, Illinois.\n"
    "Carrier: I'll take five thousand three hundred twenty-six fifty.\n"
    "Nico: Perfect, locking that in. You're all set, connecting you to a senior rep."
)


class FakeStructuredModel:
    """
    Minimal stand-in for a chat model: exposes the structured-output
    interface the extraction function uses — with_structured_output
    returning a runnable whose .invoke takes the prompt and returns
    the fixed CallSummary. No LLM, no network.
    """

    def __init__(self, summary):
        self.summary = summary
        self.schema = None
        self.calls = []

    def with_structured_output(self, schema):
        self.schema = schema
        fake = self

        class _Runnable:
            def invoke(self, prompt):
                fake.calls.append(prompt)
                return fake.summary

        return _Runnable()


def test_extracted_summary_saves_the_call_log_row(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "DB_PATH", str(tmp_path / "call_log.db"))

    summary = agent.CallSummary(
        mc_number="1234567",
        outcome="booked",
        sentiment="positive",
        load_id="TEST-CHI-REEFER",
        agreed_rate="$5,326.50",
        language="en",
        notes="Carrier booked the Chicago reefer at the accepted rate.",
    )
    fake = FakeStructuredModel(summary)

    extracted = agent.extract_summary(TRANSCRIPT, model=fake)

    # Structured output was requested against the CallSummary schema,
    # and the transcript reached the model.
    assert fake.schema is agent.CallSummary
    [prompt] = fake.calls
    assert [message.type for message in prompt] == ["system", "human"]
    assert prompt[1].content == TRANSCRIPT
    assert extracted == summary

    # The empty-string defaults hold for values that never came up.
    blank = agent.CallSummary(outcome="no_load")
    assert (
        blank.mc_number,
        blank.sentiment,
        blank.load_id,
        blank.agreed_rate,
        blank.language,
        blank.notes,
    ) == ("", "neutral", "", "", "", "")

    agent.save_summary("test-run", extracted)

    con = sqlite3.connect(agent.DB_PATH)
    try:
        # The table exists with the unchanged schema.
        assert [
            (name, col_type, is_pk)
            for _, name, col_type, _, _, is_pk in con.execute(
                "PRAGMA table_info(call_log)"
            )
        ] == [
            ("run_id", "TEXT", 1),
            ("mc_number", "TEXT", 0),
            ("outcome", "TEXT", 0),
            ("sentiment", "TEXT", 0),
            ("load_id", "TEXT", 0),
            ("agreed_rate", "REAL", 0),
            ("language", "TEXT", 0),
            ("notes", "TEXT", 0),
            ("created_at", "TEXT", 0),
        ]

        # The saved row's columns and values match the CallSummary;
        # the agreed rate is parsed to a number ($ and , stripped).
        (row,) = con.execute(
            "SELECT * FROM call_log WHERE run_id = ?", ("test-run",)
        )
        assert row[:8] == (
            "test-run",
            "1234567",
            "booked",
            "positive",
            "TEST-CHI-REEFER",
            5326.5,
            "en",
            "Carrier booked the Chicago reefer at the accepted rate.",
        )
        # created_at is an ISO-8601 timestamp.
        datetime.datetime.fromisoformat(row[8])

        # A rate that never came up stores as NULL.
        no_rate = agent.CallSummary(outcome="no_load")
        agent.save_summary("test-run-2", no_rate)
        (row2,) = con.execute(
            "SELECT agreed_rate FROM call_log WHERE run_id = ?",
            ("test-run-2",),
        )
        assert row2[0] is None
    finally:
        con.close()
