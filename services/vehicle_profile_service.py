from services.local_json_service import DATA_DIR, LocalJsonStore


DEFAULT_PROFILE = {
    "vehicle_make": "",
    "vehicle_model": "",
    "year": "",
    "colour": "",
    "registration_plate": "",
    "driver_name": "",
    "company_fleet_name": "",
}


class VehicleProfileService:
    def __init__(self):
        self.store = LocalJsonStore(
            DATA_DIR / "profile" / "vehicle_profile.json",
            DEFAULT_PROFILE.copy(),
        )

    def get_profile(self):
        return {**DEFAULT_PROFILE, **self.store.read()}

    def save_profile(self, profile):
        return self.store.write({**DEFAULT_PROFILE, **profile})
