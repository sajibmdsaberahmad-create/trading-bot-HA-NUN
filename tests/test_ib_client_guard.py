"""IB client ID guard."""
from core.ib_client_guard import acquire_lock, check_client_id_available, release_lock


def test_acquire_and_release():
    cid = 19999  # test-only id
    release_lock(cid)
    ok, _ = acquire_lock(cid, pid=999999, command="test")
    assert ok
    release_lock(cid)
    ok2, msg = check_client_id_available(cid)
    assert ok2, msg
