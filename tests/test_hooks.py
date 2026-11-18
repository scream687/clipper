from app import hooks


def test_the_skill_scorer_picks_the_payoff_hook():
    if hooks.scores(["a"]) is None:  # master-virality not installed here: the first hook is used, covered below
        return
    assert hooks.best_hook(["Hello everyone welcome back", "I lost 50 lakh in one week doing this"]) == \
        "I lost 50 lakh in one week doing this"


def test_without_the_scorer_the_ai_order_is_kept(monkeypatch):
    monkeypatch.setattr(hooks, "SCORER", hooks.Path("/nonexistent/hookscore.py"))
    assert hooks.best_hook(["first  line", "second"]) == "first line"
    assert hooks.best_hook(["", "  "]) == ""
