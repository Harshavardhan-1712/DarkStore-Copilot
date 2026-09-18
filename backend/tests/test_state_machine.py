import pytest

import state_machine as sm
from errors import Conflict


def test_happy_path_order_transitions():
    for a, b in ((sm.CREATED, sm.PICKING), (sm.PICKING, sm.BAG_VERIFICATION),
                 (sm.BAG_VERIFICATION, sm.READY_FOR_PICKUP), (sm.READY_FOR_PICKUP, sm.DISPATCHED)):
        assert sm.assert_order_transition(a, b) == b


def test_illegal_shortcuts_are_refused():
    with pytest.raises(Conflict):
        sm.assert_order_transition(sm.CREATED, sm.READY_FOR_PICKUP)
    with pytest.raises(Conflict):
        sm.assert_order_transition(sm.DISPATCHED, sm.PICKING)


def test_failed_bag_check_can_repeat_and_reopen_picking():
    assert sm.assert_order_transition(sm.BAG_VERIFICATION, sm.BAG_VERIFICATION)
    assert sm.assert_order_transition(sm.BAG_VERIFICATION, sm.PICKING)


def test_oos_to_substituted_path():
    assert sm.assert_item_transition(sm.AVAILABLE, sm.OOS)
    assert sm.assert_item_transition(sm.OOS, sm.SUBSTITUTED)
    with pytest.raises(Conflict):
        sm.assert_item_transition(sm.PICKED, sm.OOS)


def test_completion_check():
    assert sm.order_is_complete([{"state": sm.PICKED}, {"state": sm.SUBSTITUTED}])
    assert not sm.order_is_complete([{"state": sm.PICKED}, {"state": sm.OOS}])
    assert not sm.order_is_complete([])
