from personal_context_node.config import AppConfig
from personal_context_node.storage.sqlite import connect
from personal_context_node.tasks import enqueue_task
from personal_context_node.web.app import create_app


def test_startup_recovers_expired_identification_without_starting_or_stealing_work(tmp_path):
    config = AppConfig(data_dir=tmp_path / "data", obsidian_vault=tmp_path / "vault")
    expired = enqueue_task(config=config, task_type="identify_speakers", target_type="session", target_id="old")
    active = enqueue_task(config=config, task_type="identify_speakers", target_type="session", target_id="active")
    with connect(config.database_path) as conn:
        conn.execute("""update tasks set status='running',claimed_by_run_id='old_worker',
            claimed_at='2000-01-01T00:00:00+00:00',lease_expires_at='2000-01-01T00:30:00+00:00'
            where task_id=?""", (expired.task_id,))
        conn.execute("""update tasks set status='running',claimed_by_run_id='live_worker',
            lease_expires_at='2999-01-01T00:00:00+00:00' where task_id=?""", (active.task_id,))
    app = create_app(config=config)
    assert not app.state.worker.is_running()
    with connect(config.database_path) as conn:
        rows = {r["task_id"]: dict(r) for r in conn.execute("select * from tasks")}
    assert rows[expired.task_id]["status"] == "pending"
    assert rows[expired.task_id]["claimed_by_run_id"] is None
    assert rows[active.task_id]["status"] == "running"
    assert rows[active.task_id]["claimed_by_run_id"] == "live_worker"
