"""Smoke tests for dsbot - exactly 3 tests as required."""

from pathlib import Path


def test_package_imports():
    """Test that dsbot package and all submodules can be imported."""
    import dsbot
    import dsbot.approval
    import dsbot.audit
    import dsbot.cli
    import dsbot.data.clean
    import dsbot.data.connectors
    import dsbot.data.excel
    import dsbot.data.extract
    import dsbot.data.report
    import dsbot.email.connectors
    import dsbot.email.draft
    import dsbot.email.triage
    import dsbot.llm
    import dsbot.meeting.conference
    import dsbot.meeting.connectors
    import dsbot.meeting.scheduler

    assert dsbot.__version__ == "0.1.0"


def test_claude_md_exists():
    """Test that CLAUDE.md exists in project root."""
    claude_md = Path(__file__).parent.parent / "CLAUDE.md"
    assert claude_md.exists(), "CLAUDE.md not found in project root"
    content = claude_md.read_text()
    assert "PURPOSE" in content
    assert "RULES" in content
    assert "Human-in-the-loop" in content
    assert "All numbers in reports and Excel come from pandas code" in content


def test_outputs_folder_exists():
    """Test that outputs/ folder exists."""
    outputs_dir = Path(__file__).parent.parent / "outputs"
    assert outputs_dir.exists(), "outputs/ folder not found"
    assert outputs_dir.is_dir(), "outputs/ is not a directory"
