from vendors.base import VendorAPI
from vendors.sentrix_edr import SentrixEDR

VENDORS: dict[str, type[VendorAPI]] = {SentrixEDR.name: SentrixEDR}
