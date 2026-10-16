"""reduce.py on its own: no Alertmanager needed."""

from reduce import reduce

from amlint.routing import Router


def test_reduce_keeps_only_what_the_failure_needs():
    cfg = {
        "route": {"receiver": "root", "routes": [
            {"matchers": ['env="prod"'], "receiver": "prod"},
            {"matchers": ['team="a"', 'env!="dev"'], "receiver": "a", "continue": True,
             "routes": [{"matchers": ['sev="crit"'], "receiver": "a-pager"}]},
            {"matchers": ['team="b"'], "receiver": "b"},
        ]},
        "receivers": [{"name": n} for n in ("root", "prod", "a", "a-pager", "b")],
    }
    labels = {"team": "a", "sev": "crit", "env": "staging", "svc": "api"}

    def fails(case):  # stands in for "amlint and Alertmanager disagree"
        try:
            matches = Router(case["config"]["route"]).match(case["labels"])
        except Exception:
            return False
        return "a-pager" in [m.receiver for m in matches]

    reduced = reduce({"config": cfg, "labels": labels}, fails)
    assert reduced["labels"] == {}
    # the child route is hoisted out of its parent, everything else is gone
    assert reduced["config"] == {"route": {"routes": [{"receiver": "a-pager"}]}}


def test_reduce_returns_the_case_when_nothing_can_go():
    case = {"a": [1, 2]}
    assert reduce(case, lambda c: c == case) == case
