from __future__ import annotations

from datetime import date

from study_agent.grounding import FactIndex, check

TODAY = date(2026, 11, 23)
TOOL_RESULT = (
    '{"deadlines": [{"course": "CS-E4780", "due": "2026-11-27T23:59+02:00",'
    ' "due_local": "Fri 27 Nov 23:59", "title": "Report"}], "range": {"from": "2026-11-23",'
    ' "to": "2026-11-30"}}'
)


def facts(*texts: str) -> FactIndex:
    index = FactIndex(TODAY)
    index.add_all(texts)
    return index


def test_grounded_answer_passes() -> None:
    report = check("CS-E4780 Report, due Fri 27 Nov 23:59 (mycourses.ics).", facts(TOOL_RESULT))
    assert report.ok
    assert report.checked == 3


def test_invented_date_time_and_course_are_flagged() -> None:
    report = check("CS-E9999 exam on Mon 14 Dec 09:00.", facts(TOOL_RESULT))
    kinds = sorted(c.kind for c in report.unsupported)
    assert kinds == ["course", "date", "time"]


def test_wrong_weekday_for_a_real_date_is_flagged() -> None:
    report = check("Due Sat 27 Nov 23:59.", facts(TOOL_RESULT))
    assert [c.kind for c in report.unsupported] == ["weekday"]
    assert "Friday" in report.unsupported[0].value


def test_date_formats_are_recognised() -> None:
    index = facts(TOOL_RESULT)
    for text in ("27 Nov", "Nov 27", "27 November 2026", "2026-11-27", "27.11.", "Friday, 27 Nov"):
        assert check(f"Due {text}.", index).ok, text
    for text in ("28 Nov", "Nov 28", "2026-11-28", "28.11.2026"):
        assert not check(f"Due {text}.", index).ok, text


def test_time_after_a_date_is_not_read_as_a_second_date() -> None:
    report = check("Fri 27 Nov 23:59", facts(TOOL_RESULT))
    assert report.ok
    assert report.checked == 2  # one date and one time; no phantom "Nov 23"


def test_today_and_question_dates_count_as_given() -> None:
    assert check("Today, Mon 23 Nov, nothing is due.", facts()).ok
    assert check("Nothing is due on 2 Dec.", facts("What is due on 2 Dec?")).ok


def test_year_is_resolved_to_the_nearest_occurrence() -> None:
    index = FactIndex(date(2026, 12, 30))
    index.add('"due": "2027-01-04T12:00+02:00"')
    assert check("Due 4 Jan 12:00.", index).ok


def test_iso_datetime_times_are_extracted() -> None:
    index = facts('{"start": "2026-11-23T09:00+02:00", "end": "2026-11-23T10:15+02:00"}')
    assert check("Study 09:00-10:15.", index).ok
    assert not check("Study 09:00-10:30.", index).ok
