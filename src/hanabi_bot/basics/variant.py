"""Variant definitions, suit-type classification, and clue-touch rules.

Port of scala-bot/src/scala_bot/basics/Variant.scala.

A Variant is a named ruleset for Hanabi:
- Suit composition (typically 5 or 6 suits, some with special clue behavior)
- Special-rank modifiers (special_rank, rainbow_s, white_s, etc.)
- Score-curve modifiers (critical_rank, clue_starved, scarce_ones)

Suit classification is done via regex match on the suit name. See GLOSSARY.md
section "Variant terms" for the predicate semantics.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any

from .identity import Identity

# Regex predicates classifying suit names. Match with re.search (unanchored substring match).
# Source: scala-bot/src/scala_bot/basics/Variant.scala lines 9-16.
WHITISH = re.compile(r"White|Gray|Light|Null")
RAINBOWISH = re.compile(r"Rainbow|Omni")
PINKISH = re.compile(r"Pink|Omni")
BROWNISH = re.compile(r"Brown|Muddy|Cocoa|Null")
DARK = re.compile(r"Black|Dark|Gray|Cocoa")
PRISM = re.compile(r"Prism")
MUDDY = re.compile(r"Muddy|Cocoa")
NO_COLOUR = re.compile(r"White|Gray|Light|Null|Rainbow|Omni|Prism")

# Clue kind constants. Duplicated from constants.py to avoid an extra import here;
# the canonical names live in basics/clue.py (ClueKind enum).
_CLUE_KIND_COLOUR = 0
_CLUE_KIND_RANK = 1


@dataclass(frozen=True, slots=True)
class SuitType:
    """Boolean classification of a suit derived from its name."""

    whitish: bool
    rainbowish: bool
    pinkish: bool
    brownish: bool
    dark: bool
    prism: bool
    muddy: bool

    @classmethod
    def of_name(cls, name: str) -> SuitType:
        return cls(
            whitish=bool(WHITISH.search(name)),
            rainbowish=bool(RAINBOWISH.search(name)),
            pinkish=bool(PINKISH.search(name)),
            brownish=bool(BROWNISH.search(name)),
            dark=bool(DARK.search(name)),
            prism=bool(PRISM.search(name)),
            muddy=bool(MUDDY.search(name)),
        )


@dataclass(frozen=True, slots=True)
class Suit:
    name: str
    abbreviation: str | None  # single lowercase character, or None
    suit_type: SuitType


@dataclass(frozen=True, slots=True)
class Variant:
    """A complete variant ruleset."""

    id: int
    name: str
    suits: tuple[Suit, ...]
    short_forms: tuple[str, ...]
    colourable_suits: tuple[Suit, ...]
    critical_rank: int | None
    clue_starved: bool
    special_rank: int | None
    rainbow_s: bool   # specialRankAllClueColors
    white_s: bool     # specialRankNoClueColors
    pink_s: bool      # specialRankAllClueRanks
    brown_s: bool     # specialRankNoClueRanks
    deceptive_s: bool # specialRankDeceptive
    scarce_ones: bool # scarceOnes

    def all_ids(self) -> list[Identity]:
        """All (suit_index, rank) pairs across this variant."""
        return [Identity(s, r) for s in range(len(self.suits)) for r in range(1, 6)]

    def card_count(self, id_: Identity) -> int:
        """Number of physical copies of this identity in the deck."""
        if self.suits[id_.suit_index].suit_type.dark or self.critical_rank == id_.rank:
            return 1
        if id_.rank == 1 and self.scarce_ones:
            return 2
        return (3, 2, 2, 2, 1)[id_.rank - 1]

    @property
    def total_cards(self) -> int:
        return sum(self.card_count(i) for i in self.all_ids())

    def id_touched(self, id_: Identity, clue_kind: int, clue_value: int) -> bool:
        """Whether a clue of (kind, value) touches the given identity.

        Port of Variant.idTouched (scala-bot/.../Variant.scala lines 55-94).

        :param clue_kind: 0 = colour, 1 = rank
        :param clue_value: colour index for a colour clue (into colourable_suits),
                           or rank value (1..5) for a rank clue.
        """
        suit = self.suits[id_.suit_index]
        rank = id_.rank
        st = suit.suit_type

        if clue_kind == _CLUE_KIND_COLOUR:
            if st.rainbowish:
                return True
            if st.whitish:
                return False
            if self.special_rank == rank:
                if self.rainbow_s:
                    return True
                if self.white_s:
                    return False
            if st.prism:
                return ((rank - 1) % len(self.colourable_suits)) == clue_value
            return suit == self.colourable_suits[clue_value]

        # Rank clue
        if st.pinkish:
            return True
        if st.brownish:
            return False
        if self.special_rank == rank:
            if self.pink_s:
                return rank != clue_value
            if self.brown_s:
                return False
            if self.deceptive_s:
                return (id_.suit_index % 4) + (2 if rank == 1 else 1) == clue_value
        return rank == clue_value

    def touch_possibilities(self, clue_kind: int, clue_value: int) -> list[Identity]:
        return [i for i in self.all_ids() if self.id_touched(i, clue_kind, clue_value)]


# --- Loaders ---

_suit_cache: dict[str, Suit] = {}
_variant_cache: dict[str, Variant] = {}


def _load_suit_catalog() -> dict[str, Suit]:
    """Load the global suit catalog from the vendored suits.json. Cached."""
    if _suit_cache:
        return _suit_cache
    text = resources.files("hanabi_bot").joinpath("data", "suits.json").read_text(encoding="utf-8")
    raw = json.loads(text)
    for entry in raw:
        name = entry["name"]
        raw_abbrev = entry.get("abbreviation")
        abbrev = raw_abbrev[0].lower() if isinstance(raw_abbrev, str) and raw_abbrev else None
        _suit_cache[name] = Suit(name=name, abbreviation=abbrev, suit_type=SuitType.of_name(name))
    return _suit_cache


def _pick_short(sname: str, catalog: dict[str, Suit], short_forms: list[str]) -> str:
    """Pick a one-character short form for this suit, avoiding collisions.

    Port of the inner match expression of Variant.apply (scala lines 170-181).
    """
    if sname == "Black":
        return "k"
    if sname == "Pink":
        return "i"
    if sname == "Brown":
        return "n"
    catalog_entry = catalog.get(sname)
    candidate = (catalog_entry.abbreviation if catalog_entry else None) or sname[0].lower()
    if candidate not in short_forms:
        return candidate
    fallback = next((c for c in sname.lower() if c not in short_forms), None)
    if fallback is None:
        raise ValueError(f"No unused character found for suit '{sname}'")
    return fallback


def _make_variant(
    *,
    id_: int,
    name: str,
    suit_names: list[str],
    critical_rank: int | None = None,
    clue_starved: bool = False,
    special_rank: int | None = None,
    rainbow_s: bool = False,
    white_s: bool = False,
    pink_s: bool = False,
    brown_s: bool = False,
    deceptive_s: bool = False,
    scarce_ones: bool = False,
) -> Variant:
    """Build a Variant from suit names, deriving suits and short forms.

    Port of Variant.apply (scala lines 145-193).
    """
    catalog = _load_suit_catalog()
    suits: list[Suit] = []
    short_forms: list[str] = []
    colourable: list[Suit] = []

    for sname in suit_names:
        short = _pick_short(sname, catalog, short_forms)
        catalog_entry = catalog.get(sname)
        suit = catalog_entry if catalog_entry is not None else Suit(
            name=sname,
            abbreviation=short,
            suit_type=SuitType.of_name(sname),
        )
        suits.append(suit)
        short_forms.append(short)
        if not NO_COLOUR.search(sname):
            colourable.append(suit)

    return Variant(
        id=id_,
        name=name,
        suits=tuple(suits),
        short_forms=tuple(short_forms),
        colourable_suits=tuple(colourable),
        critical_rank=critical_rank,
        clue_starved=clue_starved,
        special_rank=special_rank,
        rainbow_s=rainbow_s,
        white_s=white_s,
        pink_s=pink_s,
        brown_s=brown_s,
        deceptive_s=deceptive_s,
        scarce_ones=scarce_ones,
    )


def _variant_from_json(entry: dict[str, Any]) -> Variant:
    """Build a Variant from a single variants.json entry. Port of Variant.fromJSON."""
    return _make_variant(
        id_=int(entry["id"]),
        name=entry["name"],
        suit_names=list(entry["suits"]),
        critical_rank=entry.get("criticalRank"),
        clue_starved=entry.get("clueStarved", False),
        special_rank=entry.get("specialRank"),
        rainbow_s=entry.get("specialRankAllClueColors", False),
        white_s=entry.get("specialRankNoClueColors", False),
        pink_s=entry.get("specialRankAllClueRanks", False),
        brown_s=entry.get("specialRankNoClueRanks", False),
        deceptive_s=entry.get("specialRankDeceptive", False),
        scarce_ones=entry.get("scarceOnes", False),
    )


def load_variants() -> dict[str, Variant]:
    """Load all variants from the vendored variants.json. Cached after first call."""
    if _variant_cache:
        return _variant_cache
    _load_suit_catalog()
    text = resources.files("hanabi_bot").joinpath("data", "variants.json").read_text(encoding="utf-8")
    raw = json.loads(text)
    for entry in raw:
        v = _variant_from_json(entry)
        _variant_cache[v.name] = v
    return _variant_cache


def get_variant(name: str) -> Variant:
    """Look up a variant by name. Raises ValueError if not found."""
    variants = load_variants()
    v = variants.get(name)
    if v is None:
        raise ValueError(f"Variant {name!r} not found")
    return v
