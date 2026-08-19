from __future__ import annotations

import pytest


def test_known_slugs():
    from pi_tracker.team_config import resolve_team_slug

    assert resolve_team_slug("angels")[0] == 108
    assert resolve_team_slug("yankees") == (147, "NYY", "Yankees")
    assert resolve_team_slug("dodgers")[1] == "LAD"
    assert resolve_team_slug("as")[0] == 133
    assert resolve_team_slug("white_sox")[1] == "CWS"


def test_unknown_slug_exits():
    from pi_tracker.team_config import resolve_team_slug

    with pytest.raises(SystemExit):
        resolve_team_slug("not-a-team")
    with pytest.raises(SystemExit):
        resolve_team_slug("")


def test_numeric_id_hits_api(monkeypatch):
    from pi_tracker import team_config

    class Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"teams": [{"id": 147, "abbreviation": "NYY", "teamName": "Yankees"}]}

    monkeypatch.setattr(team_config.requests, "get", lambda *a, **k: Resp())
    assert team_config.resolve_team_slug("147") == (147, "NYY", "Yankees")


def test_apply_team_cli_arg(monkeypatch):
    import sys

    from pi_tracker import team_config

    monkeypatch.setattr(sys, "argv", ["run_pi_ui.py", "mets", "--demo"])
    team_config.apply_team_cli_arg()
    assert sys.argv == ["run_pi_ui.py", "--demo"]
    assert team_config.tracked_team_id() == 121
    assert team_config.tracked_team_abbr() == "NYM"
