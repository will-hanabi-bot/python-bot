"""Coverage of Identity and IdentitySet semantics.

Focus areas:
- Round-trip ord <-> Identity
- Bitwise operators preserve the IdentitySet subclass (regression-critical)
- Set operations (union/intersect/difference) accept Identity, iterable, IdentitySet
"""

from __future__ import annotations

import pytest

from hanabi_bot.basics.identity import Identity, IdentitySet

# --- Identity ---


def test_identity_to_ord_round_trip() -> None:
    for suit_index in range(6):
        for rank in range(1, 6):
            id_ = Identity(suit_index, rank)
            assert Identity.from_ord(id_.to_ord()) == id_


def test_identity_from_ord_out_of_range() -> None:
    with pytest.raises(ValueError):
        Identity.from_ord(-1)
    with pytest.raises(ValueError):
        Identity.from_ord(30)


def test_identity_prev_next() -> None:
    assert Identity(0, 1).prev is None
    assert Identity(0, 2).prev == Identity(0, 1)
    assert Identity(0, 5).next is None
    assert Identity(0, 4).next == Identity(0, 5)


def test_identity_played_before() -> None:
    assert Identity(0, 1).played_before(Identity(0, 2)) is True
    assert Identity(0, 2).played_before(Identity(0, 1)) is False
    assert Identity(0, 1).played_before(Identity(1, 2)) is False  # different suit
    assert Identity(0, 2).played_before(Identity(0, 2)) is False  # equal


def test_identity_is_frozen() -> None:
    id_ = Identity(0, 1)
    with pytest.raises((AttributeError, TypeError)):
        id_.rank = 2  # type: ignore[misc]


# --- IdentitySet: construction ---


def test_empty_set() -> None:
    s = IdentitySet.empty()
    assert s.length == 0
    assert s.is_empty
    assert not s.non_empty
    assert list(s) == []


def test_single() -> None:
    id_ = Identity(2, 3)
    s = IdentitySet.single(id_)
    assert s.length == 1
    assert id_ in s
    assert s.head == id_
    assert s.is_exactly(id_)
    assert not s.is_exactly(Identity(2, 4))


def test_from_iter_iterates_sorted_by_ord() -> None:
    ids = [Identity(2, 1), Identity(0, 3), Identity(1, 5), Identity(0, 1)]
    s = IdentitySet.from_iter(ids)
    assert s.length == 4
    # iteration ascends by ord
    seen = list(s)
    ords = [i.to_ord() for i in seen]
    assert ords == sorted(ords)


def test_from_iter_dedupes() -> None:
    s = IdentitySet.from_iter([Identity(0, 1), Identity(0, 1), Identity(0, 2)])
    assert s.length == 2


def test_create_with_predicate() -> None:
    # All rank-5s across the standard 5-suit range (ordinals 4, 9, 14, 19, 24)
    s = IdentitySet.create(lambda i: i.rank == 5, max_ids=25)
    assert s.length == 5
    assert all(i.rank == 5 for i in s)


# --- IdentitySet: bitwise operators preserve subclass ---


@pytest.fixture
def a() -> IdentitySet:
    return IdentitySet.from_iter([Identity(0, 1), Identity(0, 2), Identity(1, 1)])


@pytest.fixture
def b() -> IdentitySet:
    return IdentitySet.from_iter([Identity(0, 2), Identity(1, 1), Identity(2, 3)])


def test_or_preserves_subclass(a: IdentitySet, b: IdentitySet) -> None:
    result = a | b
    assert type(result) is IdentitySet
    assert result.length == 4


def test_and_preserves_subclass(a: IdentitySet, b: IdentitySet) -> None:
    result = a & b
    assert type(result) is IdentitySet
    assert result.length == 2
    assert Identity(0, 2) in result
    assert Identity(1, 1) in result


def test_xor_preserves_subclass(a: IdentitySet, b: IdentitySet) -> None:
    result = a ^ b
    assert type(result) is IdentitySet
    assert result.length == 2  # symmetric diff: (0,1) and (2,3)


