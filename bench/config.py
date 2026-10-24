"""Benchmark settings, committed before any benchmark run (spec 5.1 fairness, M12).

Changing any value here after results exist needs a one-line reason in evidence/target-changes.md.
"""

# --- XGBoost-alone baseline: retraining schedule (fixed before results are seen) ----------
XGB_FIRST_TRAIN_AFTER_ROUND = 1  # first fit uses round 1's labels
XGB_RETRAIN_EVERY_N_ROUNDS = 2  # then refit on all labels so far after rounds 3, 5, 7, ...
XGB_PARAMS = {
    "n_estimators": 150,
    "max_depth": 4,
    "learning_rate": 0.1,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "tree_method": "hist",
    "n_jobs": 1,
    "random_state": 42,
}

# --- Decision thresholds on a suspicion score in [0, 1] ---------------------------------
BLOCK_AT = 0.80
STEP_UP_AT = 0.50

# --- Cost matrix (cents), used for total cost (spec 5.1) --------------------------------
# Missed fraud costs its full amount. A stepped-up fraud still gets through at the attacker's
# step-up pass rate. A false block loses the merchant's margin plus a fixed customer-goodwill
# cost; a legit step-up costs friction, and some shoppers abandon the purchase.
MERCHANT_MARGIN = 0.30
FALSE_BLOCK_FIXED_CENTS = 200
STEP_UP_FRICTION_CENTS = 50
LEGIT_STEP_UP_ABANDON_RATE = 0.10
FRAUD_STEP_UP_PASS_RATE = 0.20

# --- Metrics ----------------------------------------------------------------------------
TARGET_FPR = 0.01  # recall is reported at this false-positive rate
SHELF_DEFENDED_CATCH_RATE = 0.80  # a filled shelf counts as defended at or above this catch rate
N_SHELVES = 24
MAIN_WAVES = (0, 1, 2)  # the round's main traffic (spec 3.9, point 7)
EXAM_WAVE = 3  # proposed marker for frozen exploitability-exam traffic; see CONTRACT_NOTES.md
