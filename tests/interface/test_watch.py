"""Tests for living watch message service layer (watch.py)."""

import time

import pytest

from pixel_hawk.interface.access import ErrorMsg
from pixel_hawk.interface.watch import (
    create_watch,
    delete_watches_for_project,
    format_grief_message,
    format_watch_message,
    get_watch_image_paths,
    get_watches_for_projects,
    remove_watch,
    save_watch_message,
)
from pixel_hawk.models.entities import (
    DiffStatus,
    HistoryChange,
    Person,
    ProjectInfo,
    ProjectState,
    WatchMessage,
)
from pixel_hawk.models.geometry import Point, Rectangle, Size
from pixel_hawk.models.griefing import GriefReport, Painter
from pixel_hawk.watcher.projects import Project

RECT = Rectangle.from_point_size(Point(500_000, 600_000), Size(100, 100))


async def _person_and_project(*, state=ProjectState.ACTIVE) -> tuple[Person, ProjectInfo]:
    person = await Person.create(name="Watcher", discord_id=77777)
    info = await ProjectInfo.from_rect(RECT, person.id, "test project")
    if state != ProjectState.ACTIVE:
        info.state = state
        await info.save()
    return person, info


# format_watch_message tests


class TestFormatWatchMessage:
    async def test_creating_state(self):
        person = await Person.create(name="Bob", discord_id=10001)
        info = ProjectInfo(owner_id=person.id, name="wip", state=ProjectState.CREATING, width=50, height=50)
        await info.save_as_new()
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "CREATING" in result
        assert "wip" in result

    async def test_inactive_state(self):
        person, info = await _person_and_project(state=ProjectState.INACTIVE)
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "INACTIVE" in result
        assert "not being monitored" in result.lower()

    async def test_never_checked(self):
        person, info = await _person_and_project()
        info.last_check = 0
        await info.save()
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "Not yet checked" in result

    async def test_in_progress(self):
        person, info = await _person_and_project()
        now = round(time.time())
        info.last_check = now
        info.total_progress = 500
        info.total_regress = 23
        await info.save()
        await HistoryChange.create(
            project=info,
            timestamp=now,
            status=DiffStatus.IN_PROGRESS,
            num_remaining=1234,
            num_target=2500,
            completion_percent=50.6,
            progress_pixels=50,
            regress_pixels=3,
        )
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "50.6%" in result
        assert "1,234" in result
        assert "2,500" in result
        assert "+50" in result
        assert "-3" in result
        assert "+500" in result  # lifetime
        assert "Last checked" in result

    async def test_complete(self):
        person, info = await _person_and_project()
        now = round(time.time())
        info.last_check = now
        info.max_completion_time = now - 3600
        await info.save()
        await HistoryChange.create(
            project=info,
            timestamp=now,
            status=DiffStatus.COMPLETE,
            num_remaining=0,
            num_target=2500,
            completion_percent=100.0,
        )
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "Complete" in result
        assert "2,500" in result

    async def test_not_started(self):
        person, info = await _person_and_project()
        now = round(time.time())
        info.last_check = now
        await info.save()
        await HistoryChange.create(
            project=info,
            timestamp=now,
            status=DiffStatus.NOT_STARTED,
            num_remaining=0,
            num_target=1000,
            completion_percent=0.0,
        )
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "Not started" in result
        assert "1,000" in result

    async def test_rate_and_eta(self):
        person, info = await _person_and_project()
        now = round(time.time())
        info.last_check = now
        info.recent_rate_pixels_per_hour = 10.5
        await info.save()
        await HistoryChange.create(
            project=info,
            timestamp=now,
            status=DiffStatus.IN_PROGRESS,
            num_remaining=500,
            num_target=1000,
            completion_percent=50.0,
        )
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "10.5 px/hr" in result
        assert "ETA:" in result

    async def test_negative_rate(self):
        person, info = await _person_and_project()
        now = round(time.time())
        info.last_check = now
        info.recent_rate_pixels_per_hour = -2.0
        await info.save()
        await HistoryChange.create(
            project=info,
            timestamp=now,
            status=DiffStatus.IN_PROGRESS,
            num_remaining=500,
            num_target=1000,
            completion_percent=50.0,
        )
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "-2.0 px/hr" in result
        assert "ETA:" not in result

    async def test_best_completion_shown_when_in_progress(self):
        person, info = await _person_and_project()
        now = round(time.time())
        info.last_check = now
        info.max_completion_percent = 75.0
        info.max_completion_time = now - 7200
        await info.save()
        await HistoryChange.create(
            project=info,
            timestamp=now,
            status=DiffStatus.IN_PROGRESS,
            num_remaining=300,
            num_target=1000,
            completion_percent=70.0,
        )
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "Best: 75.0%" in result

    async def test_worst_grief(self):
        person, info = await _person_and_project()
        now = round(time.time())
        info.last_check = now
        info.largest_regress_pixels = 42
        info.largest_regress_time = now - 3600
        await info.save()
        await HistoryChange.create(
            project=info,
            timestamp=now,
            status=DiffStatus.IN_PROGRESS,
            num_remaining=500,
            num_target=1000,
            completion_percent=50.0,
        )
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "Worst grief: 42 px" in result

    async def test_24h_activity(self):
        person, info = await _person_and_project()
        now = round(time.time())
        info.last_check = now
        await info.save()
        # Create a recent change within 24h
        await HistoryChange.create(
            project=info,
            timestamp=now - 100,
            status=DiffStatus.IN_PROGRESS,
            num_remaining=600,
            num_target=1000,
            completion_percent=40.0,
            progress_pixels=30,
            regress_pixels=5,
        )
        await HistoryChange.create(
            project=info,
            timestamp=now,
            status=DiffStatus.IN_PROGRESS,
            num_remaining=500,
            num_target=1000,
            completion_percent=50.0,
            progress_pixels=100,
            regress_pixels=0,
        )
        await info.fetch_related_owner()
        result = await format_watch_message(info)
        assert "Last 24h: +130 / -5" in result


