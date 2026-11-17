"""The @transactional decorator — REQUIRED propagation, without a database.

A fake session that counts commits and rollbacks is enough to pin the
semantics, and it makes the nesting rule readable in a way a database test
never would.
"""

import pytest

from app.services.base import BaseService, transactional

pytestmark = pytest.mark.unit


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class Boom(Exception):
    pass


class Sample(BaseService):
    @transactional
    def outer_calling_inner(self, fail: bool = False):
        self.inner()
        self.inner()
        if fail:
            raise Boom()
        return "done"

    @transactional
    def inner(self):
        return "inner"

    @transactional
    def solo(self, fail: bool = False):
        if fail:
            raise Boom()
        return "solo"

    @transactional
    def recovers_internally(self):
        try:
            self.solo(fail=True)
        except Boom:
            pass
        return self.depth_now()

    def depth_now(self) -> int:
        return getattr(self.session, "_saiv_tx_depth", 0)


@pytest.fixture
def service():
    return Sample(FakeSession())


class TestSingleCall:
    def test_success_commits_exactly_once(self, service):
        assert service.solo() == "solo"
        assert (service.session.commits, service.session.rollbacks) == (1, 0)

    def test_failure_rolls_back_and_reraises(self, service):
        with pytest.raises(Boom):
            service.solo(fail=True)
        assert (service.session.commits, service.session.rollbacks) == (0, 1)


class TestNesting:
    def test_only_the_outermost_call_commits(self, service):
        """Two inner calls plus the outer one produce ONE commit. If each
        committed, a partially-completed operation would be durable."""
        service.outer_calling_inner()
        assert service.session.commits == 1

    def test_inner_failure_rolls_back_the_whole_unit_once(self, service):
        with pytest.raises(Boom):
            service.outer_calling_inner(fail=True)
        assert (service.session.commits, service.session.rollbacks) == (0, 1)

    def test_inner_call_alone_still_commits(self, service):
        """The same method is a unit of work on its own, and a participant when
        called from another service."""
        service.inner()
        assert service.session.commits == 1


class TestDepthBookkeeping:
    def test_depth_returns_to_zero_after_success(self, service):
        service.outer_calling_inner()
        assert service.depth_now() == 0

    def test_depth_returns_to_zero_after_failure(self, service):
        with pytest.raises(Boom):
            service.solo(fail=True)
        assert service.depth_now() == 0

    def test_depth_is_restored_when_an_exception_is_caught_internally(self, service):
        """Without the finally block, a caught-and-recovered error would leave
        the counter stuck high and the next outermost call would never commit."""
        assert service.recovers_internally() == 1
        assert service.depth_now() == 0

    def test_in_transaction_property_tracks_depth(self, service):
        assert not service.in_transaction
        service.solo()
        assert not service.in_transaction
