from distress_radar.sources.vendors.base import VendorAdapter


class PropertyRadarAdapter(VendorAdapter):
    name = "propertyradar"
    credential_environment_variable = "PROPERTYRADAR_API_KEY"
