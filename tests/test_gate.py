"""Connector gate and data command tests."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dsbot.approvals import create_approval
from dsbot.cli import app
from dsbot.connectors.base import MailProvider
from dsbot.connectors.mock_calendar import MockCalendarProvider
from dsbot.db import get_db
from dsbot.email.connectors import MockEmailConnector

SAMPLE_CSV = Path(__file__).parents[1] / "data" / "samples" / "dirty_sales.csv"


def test_data_run_builds_workbook_for_sample_csv(tmp_path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    output_path = tmp_path / "sales.xlsx"

    result = CliRunner().invoke(
        app,
        ["data", "run", str(SAMPLE_CSV), "--output", str(output_path)],
    )

    assert result.exit_code == 0, result.output
    assert output_path.is_file()
    assert str(output_path) in result.output


def test_calendar_refuses_unapproved_approval_without_creating_event(tmp_path, monkeypatch):
    monkeypatch.setenv("DSBOT_DB_PATH", str(tmp_path / "dsbot.db"))
    approval_id = create_approval("meeting_reply", {"topic": "Review", "slots": []})
    approval_row = get_db().fetch_one(
        "SELECT rowid FROM approvals WHERE id = ?",
        (approval_id,),
    )
    assert approval_row is not None
    calendar_path = tmp_path / "calendar.json"
    calendar = MockCalendarProvider(calendar_path)

    with pytest.raises(PermissionError):
        calendar.create_event(
            "Review",
            datetime(2026, 10, 26, 10, tzinfo=UTC),
            datetime(2026, 10, 26, 11, tzinfo=UTC),
            approval_id=int(approval_row["rowid"]),
        )

    assert not calendar_path.exists()
    refused = get_db().fetch_one(
        "SELECT action FROM audit_log WHERE action = 'calendar_event_creation_refused'"
    )
    assert refused is not None


def test_mail_interfaces_and_mock_have_no_send_method():
    assert not hasattr(MailProvider, "send")
    assert not hasattr(MockEmailConnector, "send")
