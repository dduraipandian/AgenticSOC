import httpx

from pipeline.puller.adapters.adapters import Page, PullerAdapter, RawEvent


class EDR(PullerAdapter):
    name = "sentrix-edr"

    def pull(self, client: httpx.Client, checkpoint: dict | None = None) -> Page:
        limit = self.config.get("limit", self.default_limit)
        cursor = checkpoint.get("cursor") if checkpoint else None
        params = {"limit": limit, "cursor": cursor}
        data = self._get(client, params)
        events = [RawEvent(self.name, d["id"], d) for d in data["detections"]]
        return Page(events, {"cursor": data.get("next_cursor")}, bool(data["has_more"]))
