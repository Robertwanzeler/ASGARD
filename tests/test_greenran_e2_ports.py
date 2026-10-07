import pytest

from src.greenran_e2_ports import E2PortPlanError, build_e2_port_plan


def test_rejects_the_previous_overlapping_plan_with_cell_1_and_2_labels():
    with pytest.raises(E2PortPlanError, match=r"40201 .*e2_term/e2_local_cell_1"):
        build_e2_port_plan(40201, 40202, 40200)


def test_accepts_the_campaign_plan_and_emits_all_local_binds():
    plan = build_e2_port_plan(40301, 40302, 40320)

    assert plan["valid"] is True
    assert plan["e2_local_bind_ports"] == {
        "1": 40321,
        "2": 40322,
        "3": 40323,
        "4": 40324,
    }


@pytest.mark.parametrize(
    "term,xapp,local",
    [(0, 40302, 40320), (40301, 65536, 40320), (40301, 40302, 65533)],
)
def test_rejects_out_of_range_ports(term, xapp, local):
    with pytest.raises(E2PortPlanError):
        build_e2_port_plan(term, xapp, local)
