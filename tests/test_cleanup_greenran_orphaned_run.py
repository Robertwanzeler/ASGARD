from scripts.cleanup_greenran_orphaned_run import _is_greenran_cgroup


def test_cleanup_accepts_static_and_systemd_user_greenran_cgroups():
    assert _is_greenran_cgroup("0::/user.slice/user@1000.service/greenran/collectors")
    assert _is_greenran_cgroup(
        "0::/user.slice/user@1000.service/app.slice/greenran-arm-r2-collectors-1.scope"
    )
    assert not _is_greenran_cgroup("0::/user.slice/user@1000.service/app.slice/unrelated.scope")
