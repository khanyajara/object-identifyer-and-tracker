import hashlib
def hash_password(password):
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


DEVELOPMENT_ADMINS = [
    {
        "username": "superadmin",
        "password_hash": "d3535b78e24867f3c850fec1c8591b7a469b8f8683be64d835057cb9fd204aa1",
        "role": "super_admin",
        "development_credential": True,
    },
    {
        "username": "admin1",
        "password_hash": "766340237de86504f7fecbf29247badb220fc00ce1aa7c35d1c9b02035588e3d",
        "role": "admin",
        "development_credential": True,
    },
    {
        "username": "admin2",
        "password_hash": "dad078d9f6a678558455993a9ee3a6d64546f27ab7443824533dcaffeaffc3cc",
        "role": "admin",
        "development_credential": True,
    },
]


class AdminAuthService:
    def __init__(self):
        self.admins = DEVELOPMENT_ADMINS

    def authenticate(self, username, password):
        username = (username or "").strip()
        password_hash = hash_password(password or "")
        for admin in self.admins:
            if admin["username"] == username and admin["password_hash"] == password_hash:
                return {
                    "username": admin["username"],
                    "role": admin["role"],
                    "development_credential": admin.get("development_credential", False),
                }
        return None

    def list_admins(self):
        return [
            {
                "username": admin["username"],
                "role": admin["role"],
                "development_credential": admin.get("development_credential", False),
            }
            for admin in self.admins
        ]

    def development_credentials_enabled(self):
        return any(admin.get("development_credential") for admin in self.admins)
