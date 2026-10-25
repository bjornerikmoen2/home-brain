from __future__ import annotations

import dataclasses
import datetime as dt
from string import Formatter

import pytest

from home_brain.texts import EN, LANGUAGES, NB, Texts

TEMPLATES = [f.name for f in dataclasses.fields(Texts) if f.type == "str"]


def placeholders(template: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(template) if name}


@pytest.mark.parametrize("name", TEMPLATES)
def test_translation_uses_only_known_placeholders(name: str) -> None:
    # The callers fill in what the English template needs; a translation may leave some out.
    assert placeholders(getattr(NB, name)) <= placeholders(getattr(EN, name))


def test_dates() -> None:
    day = dt.date(2026, 10, 26)
    assert (EN.long_date(day), EN.short_date(day), EN.day_month(day)) == ("Monday 26 October", "Mon 26 Oct", "26 Oct")
    assert (NB.long_date(day), NB.short_date(day), NB.day_month(day)) == ("mandag 26. oktober", "man. 26. okt.", "26. okt.")
    assert NB.short_date(dt.date(2027, 3, 28)) == "søn. 28. mars"


def test_numbers() -> None:
    assert (EN.number(3.5), EN.number(3.0), NB.number(3.5), NB.number(1.6)) == ("3.5", "3", "3,5", "1,6")


def test_languages() -> None:
    assert LANGUAGES == {"en": EN, "nb": NB, "no": NB}
    assert all(len(t.weekdays) == len(t.weekdays_short) == 7 for t in (EN, NB))
    assert all(len(t.months) == len(t.months_short) == 12 for t in (EN, NB))
