"""
Tests for outline_editor — 节点 CRUD + 重排 + diff + validation.
"""
import pytest

from lib import outline_editor as oe
from lib import storage


def _seed_outline(book_root) -> str:
    """Create test_book with a 2-volume / 5-chapter outline."""
    storage.init_project("test_book", {"book_name": "test_book", "genre": "玄幻"})
    outline = {
        "meta": {"title": "测试书", "target_chapters": 20, "summary": "summary"},
        "volumes": [
            {"id": "vol_1", "title": "第一卷 序章", "summary": "vs1", "chapters": []},
            {"id": "vol_2", "title": "第二卷 高潮", "summary": "vs2", "chapters": []},
        ],
        "chapters": [
            {"id": "ch_001", "vol": "vol_1", "title": "开篇", "summary": "s1",
             "pov": "P", "key_events": [], "foreshadow": []},
            {"id": "ch_002", "vol": "vol_1", "title": "相遇", "summary": "s2",
             "pov": "P", "key_events": [], "foreshadow": []},
            {"id": "ch_003", "vol": "vol_1", "title": "冲突", "summary": "s3",
             "pov": "P", "key_events": [], "foreshadow": []},
            {"id": "ch_004", "vol": "vol_2", "title": "转折", "summary": "s4",
             "pov": "P", "key_events": [], "foreshadow": []},
            {"id": "ch_005", "vol": "vol_2", "title": "高潮", "summary": "s5",
             "pov": "P", "key_events": [], "foreshadow": []},
        ],
        "generated_at": "2026-07-01T00:00:00",
    }
    oe.save_outline("test_book", outline, auto_snapshot=False)
    return "test_book"


@pytest.fixture
def seeded(tmp_projects_root):
    return _seed_outline(tmp_projects_root)


# ── load / save ──────────────────────────────────────────────────────────────

