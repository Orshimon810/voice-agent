import pytest

from app.hebrew import normalize_number, normalize_time


class TestNormalizeTime:
    @pytest.mark.parametrize(
        "time_str,expected",
        [
            ("06:00", "שש בבוקר"),
            ("23:45", "רבע לחצות"),
            ("14:30", "שתיים וחצי בצהריים"),
            ("08:15", "שמונה ורבע בבוקר"),
            ("12:00", "שתים עשרה בצהריים"),
            ("00:30", "שתים עשרה וחצי בלילה"),
        ],
    )
    def test_required_examples(self, time_str: str, expected: str) -> None:
        assert normalize_time(time_str) == expected

    def test_midnight_exact(self) -> None:
        assert normalize_time("00:00") == "שתים עשרה בלילה"

    def test_noon_exact(self) -> None:
        assert normalize_time("12:00") == "שתים עשרה בצהריים"

    def test_arbitrary_minute(self) -> None:
        assert normalize_time("08:10") == "שמונה ועשר בבוקר"

    def test_quarter_to_non_midnight(self) -> None:
        assert normalize_time("08:45") == "רבע לתשע בבוקר"

    def test_quarter_to_noon(self) -> None:
        assert normalize_time("11:45") == "רבע לשתים עשרה בצהריים"

    def test_evening_boundary(self) -> None:
        assert normalize_time("17:00") == "חמש בערב"

    def test_late_night_boundary(self) -> None:
        assert normalize_time("21:00") == "תשע בלילה"

    def test_empty_string(self) -> None:
        with pytest.raises(ValueError):
            normalize_time("")

    def test_malformed_missing_colon(self) -> None:
        with pytest.raises(ValueError):
            normalize_time("0600")

    def test_malformed_non_numeric(self) -> None:
        with pytest.raises(ValueError):
            normalize_time("ab:cd")

    def test_hour_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            normalize_time("25:00")

    def test_minute_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            normalize_time("12:60")

    def test_never_uses_masculine_one_or_two(self) -> None:
        # Hour names must use feminine forms: never "שניים" or "אחד"
        for h in range(24):
            spoken = normalize_time(f"{h:02d}:00")
            assert "שניים" not in spoken
            assert "אחד " not in spoken and not spoken.startswith("אחד")


class TestNormalizeNumber:
    def test_six(self) -> None:
        assert normalize_number(6) == "שש"

    def test_three_thousand_six_hundred_seventy(self) -> None:
        assert normalize_number(3670) == "שלושת אלפים שש מאות ושבעים"

    def test_zero(self) -> None:
        assert normalize_number(0) == "אפס"

    def test_compound_hundreds_tens_units_single_vav(self) -> None:
        # Exactly one "ו" total, right before the final atomic term.
        assert normalize_number(623) == "שש מאות עשרים ושלוש"

    def test_round_hundred(self) -> None:
        assert normalize_number(100) == "מאה"

    def test_two_hundred_special_form(self) -> None:
        assert normalize_number(200) == "מאתיים"

    def test_round_thousand(self) -> None:
        assert normalize_number(1000) == "אלף"

    def test_two_thousand_special_form(self) -> None:
        assert normalize_number(2000) == "אלפיים"

    def test_teen(self) -> None:
        assert normalize_number(15) == "חמש עשרה"

    def test_flight_codes_not_transformed(self) -> None:
        # normalize_number only ever receives ints from callers; flight
        # codes like "LY315" are never passed through it.
        with pytest.raises(ValueError):
            normalize_number("LY315")  # type: ignore[arg-type]

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_number(-5)

    def test_non_int_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_number(6.5)  # type: ignore[arg-type]

    def test_bool_raises(self) -> None:
        # bool is a subclass of int in Python; explicitly rejected.
        with pytest.raises(ValueError):
            normalize_number(True)  # type: ignore[arg-type]

    def test_out_of_supported_range(self) -> None:
        with pytest.raises(ValueError):
            normalize_number(1_000_000)
