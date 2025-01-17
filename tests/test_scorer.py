import logging

import numpy as np

from emmental.scorer import Scorer


def test_scorer(caplog):
    """Unit test of scorer"""

    caplog.set_level(logging.INFO)

    golds = np.array([1, 0, 1, 0, 1, 0])
    preds = np.array([1, 1, 1, 1, 1, 0])
    probs = np.array([0.8, 0.6, 0.9, 0.7, 0.7, 0.2])

    def sum(gold, probs, preds, uids):
        return np.sum(preds)

    scorer = Scorer(metrics=["accuracy", "f1"], customize_metric_funcs={"sum": sum})

    assert scorer.score(golds, probs, preds) == {
        "accuracy": 0.6666666666666666,
        "f1": 0.7499999999999999,
        "sum": 5,
    }
