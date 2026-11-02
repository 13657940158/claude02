"""导读计划规则兜底的回归测试。"""
from app.models.book import Chapter
from app.services.plans import _fallback_sessions


def _chapters(count: int = 41) -> list[Chapter]:
    return [
        Chapter(chapter_no=number, title=f"第{number}章", char_count=1000)
        for number in range(1, count + 1)
    ]


def test_fallback_sessions_start_pending_and_cover_all_chapters() -> None:
    sessions = _fallback_sessions(_chapters(), 5, 20, 100)

    assert len(sessions) == 5
    # 新计划的所有天都应是待读，不能把最后一天预先标成 done
    assert all(session["status"] == "pending" for session in sessions)
    assert sessions[0]["chapter_from"] == 1
    assert sessions[-1]["chapter_to"] == 41
    for previous, current in zip(sessions, sessions[1:], strict=False):
        assert current["chapter_from"] == previous["chapter_to"] + 1


def test_fallback_repeats_last_chapter_when_days_exceed_chapters() -> None:
    sessions = _fallback_sessions(_chapters(3), 5, 20, 100)

    assert len(sessions) == 5
    assert [session["chapter_to"] for session in sessions] == [1, 2, 3, 3, 3]
    assert all(session["status"] == "pending" for session in sessions)
