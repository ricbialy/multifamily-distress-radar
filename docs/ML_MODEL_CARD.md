# ML model card

## Status

No trained or active ML model exists. Production recommendations use the
deterministic rule engine. ML predictions must remain disabled until there are
at least 25 positive and 25 negative real labels for a target and an offline
candidate outperforms the deterministic baseline.

## Intended targets

- serious human review;
- contact leading to negotiation;
- offer made;
- offer accepted.

## Data and leakage controls

`ml.dataset.build_dataset` selects only the latest feature observation at or
before the label timestamp. Later feature observations are excluded.
`ml.evaluate.time_based_split` sorts by observation time and places the newest
rows in validation.

Outcome capture supports review/contact/document/offer/acceptance/close/loss
events, amounts, reasons, and metadata. Actual purchase price, cure/renovation
cost, stabilized NOI, and resale outcome should be captured as available.

## Activation requirements

Before adding an active model:

1. Document every feature, missing-value policy, target, and label horizon.
2. Use a chronological train/validation split.
3. Report precision, recall, PR-AUC, and probability calibration.
4. Compare against the deterministic baseline on the same validation period.
5. Produce calibrated probabilities and interpretable feature contributions.
6. Version the dataset, code, parameters, metrics, and model card.
7. Activate only after repeatable baseline outperformance.
8. Fall back automatically to deterministic rules on model or feature failure.

## Current limitations

The fixtures are synthetic workflow tests and must never be used as training
labels. There is no calibrated probability model, feature-importance report,
or production model registry artifact.
