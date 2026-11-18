"""客流变动说明：基线选择、净增减、事件号证据与发布原子性。"""

from app import db, service

from helpers import day, make_csv, pairs, state, tap, upload


def changes(db_path, date):
    return day(db_path, date)["changes"]


def pairs_by_route(ch):
    return {(p["from_station"], p["to_station"]): (p["before_trips"], p["after_trips"], p["delta"])
            for p in ch["pairs"]}


def journey_keys(ch, kind):
    return {(j["entry_event_no"], j["exit_event_no"], j["from_station"], j["to_station"])
            for j in ch["journeys"] if j["kind"] == kind}


def seed(db_path):
    upload(db_path, [
        tap("e1", "c1", "2024-05-01T08:00:00Z", "A", "in"),
        tap("e2", "c1", "2024-05-01T08:20:00Z", "B", "out"),
        tap("e3", "c2", "2024-05-01T09:00:00Z", "A", "in"),
        tap("e4", "c2", "2024-05-01T09:30:00Z", "C", "out"),
    ])


def test_first_publication_has_no_baseline_but_has_evidence(db_path):
    seed(db_path)
    ch = changes(db_path, "2024-05-01")
    assert ch is not None
    assert ch["has_baseline"] is False
    assert journey_keys(ch, "added") == {("e1", "e2", "A", "B"), ("e3", "e4", "A", "C")}
    assert ch["removed_count"] == 0


def test_correction_reports_net_delta_and_event_numbers(db_path):
    seed(db_path)
    baseline_batch = state(db_path, "2024-05-01")["published_batch_id"]

    upload(db_path, [tap("e4", "c2", "2024-05-01T09:30:00Z", "D", "out", revision=1)])
    ch = changes(db_path, "2024-05-01")
    assert ch["has_baseline"] is True
    assert ch["baseline_batch_id"] == baseline_batch
    assert pairs_by_route(ch) == {("A", "B"): (1, 1, 0), ("A", "C"): (1, 0, -1), ("A", "D"): (0, 1, 1)}
    # 证据用事件号说明哪条行程被换掉。
    assert journey_keys(ch, "removed") == {("e3", "e4", "A", "C")}
    assert journey_keys(ch, "added") == {("e3", "e4", "A", "D")}
    assert ch["added_count"] == ch["removed_count"] == 1


def test_zero_net_delta_still_shows_swapped_journeys(db_path):
    upload(db_path, [
        tap("a1", "c1", "2024-05-01T08:00:00Z", "A", "in"),
        tap("a2", "c1", "2024-05-01T08:20:00Z", "B", "out"),
    ])
    # 撤掉 A→B，同时新增同样数量的 A→B（不同卡、不同事件号）：净增减为 0。
    upload(db_path, [
        tap("b1", "c2", "2024-05-01T10:00:00Z", "A", "in"),
        tap("b2", "c2", "2024-05-01T10:20:00Z", "B", "out"),
        tap("a2", "c1", "2024-05-01T08:20:00Z", "Z", "out", revision=1),
    ])
    ch = changes(db_path, "2024-05-01")
    assert set(pairs_by_route(ch)) == {("A", "B"), ("A", "Z")}
    assert pairs_by_route(ch)[("A", "B")] == (1, 1, 0)
    assert pairs_by_route(ch)[("A", "Z")] == (0, 1, 1)
    # 净增减为 0 的区间仍列出被换掉的行程。
    assert journey_keys(ch, "removed") == {("a1", "a2", "A", "B")}
    assert journey_keys(ch, "added") == {("b1", "b2", "A", "B"), ("a1", "a2", "A", "Z")}


def test_zero_net_delta_single_route(db_path):
    upload(db_path, [
        tap("a1", "c1", "2024-05-01T08:00:00Z", "A", "in"),
        tap("a2", "c1", "2024-05-01T08:20:00Z", "B", "out"),
    ])
    upload(db_path, [tap("a2", "c1", "2024-05-01T08:20:00Z", "C", "out", revision=1),
                     tap("b1", "c2", "2024-05-01T10:00:00Z", "A", "in"),
                     tap("b2", "c2", "2024-05-01T10:20:00Z", "B", "out")])
    ch = changes(db_path, "2024-05-01")
    # A→B 人数前后都是 1，但成员完全换掉；A→C 新增 1。
    assert pairs_by_route(ch)[("A", "B")] == (1, 1, 0)
    assert journey_keys(ch, "removed") == {("a1", "a2", "A", "B")}
    assert journey_keys(ch, "added") == {("b1", "b2", "A", "B"), ("a1", "a2", "A", "C")}


