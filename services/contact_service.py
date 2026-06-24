from uuid import uuid4

from services.local_json_service import DATA_DIR, LocalJsonStore


class ContactService:
    def __init__(self):
        self.store = LocalJsonStore(
            DATA_DIR / "contacts" / "emergency_contacts.json", []
        )

    def list_contacts(self):
        return self.store.read()

    def add_contact(self, name, phone, relationship, active=True, device_id="roadwatch_local_01"):
        contacts = self.list_contacts()
        contact = {
            "contact_id": f"contact_{uuid4().hex[:8]}",
            "device_id": device_id,
            "name": name,
            "phone": phone,
            "relationship": relationship,
            "active_for_sos": active,
        }
        contacts.append(contact)
        self.store.write(contacts)
        return contact

    def update_contacts(self, contacts):
        return self.store.write(contacts)

    def active_count(self):
        return sum(
            1 for item in self.list_contacts()
            if item.get("active_for_sos")
        )
