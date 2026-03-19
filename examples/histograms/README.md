# Histogram Sample Data

This folder contains a synthetic summary CSV for testing the Histogram page:

- `synthetic_detector_events_summary.csv`

What it includes:

- 12,800 events total
- 4 channels: `C1`, `C2`, `C3`, `C4`
- timing, duration, amplitude, threshold, phase, cycle, and SNR columns
- additional `EFraw`, `EFdynamic`, and `batch` marker columns used by the app

The file is synthetic and deterministic. It is shaped to resemble detector-style event summaries, with distinct channel populations and useful correlations for histogram exploration.

Recommended first plots:

- `peak_amplitude_mV` by `channel`
- `event_duration_ns` by `channel`
- `peak_amplitude_mV` versus `snr`
- `phase` versus `peak_time_ns`