def test_cross_midnight_journey_belongs_to_entry_day(db_path):
    upload(db_path, [
        tap("in1", "c1", "2024-05-01T23:50:00Z", "A", "in"),
        tap("out1", "c1", "2024-05-02T00:20:00Z", "B", "out"),
    ])
    upload(db_path, [tap("out1", "c1", "2024-05-02T00:20:00Z", "C", "out", revision=1)])
    d1 = changes(db_path, "2024-05-01")
    assert journey_keys(d1, "removed") == {("in1", "out1", "A", "B")}
    assert journey_keys(d1, "added") == {("in1", "out1", "A", "C")}
    # 出站日（次日）没有自己的行程，其说明为空但仍带基线。
    d2 = changes(db_path, "2024-05-02")
    assert d2["journeys"] == []
    assert d2["pairs"] == []
    # 两端日期同一事务发布，说明也一起成为现行。
    assert state(db_path, "2024-05-01")["computed_at"] == state(db_path, "2024-05-02")["computed_at"]


def test_consecutive_uploads_compare_against_last_published(db_path):
    seed(db_path)
    first_publish = state(db_path, "2024-05-01")["computed_at"]

    # 两次受理都还没重算，然后一次性重算：应以最后一次“已发布”为基线。
    service.accept_upload(db_path, "f1.csv", make_csv([
        tap("e4", "c2", "2024-05-01T09:30:00Z", "D", "out", revision=1),
    ]))
    service.accept_upload(db_path, "f2.csv", make_csv([
        tap("e4", "c2", "2024-05-01T09:30:00Z", "E", "out", revision=2),
    ]))
    # 期间不得展示任何旧说明。
    assert day(db_path, "2024-05-01")["changes"] is None
    service.run_pending_jobs(db_path)

    ch = changes(db_path, "2024-05-01")
    assert ch["has_baseline"] is True
    assert ch["baseline_computed_at"] == first_publish
    assert pairs_by_route(ch) == {("A", "B"): (1, 1, 0), ("A", "C"): (1, 0, -1), ("A", "E"): (0, 1, 1)}
    assert journey_keys(ch, "removed") == {("e3", "e4", "A", "C")}
    assert journey_keys(ch, "added") == {("e3", "e4", "A", "E")}


def test_acceptance_invalidates_explanation_immediately(db_path):
    seed(db_path)
    assert changes(db_path, "2024-05-01") is not None
    service.accept_upload(db_path, "c.csv", make_csv([
        tap("e4", "c2", "2024-05-01T09:30:00Z", "D", "out", revision=1),
    ]))
    detail = day(db_path, "2024-05-01")
    assert detail["status"] == "stale"
    assert detail["changes"] is None
    assert detail["station_pairs"] == []