# create_watch tests


class TestCreateWatch:
    async def test_no_person(self):
        with pytest.raises(ErrorMsg, match="No linked account"):
            await create_watch(99999, 1, 100, 500)

    async def test_project_not_found(self):
        await Person.create(name="X", discord_id=20001)
        with pytest.raises(ErrorMsg, match="not found"):
            await create_watch(20001, 9999, 100, 500)

    async def test_not_owner(self):
        person1 = await Person.create(name="Owner", discord_id=30001)
        await Person.create(name="Other", discord_id=30002)
        info = await ProjectInfo.from_rect(RECT, person1.id, "mine")
        with pytest.raises(ErrorMsg, match="not yours"):
            await create_watch(30002, info.id, 100, 500)

    async def test_duplicate_watch(self):
        person, info = await _person_and_project()
        await WatchMessage.create(project_id=info.id, channel_id=100, message_id=999)
        with pytest.raises(ErrorMsg, match="already being watched.*discord.com/channels/500/100/999"):
            await create_watch(77777, info.id, 100, 500)

    async def test_success(self):
        person, info = await _person_and_project()
        info.last_check = round(time.time())
        await info.save()
        content, returned_info = await create_watch(77777, info.id, 200, 500)
        assert returned_info.id == info.id
        assert "test project" in content

    async def test_different_channel_allowed(self):
        person, info = await _person_and_project()
        await WatchMessage.create(project_id=info.id, channel_id=100, message_id=999)
        content, returned_info = await create_watch(77777, info.id, 200, 500)
        assert returned_info.id == info.id


# save_watch_message tests


async def test_save_watch_message():
    person, info = await _person_and_project()
    await save_watch_message(info.id, 300, 12345)
    watch = await WatchMessage.get_by_project_channel(info.id, 300)
    assert watch is not None
    assert watch.message_id == 12345


# remove_watch tests


class TestRemoveWatch:
    async def test_no_person(self):
        with pytest.raises(ErrorMsg, match="No linked account"):
            await remove_watch(99999, 1, 100)

    async def test_project_not_found(self):
        await Person.create(name="X", discord_id=40001)
        with pytest.raises(ErrorMsg, match="not found"):
            await remove_watch(40001, 9999, 100)

    async def test_not_owner(self):
        person1 = await Person.create(name="Owner", discord_id=50001)
        await Person.create(name="Other", discord_id=50002)
        info = await ProjectInfo.from_rect(RECT, person1.id, "theirs")
        with pytest.raises(ErrorMsg, match="not yours"):
            await remove_watch(50002, info.id, 100)

    async def test_not_watching(self):
        person, info = await _person_and_project()
        with pytest.raises(ErrorMsg, match="not being watched"):
            await remove_watch(77777, info.id, 100)

    async def test_success(self):
        person, info = await _person_and_project()
        await WatchMessage.create(project_id=info.id, channel_id=100, message_id=555)
        message_id = await remove_watch(77777, info.id, 100)
        assert message_id == 555
        assert await WatchMessage.get_by_project_channel(info.id, 100) is None


# get_watches_for_projects tests


async def test_get_watches_for_projects():
    person, info = await _person_and_project()
    await WatchMessage.create(project_id=info.id, channel_id=100, message_id=111)
    await WatchMessage.create(project_id=info.id, channel_id=200, message_id=222)
    watches = await get_watches_for_projects([info.id])
    assert len(watches) == 2
    assert {w.message_id for w in watches} == {111, 222}


