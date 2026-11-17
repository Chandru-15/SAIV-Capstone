"""Page and limit clamping."""

import pytest

from app.repositories.base import DEFAULT_LIMIT, MAX_LIMIT, Page, clamp_limit

pytestmark = pytest.mark.unit


class TestClampLimit:
    def test_none_uses_the_default(self):
        assert clamp_limit(None) == DEFAULT_LIMIT == 50

    def test_over_maximum_is_clamped(self):
        """The API spec caps page size at 100. Without clamping, ?limit=1000000
        is a trivial way to exhaust memory."""
        assert clamp_limit(10_000) == MAX_LIMIT == 100

    @pytest.mark.parametrize("value,expected", [(1, 1), (50, 50), (100, 100)])
    def test_values_in_range_pass_through(self, value, expected):
        assert clamp_limit(value) == expected

    @pytest.mark.parametrize("value", [0, -1, -999])
    def test_non_positive_becomes_one(self, value):
        assert clamp_limit(value) == 1


class TestPage:
    def test_has_next_when_more_remain(self):
        assert Page(items=list(range(50)), total=150, limit=50, offset=0).has_next

    def test_no_next_on_the_last_page(self):
        assert not Page(items=list(range(50)), total=100, limit=50, offset=50).has_next

    def test_no_next_when_everything_fits(self):
        assert not Page(items=list(range(3)), total=3, limit=50, offset=0).has_next

    def test_has_previous_only_when_offset_advanced(self):
        assert not Page(items=[], total=0, limit=50, offset=0).has_previous
        assert Page(items=[], total=100, limit=50, offset=50).has_previous

    def test_empty_page_is_coherent(self):
        page = Page(items=[], total=0, limit=50, offset=0)
        assert not page.has_next and not page.has_previous