def test_failed_recompute_never_serves_old_explanation(db_path, monkeypatch):
    seed(db_path)
    original = service._publish
    monkeypatch.setattr(service, "_publish", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    service.accept_upload(db_path, "c.csv", make_csv([
        tap("e4", "c2", "2024-05-01T09:30:00Z", "D", "out", revision=1),
    ]))
    service.run_pending_jobs(db_path)
    detail = day(db_path, "2024-05-01")
    assert detail["status"] == "stale"
    assert detail["changes"] is None
    assert detail["station_pairs"] == []
    # 持久层也没有把旧说明留在可读位置。
    conn = db.connect(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM day_changes").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM change_journeys").fetchone()[0] == 0
        # 基线仍然保留，修复后用于比较。
        assert conn.execute("SELECT COUNT(*) FROM published_journeys").fetchone()[0] == 2
    finally:
        conn.close()

    monkeypatch.setattr(service, "_publish", original)
    service.request_recompute(db_path, ["2024-05-01"])
    service.run_pending_jobs(db_path)
    ch = changes(db_path, "2024-05-01")
    assert ch["has_baseline"] is True
    assert journey_keys(ch, "removed") == {("e3", "e4", "A", "C")}


def test_restart_recovery_hides_old_explanation_until_republished(db_path):
    seed(db_path)
    service.accept_upload(db_path, "c.csv", make_csv([
        tap("e4", "c2", "2024-05-01T09:30:00Z", "D", "out", revision=1),
    ]))
    conn = db.connect(db_path)
    try:
        # 伪造“已发布但 epoch 落后”的旧说明，模拟崩溃前的残留。
        conn.execute(
            "INSERT OR REPLACE INTO day_changes (service_date, publish_seq, data_epoch, has_baseline, "
            " baseline_batch_id, baseline_computed_at, added_count, removed_count, computed_at) "
            "VALUES ('2024-05-01', 99, 0, 1, 1, NULL, 0, 0, '2024-05-01T00:00:00Z')"
        )
        conn.execute("UPDATE day_state SET status='current', data_epoch=0 WHERE service_date='2024-05-01'")
    finally:
        conn.close()
    assert day(db_path, "2024-05-01")["changes"] is None  # 证据 epoch 不匹配，拒绝展示

    service.recover(db_path)
    assert day(db_path, "2024-05-01")["status"] == "stale"
    assert day(db_path, "2024-05-01")["changes"] is None
    service.run_pending_jobs(db_path)
    ch = changes(db_path, "2024-05-01")
    assert ch is not None and ch["has_baseline"] is True


def test_interleaved_publish_cannot_publish_old_comparison(db_path):
    seed(db_path)
    old_batch = state(db_path, "2024-05-01")["published_batch_id"]

    first = service.accept_upload(db_path, "first.csv", make_csv([
        tap("e4", "c2", "2024-05-01T09:30:00Z", "D", "out", revision=1),
    ]))
    second_batch = {"id": None}

    def racing(stage, conn, info):
        # 第一个任务算完之后、发布之前，第二个上传受理并被另一个 worker 发布。
        if stage == "after_compute" and info["job_id"] == first.job_id and second_batch["id"] is None:
            second = service.accept_upload(db_path, "second.csv", make_csv([
                tap("e4", "c2", "2024-05-01T09:30:00Z", "E", "out", revision=2),
            ]))
            second_batch["id"] = second.batch_id
            service.run_pending_jobs(db_path, limit=10)

    service.run_pending_jobs(db_path, hook=racing)
    service.run_pending_jobs(db_path)

    detail = day(db_path, "2024-05-01")
    assert detail["status"] == "current"
    assert pairs(detail) == {("A", "B"): 1, ("A", "E"): 1}
    ch = detail["changes"]
    assert ch is not None
    # 说明必须描述“最后被排班组看到的现行发布（批次 1） → 本次”，
    # 交错任务的中间结果（A→D）不得成为基线或被报告。
    assert ch["baseline_batch_id"] == old_batch
    assert ch["compared_at"] == detail["computed_at"]
    assert journey_keys(ch, "removed") == {("e3", "e4", "A", "C")}
    assert journey_keys(ch, "added") == {("e3", "e4", "A", "E")}
    assert second_batch["id"] != old_batch


def test_publish_sequence_fence_rejects_stale_comparison_without_new_data(db_path):
    """同一 epoch 下被别的任务抢先发布时，旧的比较基线也必须作废。"""
    seed(db_path)
    service.accept_upload(db_path, "c.csv", make_csv([
        tap("e4", "c2", "2024-05-01T09:30:00Z", "D", "out", revision=1),
    ]))

    # 手动再造一个覆盖同一天的任务，并让第一个任务处于 running，避免合并。
    conn = db.connect(db_path)
    try:
        with db.transaction(conn):
            first_job = conn.execute(
                "SELECT id FROM recompute_jobs WHERE status='pending' ORDER BY id LIMIT 1"
            ).fetchone()["id"]
            conn.execute(
                "UPDATE recompute_jobs SET status='running', started_at='2024-05-01T00:00:00Z' "
                "WHERE id = ?", (first_job,)
            )
            second_job = service._enqueue_job(conn, ["2024-05-01"], None)
            conn.execute(
                "UPDATE recompute_jobs SET status='pending' WHERE id = ?", (first_job,)
            )
    finally:
        conn.close()

    def racing(stage, conn, info):
        if stage == "after_compute" and info["job_id"] == first_job:
            # 另一个任务用同一个 epoch 发布（没有新上传、epoch 不变）。
            service.run_pending_jobs(db_path, limit=10)

    service.run_pending_jobs(db_path, hook=racing)
    service.run_pending_jobs(db_path)

    conn = db.connect(db_path)
    try:
        done = conn.execute(
            "SELECT id, status, attempts FROM recompute_jobs WHERE id IN (?, ?) ORDER BY id",
            (first_job, second_job),
        ).fetchall()
    finally:
        conn.close()
    # 第一个任务因发布序号变化被拒绝并重试，最终状态仍一致。
    assert {row["status"] for row in done} == {"done"}
    assert dict(done[0])["attempts"] >= 2
    detail = day(db_path, "2024-05-01")
    assert detail["status"] == "current"
    ch = detail["changes"]
    assert ch is not None
    # 交错发布不得把“上一次现行 → 更正后”的说明改写成第二次发布的零变更；
    # 排班组从未看到过 A→D，最终说明必须仍是那次更正。
    assert journey_keys(ch, "removed") == {("e3", "e4", "A", "C")}
    assert journey_keys(ch, "added") == {("e3", "e4", "A", "D")}
    assert ch["has_baseline"] is True
    assert ch["baseline_computed_at"] is not None