class TestLoadSave:
    def test_load_returns_skeleton_from_init_project(self, tmp_projects_root):
        """storage.init_project seeds outline.json = {meta:{},volumes:[],chapters:[]};
        outline_editor does NOT fabricate defaults — it returns whatever storage gave."""
        storage.init_project("empty_book", {"book_name": "empty_book"})
        o = oe.load_outline_or_empty("empty_book")
        assert o["volumes"] == []
        assert o["chapters"] == []
        assert o["meta"] == {}  # not fabricated

    def test_save_writes_file_and_syncs_volumes(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        # volumes[].chapters should have been synced during seeding save
        v1 = next(v for v in o["volumes"] if v["id"] == "vol_1")
        assert len(v1["chapters"]) == 3  # 3 chapters in vol_1
        v2 = next(v for v in o["volumes"] if v["id"] == "vol_2")
        assert len(v2["chapters"]) == 2
        assert "第1章 开篇" in v1["chapters"]
        assert "第3章 冲突" in v1["chapters"]

    def test_round_trip_preserves_data(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        assert len(o["chapters"]) == 5
        assert o["chapters"][0]["title"] == "开篇"


# ── Volume CRUD ──────────────────────────────────────────────────────────────

class TestVolumeCRUD:
    def test_add_volume(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        vol = oe.add_volume(o, "第三卷 结局", summary="end")
        assert vol["id"] == "vol_3"
        assert len(o["volumes"]) == 3

    def test_add_volume_unique_id_collision(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        oe.add_volume(o, "skip-N")  # vol_3
        vol2 = oe.add_volume(o, "skip-N+1")  # vol_4 (because vol_3 taken)
        assert vol2["id"] == "vol_4"

    def test_remove_volume_reassigns_chapters(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        n = oe.remove_volume(o, "vol_1")
        assert n == 3  # 3 chapters in vol_1
        for ch in o["chapters"]:
            assert ch["vol"] == "vol_2"
        assert len(o["volumes"]) == 1

    def test_remove_volume_raises_if_missing(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        with pytest.raises(ValueError):
            oe.remove_volume(o, "vol_999")

    def test_update_volume(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        v = oe.update_volume(o, "vol_1", title="第一卷 新标题", summary="new")
        assert v["title"] == "第一卷 新标题"
        assert v["summary"] == "new"

    def test_update_volume_cannot_change_id(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        v = oe.update_volume(o, "vol_1", id="vol_X")
        assert v["id"] == "vol_1"  # unchanged


# ── Chapter node CRUD ───────────────────────────────────────────────────────

class TestNodeCRUD:
    def test_add_node_appends_within_vol(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        node = oe.add_node(o, "vol_1", position=99, title="vol_1 末尾新章")
        assert node["id"] == "ch_006"  # next in sequence
        assert node["vol"] == "vol_1"
        # Should be the LAST chapter in vol_1's slots (chapter-index 5)
        v1_indices = [ch for ch in o["chapters"] if ch["vol"] == "vol_1"]
        assert node is v1_indices[-1]
        assert len(v1_indices) == 4

    def test_add_node_prepends_at_zero(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        node = oe.add_node(o, "vol_1", position=0, title="vol_1 头部新章")
        v1 = [ch for ch in o["chapters"] if ch["vol"] == "vol_1"]
        assert node is v1[0]
        assert node["title"] == "vol_1 头部新章"

    def test_add_node_to_empty_vol_appends(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        oe.add_volume(o, "空卷")
        node = oe.add_node(o, "vol_3", position=5, title="solo")
        assert node["vol"] == "vol_3"

    def test_remove_node(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        removed = oe.remove_node(o, "ch_002")
        assert removed is not None
        assert removed["title"] == "相遇"
        ids = [ch["id"] for ch in o["chapters"]]
        assert "ch_002" not in ids

    def test_remove_node_missing_returns_none(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        assert oe.remove_node(o, "ch_999") is None

    def test_update_node_field_subset(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        n = oe.update_node(o, "ch_001", title="新开篇", pov="Q")
        assert n["title"] == "新开篇"
        assert n["pov"] == "Q"
        # Untouched fields preserved
        assert n["summary"] == "s1"

    def test_update_node_unknown_chapter_raises(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        with pytest.raises(ValueError):
            oe.update_node(o, "ch_999", title="x")

    def test_unique_id_when_many_added(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        ids = set()
        for i in range(5):
            n = oe.add_node(o, "vol_1", position=i, title=f"new_{i}")
            ids.add(n["id"])
        assert len(ids) == 5  # all unique


# ── Move / reorder ──────────────────────────────────────────────────────────

class TestMoveReorder:
    def test_move_within_same_vol(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        # ch_002 → vol_1 pos 0 (move to front)
        oe.move_node(o, "ch_002", "vol_1", 0)
        v1 = [ch for ch in o["chapters"] if ch["vol"] == "vol_1"]
        assert v1[0]["id"] == "ch_002"
        assert v1[1]["id"] == "ch_001"
        assert v1[2]["id"] == "ch_003"

    def test_move_across_vols(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        # ch_001 → vol_2 pos 0
        oe.move_node(o, "ch_001", "vol_2", 0)
        v1 = [ch for ch in o["chapters"] if ch["vol"] == "vol_1"]
        v2 = [ch for ch in o["chapters"] if ch["vol"] == "vol_2"]
        # vol_1 originally had 3 (ch_001/ch_002/ch_003); moving ch_001 out leaves 2.
        assert len(v1) == 2
        assert all(ch["id"] in {"ch_002", "ch_003"} for ch in v1)
        # vol_2 originally had ch_004/ch_005; after moving ch_001 in at pos 0:
        assert v2[0]["id"] == "ch_001"

    def test_move_to_end_position(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        # ch_001 → vol_2 pos 999 (append)
        oe.move_node(o, "ch_001", "vol_2", 999)
        v2 = [ch for ch in o["chapters"] if ch["vol"] == "vol_2"]
        assert v2[-1]["id"] == "ch_001"
        assert len(v2) == 3

    def test_reorder_batch(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        # Move ch_005 to vol_1 pos 0; ch_001 to vol_2 pos 0
        result = oe.reorder_nodes(o, [
            {"ch_id": "ch_005", "new_vol": "vol_1", "new_position": 0},
            {"ch_id": "ch_001", "new_vol": "vol_2", "new_position": 0},
        ])
        # ch_001 should now be in vol_2 (we moved it second)
        v2 = [ch for ch in o["chapters"] if ch["vol"] == "vol_2"]
        assert v2[0]["id"] == "ch_001"

    def test_reorder_invalid_move_raises(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        with pytest.raises(ValueError):
            oe.reorder_nodes(o, [{"ch_id": "ch_001"}])  # missing keys


# ── Sync ────────────────────────────────────────────────────────────────────

class TestSync:
    def test_sync_after_move(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        oe.move_node(o, "ch_001", "vol_2", 0)  # 移出 vol_1 → vol_2
        oe.sync_volumes_chapters(o)
        v1 = next(v for v in o["volumes"] if v["id"] == "vol_1")
        v2 = next(v for v in o["volumes"] if v["id"] == "vol_2")
        assert len(v1["chapters"]) == 2
        assert len(v2["chapters"]) == 3

    def test_sync_renumbers_labels_1_to_n(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        oe.sync_volumes_chapters(o)
        v1 = next(v for v in o["volumes"] if v["id"] == "vol_1")
        labels = v1["chapters"]
        assert labels[0].startswith("第1章 ")
        assert labels[1].startswith("第2章 ")
        assert labels[2].startswith("第3章 ")

    def test_sync_is_idempotent(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        a = oe.sync_volumes_chapters(o)["volumes"]
        b = oe.sync_volumes_chapters(json_copy := {**o, "volumes": [dict(v) for v in o["volumes"]]})["volumes"]
        assert a == b


# ── Validation ──────────────────────────────────────────────────────────────

class TestValidation:
    def test_valid_outline(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        assert oe.validate_outline(o) == []

    def test_duplicate_chapter_id(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        o["chapters"][0]["id"] = "ch_002"  # collide
        errors = oe.validate_outline(o)
        assert any("duplicate chapter id" in e for e in errors)

    def test_chapter_references_unknown_vol(self, seeded):
        o = oe.load_outline_or_empty("test_book")
        o["chapters"][0]["vol"] = "vol_999"
        errors = oe.validate_outline(o)
        assert any("unknown" in e for e in errors)


# ── Diff ─────────────────────────────────────────────────────────────────────

class TestDiff:
    def test_diff_no_change(self, seeded):
        a = oe.load_outline_or_empty("test_book")
        b = oe.load_outline_or_empty("test_book")
        d = oe.diff_outlines(a, b)
        assert d["volumes_added"] == []
        assert d["chapters_added"] == []
        assert d["chapters_moved"] == []

    def test_diff_added_chapter(self, seeded):
        a = oe.load_outline_or_empty("test_book")
        b = oe.load_outline_or_empty("test_book")
        oe.add_node(b, "vol_1", position=99, title="新章")
        d = oe.diff_outlines(a, b)
        assert "ch_006" in d["chapters_added"]

    def test_diff_removed_chapter(self, seeded):
        a = oe.load_outline_or_empty("test_book")
        b = oe.load_outline_or_empty("test_book")
        oe.remove_node(b, "ch_005")
        d = oe.diff_outlines(a, b)
        assert "ch_005" in d["chapters_removed"]

    def test_diff_moved_chapter(self, seeded):
        a = oe.load_outline_or_empty("test_book")
        b = oe.load_outline_or_empty("test_book")
        # ch_001 vol_1 pos 0 → vol_2 pos 0
        oe.move_node(b, "ch_001", "vol_2", 0)
        d = oe.diff_outlines(a, b)
        moves = d["chapters_moved"]
        assert any(m["ch_id"] == "ch_001" and m["from_vol"] == "vol_1" and m["to_vol"] == "vol_2"
                   for m in moves)

    def test_diff_edited_chapter(self, seeded):
        a = oe.load_outline_or_empty("test_book")
        b = oe.load_outline_or_empty("test_book")
        oe.update_node(b, "ch_001", title="新标题")
        d = oe.diff_outlines(a, b)
        assert any(e["ch_id"] == "ch_001" and "title" in e["fields_changed"]
                   for e in d["chapters_edited"])

    def test_diff_volume_added(self, seeded):
        a = oe.load_outline_or_empty("test_book")
        b = oe.load_outline_or_empty("test_book")
        oe.add_volume(b, "vol_3 终章")
        d = oe.diff_outlines(a, b)
        assert "vol_3" in d["volumes_added"]
