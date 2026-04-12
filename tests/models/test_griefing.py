"""Tests for Painter and GriefReport types."""

from pixel_hawk.models.griefing import GriefReport, Painter


def test_grief_report_falsy_when_no_regress():
    """GriefReport with regress_count=0 is falsy, even with painters."""
    painters = (Painter(user_id=1, user_name="A", alliance_name="", discord_id="", discord_name=""),)
    assert not GriefReport()
    assert not GriefReport(regress_count=0, painters=painters)


def test_grief_report_truthy_with_regress_count():
    """GriefReport is truthy when regress_count > 0, regardless of painters."""
    assert GriefReport(regress_count=1)
    assert GriefReport(regress_count=50, painters=())
    assert GriefReport(
        regress_count=10,
        painters=(Painter(user_id=1, user_name="A", alliance_name="", discord_id="", discord_name=""),),
    )
