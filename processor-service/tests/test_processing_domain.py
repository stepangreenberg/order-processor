import random

import pytest

from domain.processing import ProcessingState, ProcessingResult, ValidationError


def test_embargo_pineapple_or_teapot_fails():
    state = ProcessingState(order_id="ord-1", version=0)
    result = state.apply_order_created(
        items=["teapot", "other"], amount=10.0, random_func=lambda: 1.0
    )
    assert result.status == "failed"
    assert "embargo" in result.reason.lower()
    assert state.status == "failed"
    assert state.version == 1
    assert state.attempt_count == 1


def test_potato_fails_with_fat_reason():
    state = ProcessingState(order_id="ord-1", version=0)
    result = state.apply_order_created(
        items=["potato", "other"], amount=10.0, random_func=lambda: 1.0
    )
    assert result.status == "failed"
    assert "fatty" in result.reason.lower()
    assert state.status == "failed"
    assert state.version == 1
    assert state.attempt_count == 1


def test_random_success_with_seeded_random():
    state = ProcessingState(order_id="ord-1", version=0)
    # random_func returns 0.5 -> success (0.6 threshold)
    result = state.apply_order_created(
        items=["normal"], amount=10.0, random_func=lambda: 0.5
    )
    assert result.status == "success"
    assert state.status == "done"
    assert state.version == 1
    assert state.attempt_count == 1


def test_random_failure_with_seeded_random():
    state = ProcessingState(order_id="ord-1", version=0)
    # random_func returns 0.9 -> fail (0.6 threshold)
    result = state.apply_order_created(
        items=["normal"], amount=10.0, random_func=lambda: 0.9
    )
    assert result.status == "failed"
    assert "random failure" in result.reason.lower()
    assert state.status == "failed"
    assert state.version == 1
    assert state.attempt_count == 1


def test_older_version_is_ignored():
    state = ProcessingState(order_id="ord-1", version=2, status="done")
    result = state.apply_order_created(
        items=["normal"], amount=10.0, version=1, random_func=lambda: 0.5
    )
    assert result.status == "ignored"
    assert state.version == 2
    assert state.status == "done"
    assert state.attempt_count == 0
