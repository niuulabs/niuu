# No Magic Numbers

## Rule

No hardcoded timing values, thresholds, intervals, or counts in business logic code. All such values must come from the package's configuration model (`src/<package>/config.py`) with sensible defaults.

## What Counts as a Magic Number

- Timing intervals (sleep durations, timeouts, TTLs)
- Thresholds (confidence levels, score minimums, capacity limits)
- Counts (max retries, buffer sizes, batch sizes)
- Multipliers and scaling factors used in algorithms

## Exceptions

- Loop indices and array positions (0, 1, -1)
- Mathematical constants (0.0, 1.0 as identity values)
- Test fixtures (test data can use literal values)
- Log messages and string formatting

## How to Fix

1. Add the value to the appropriate config class in `src/<package>/config.py`
2. Give it a sensible default so existing behavior doesn't change
3. Thread the config value through to where it's used
4. Expose it where the service's configuration is rendered (Helm chart values and config templates, example configs) when operators may need to change it
