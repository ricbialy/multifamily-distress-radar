from distress_radar.sources.vendors.base import VendorAdapter


class MelissaAdapter(VendorAdapter):
    name = "melissa"
    credential_environment_variable = "MELISSA_API_KEY"
