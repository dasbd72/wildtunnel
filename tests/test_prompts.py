import click
import pytest

from wildtunnel.prompts import confirm_overwrite


def test_no_prompt_when_yes_flag_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        click,
        "confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt")),
    )

    confirm_overwrite("old", "new", yes=True)


def test_no_prompt_when_values_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        click,
        "confirm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not prompt")),
    )

    confirm_overwrite("same", "same", yes=False)


def test_prompts_and_aborts_when_declined(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        click, "confirm", lambda *a, **k: (_ for _ in ()).throw(click.Abort())
    )

    with pytest.raises(click.Abort):
        confirm_overwrite("old", "new", yes=False)


def test_prompts_and_proceeds_when_confirmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(click, "confirm", lambda *a, **k: True)

    confirm_overwrite("old", "new", yes=False)
