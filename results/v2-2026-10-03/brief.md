# DoorDash Bench v2

For each task, assign a probability to every fixed meal choice from the provided history, using patterns like frequency, recency, and timing. Probabilities must be nonnegative and sum to 1 (within 0.005). Avoid overconfidence, since Brier loss penalizes confident misses. Predict only known menu items; each task runs in isolation.

Score is 100 × (1 − Brier loss/2), averaged over all four tasks. Brier loss sums squared errors against the true meal. Lower loss gives a higher score; 100 is perfect.
