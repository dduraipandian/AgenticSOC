from pipeline.puller.adapters.adapters import Page, PullerAdapter, RawEvent
from vendors.sentrix_edr import SentrixEDR


class EDR(PullerAdapter):
    name = "sentrix-edr"
    api_class = SentrixEDR
    api: SentrixEDR

    def pull(self, checkpoint: dict | None = None) -> Page:
        cursor = checkpoint.get("cursor") if checkpoint else None
        data = self.api.get_detections(cursor, self.limit)
        events = [RawEvent(self.name, d["id"], d) for d in data["detections"]]
        return Page(events, {"cursor": data.get("next_cursor")}, bool(data["has_more"]))
