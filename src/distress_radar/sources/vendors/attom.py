from distress_radar.sources.vendors.base import VendorAdapter


class AttomAdapter(VendorAdapter):
    name = "attom"
    credential_environment_variable = "ATTOM_API_KEY"