def test_sub_preserves_subclass(a: IdentitySet, b: IdentitySet) -> None:
    result = a - b
    assert type(result) is IdentitySet
    assert result.length == 1
    assert Identity(0, 1) in result


def test_difference_via_invert(a: IdentitySet, b: IdentitySet) -> None:
    # a & ~b should equal a - b. This is the form Scala uses internally;
    # ~b is a plain int (negative), but & with an IdentitySet returns IdentitySet.
    result = a & ~b
    assert type(result) is IdentitySet
    assert result == a - b


def test_right_handed_ops_preserve_subclass(a: IdentitySet, b: IdentitySet) -> None:
    # An int-on-the-left binop still yields an IdentitySet via __rand__/__ror__/__rxor__
    assert type(int(b) | a) is IdentitySet
    assert type(int(b) & a) is IdentitySet
    assert type(int(b) ^ a) is IdentitySet


# --- IdentitySet: high-level set ops ---


def test_union_with_identity(a: IdentitySet) -> None:
    result = a.union(Identity(3, 4))
    assert type(result) is IdentitySet
    assert result.length == 4
    assert Identity(3, 4) in result


def test_union_with_iterable(a: IdentitySet) -> None:
    result = a.union([Identity(3, 4), Identity(3, 5)])
    assert result.length == 5


def test_intersect_with_iterable(a: IdentitySet) -> None:
    result = a.intersect([Identity(0, 1), Identity(5, 5)])
    assert result.length == 1
    assert Identity(0, 1) in result


def test_difference_with_identity(a: IdentitySet) -> None:
    result = a.difference(Identity(0, 1))
    assert result.length == 2
    assert Identity(0, 1) not in result


def test_add_remove(a: IdentitySet) -> None:
    plus = a.add(Identity(5, 5))
    assert plus.length == 4
    assert Identity(5, 5) in plus

    minus = a.remove(Identity(0, 1))
    assert minus.length == 2
    assert Identity(0, 1) not in minus


# --- IdentitySet: predicates ---


def test_filter(a: IdentitySet) -> None:
    result = a.filter(lambda i: i.rank == 1)
    assert result.length == 2
    assert all(i.rank == 1 for i in result)


def test_forall_exists(a: IdentitySet) -> None:
    assert a.forall(lambda i: i.suit_index in (0, 1))
    assert not a.forall(lambda i: i.suit_index == 0)
    assert a.exists(lambda i: i.rank == 2)
    assert not a.exists(lambda i: i.rank == 5)


def test_find(a: IdentitySet) -> None:
    assert a.find(lambda i: i.rank == 2) == Identity(0, 2)
    assert a.find(lambda i: i.rank == 5) is None


def test_count(a: IdentitySet) -> None:
    assert a.count(lambda i: i.rank == 1) == 2
    assert a.count(lambda i: i.rank == 5) == 0


def test_when_empty() -> None:
    empty = IdentitySet.empty()
    fallback = IdentitySet.single(Identity(0, 1))
    assert empty.when_empty(fallback) == fallback
    assert fallback.when_empty(empty) == fallback


# --- IdentitySet: head edge cases ---


def test_head_of_empty_raises() -> None:
    with pytest.raises(IndexError):
        _ = IdentitySet.empty().head


def test_head_returns_lowest_ord() -> None:
    s = IdentitySet.from_iter([Identity(3, 5), Identity(1, 2), Identity(2, 1)])
    # ords: 19, 6, 11. Lowest is 6 = Identity(1, 2)
    assert s.head == Identity(1, 2)


# --- IdentitySet: containment with non-Identity ---


def test_contains_non_identity_returns_false() -> None:
    s = IdentitySet.single(Identity(0, 1))
    assert "not an identity" not in s
    assert 5 not in s


def test_repr(a: IdentitySet) -> None:
    assert "IdentitySet" in repr(a)
