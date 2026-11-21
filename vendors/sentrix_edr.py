from vendors.base import VendorAPI

DEFAULT_LIMIT = 100


class SentrixEDR(VendorAPI):
    name = "sentrix-edr"

    def get_detections(self, cursor: str | None, limit: int = DEFAULT_LIMIT) -> dict:
        detections_config = self.api_config.get("detections", {})
        path = detections_config["path"]
        data = self._get(path, limit=limit, cursor=cursor)
        return data

    def get_host(self, host: str) -> dict:
        hosts_config = self.api_config.get("hosts", {})
        path = hosts_config["path"]
        data = self._get(path, host=host)
        return data

    def get_process_tree(self, host: str, pid: int, at_ms: int | None = None) -> dict:
        process_tree_config = self.api_config.get("process_tree", {})
        path = process_tree_config["path"]
        # at_ms: the process that had this PID at that time, not the newest one (PIDs are reused)
        data = self._get(path, host=host, pid=pid, at_ms=at_ms)
        return data

    def search_telemetry(
        self,
        host: str,
        contains: str | None,
        since_ms: int | None,
        limit: int = 20,
        until_ms: int | None = None,
    ) -> dict:
        telemetry_config = self.api_config.get("telemetry", {})
        path = telemetry_config["path"]
        data = self._get(
            path, host=host, limit=limit, contains=contains, since_ms=since_ms, until_ms=until_ms
        )
        return data
