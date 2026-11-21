from collections.abc import Callable

from pipeline.soc_agent.tools.default import SUBMIT_INVESTIGATION
from pipeline.soc_agent.tools.edr_tools import make_edr_tools
from pipeline.soc_agent.tools.tools import Tool
from vendors.base import VendorAPI

TOOL_LIST = [SUBMIT_INVESTIGATION]

TOOL_FACTORIES: dict[str, Callable[[VendorAPI], list[Tool]]] = {"sentrix-edr": make_edr_tools}