async def test_get_watches_for_projects_empty():
    watches = await get_watches_for_projects([])
    assert watches == []


# delete_watches_for_project tests


async def test_delete_watches_for_project():
    person, info = await _person_and_project()
    await WatchMessage.create(project_id=info.id, channel_id=100, message_id=111)
    await WatchMessage.create(project_id=info.id, channel_id=200, message_id=222)
    deleted = await delete_watches_for_project(info.id)
    assert deleted == 2
    assert await WatchMessage.count_by_project(info.id) == 0


async def test_delete_watches_for_project_none():
    deleted = await delete_watches_for_project(9999)
    assert deleted == 0


# format_grief_message tests


def _grief_project(info: ProjectInfo, report: GriefReport) -> Project:
    """Build a minimal Project stub with info and grief_report set."""
    proj = object.__new__(Project)
    proj.info = info
    proj.grief_report = report
    return proj


class TestFormatGriefMessage:
    async def test_with_discord_id(self):
        person = await Person.create(name="Victim", discord_id=12345)
        info = await ProjectInfo.from_rect(RECT, person.id, "my art")
        await info.fetch_related_owner()
        painters = (Painter(user_id=99, user_name="Griefer", alliance_name="Bad", discord_id="", discord_name=""),)
        proj = _grief_project(info, GriefReport(regress_count=150, painters=painters))
        result = format_grief_message(proj)
        assert "<@12345>" in result
        assert "my art" in result
        assert "-150" in result
        assert "~Griefer" in result
        assert f"`{info.id:04}`" in result
        assert "wplace.live" in result

    async def test_without_discord_id(self):
        person = await Person.create(name="NoDC")
        info = await ProjectInfo.from_rect(RECT, person.id, "project")
        await info.fetch_related_owner()
        painters = (Painter(user_id=1, user_name="X", alliance_name="", discord_id="", discord_name=""),)
        proj = _grief_project(info, GriefReport(regress_count=200, painters=painters))
        result = format_grief_message(proj)
        assert "NoDC" in result
        assert "<@" not in result

    async def test_multiple_painters(self):
        person = await Person.create(name="V", discord_id=55555)
        info = await ProjectInfo.from_rect(RECT, person.id, "art")
        await info.fetch_related_owner()
        painters = (
            Painter(user_id=1, user_name="Alice", alliance_name="A", discord_id="", discord_name=""),
            Painter(user_id=2, user_name="Bob", alliance_name="B", discord_id="", discord_name=""),
        )
        proj = _grief_project(info, GriefReport(regress_count=300, painters=painters))
        result = format_grief_message(proj)
        lines = result.split("\n")
        assert len(lines) == 3  # header + 2 painters
        assert "~Alice" in lines[1]
        assert "~Bob" in lines[2]


# get_watch_image_paths tests


class TestGetWatchImagePaths:
    async def test_creating_project_returns_empty(self):
        person = await Person.create(name="Creator", discord_id=80001)
        info = ProjectInfo(owner_id=person.id, name="wip", state=ProjectState.CREATING, width=50, height=50)
        await info.save_as_new()
        await info.fetch_related_owner()
        assert get_watch_image_paths(info) == {}

    async def test_returns_existing_paths(self, setup_config):
        person, info = await _person_and_project()
        await info.fetch_related_owner()
        from pixel_hawk.models.config import get_config

        config = get_config()
        # Create goal file
        goal_dir = config.projects_dir / str(person.id)
        goal_dir.mkdir(parents=True, exist_ok=True)
        goal_path = goal_dir / info.filename
        goal_path.write_bytes(b"fake png")
        # Create snapshot file
        snap_dir = config.snapshots_dir / str(person.id)
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_path = snap_dir / info.filename
        snap_path.write_bytes(b"fake png")

        paths = get_watch_image_paths(info)
        assert len(paths) == 2
        assert paths[f"goal_{info.id:04}.png"] == goal_path
        assert paths[f"snapshot_{info.id:04}.png"] == snap_path

    async def test_only_goal_when_no_snapshot(self, setup_config):
        person, info = await _person_and_project()
        await info.fetch_related_owner()
        from pixel_hawk.models.config import get_config

        config = get_config()
        goal_dir = config.projects_dir / str(person.id)
        goal_dir.mkdir(parents=True, exist_ok=True)
        (goal_dir / info.filename).write_bytes(b"fake png")

        paths = get_watch_image_paths(info)
        assert len(paths) == 1
        assert f"goal_{info.id:04}.png" in paths

    async def test_empty_when_no_files(self, setup_config):
        person, info = await _person_and_project()
        await info.fetch_related_owner()
        paths = get_watch_image_paths(info)
        assert paths == {}
