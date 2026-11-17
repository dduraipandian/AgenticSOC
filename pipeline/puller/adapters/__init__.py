from pipeline.puller.adapters.adapters import PullerAdapter
from pipeline.puller.adapters.edr import EDR

ADAPTERS: dict[str, type[PullerAdapter]] = {EDR.name: EDR}
