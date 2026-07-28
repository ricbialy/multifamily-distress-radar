from distress_radar.sources.vendors.base import VendorAdapter


class RentCastAdapter(VendorAdapter):
    name = "rentcast"
    credential_environment_variable = "RENTCAST_API_KEY"
