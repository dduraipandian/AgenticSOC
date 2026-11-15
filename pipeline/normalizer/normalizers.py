from pipeline.alerts.edr import EDR

# source -> normalizer class. Every normalizer is built as cls(event_id, raw) and has .alert().
NORMALIZERS: dict[str, type[EDR]] = {
    "sentrix-edr": EDR
}
