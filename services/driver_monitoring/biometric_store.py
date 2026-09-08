"""Server-only Firestore storage. No REST, public push URL or local fallback."""
import os
import re
from models.driver_identity import utc_now
from services.firebase_service import FirebaseService
from .face_recognition_service import normalize_embedding

PUBLIC_FIELDS = ("driver_id", "display_name", "vehicle_id", "recognition_enabled", "enrollment_status", "embedding_version", "created_at", "last_recognized")


def require_admin(actor):
    # actor must come from the existing authenticated server session, never request JSON.
    if not actor or actor.get("role") not in {"admin", "super_admin"}:
        raise PermissionError("Administrator access required.")


class BiometricStore:
    def __init__(self, firebase=None):
        self.revision = 0
        self.firebase = firebase or FirebaseService(
            project_id=os.getenv("FIREBASE_PROJECT_ID", ""), client_email=os.getenv("FIREBASE_CLIENT_EMAIL", ""),
            private_key=os.getenv("FIREBASE_PRIVATE_KEY", ""),
        )

    def _db(self):
        if os.getenv("DRIVER_BIOMETRICS_RULES_CONFIGURED", "false").lower() != "true":
            raise RuntimeError("Configure private biometric Firestore rules before enrollment.")
        db = self.firebase._client()
        if db is None:
            raise RuntimeError("Private driver database unavailable.")
        return db

    @staticmethod
    def _id(driver_id):
        if not isinstance(driver_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", driver_id):
            raise ValueError("Driver ID must use 1–64 letters, numbers, underscores or hyphens.")
        return driver_id

    def list_drivers(self, actor):
        require_admin(actor)
        return [{k: doc.to_dict().get(k) for k in PUBLIC_FIELDS} for doc in self._db().collection("drivers").stream(timeout=3)]

    def add_driver(self, actor, driver_id, display_name, vehicle_id=None):
        require_admin(actor)
        self._id(driver_id)
        if not str(display_name).strip():
            raise ValueError("Driver name is required.")
        data = dict(driver_id=driver_id, display_name=str(display_name).strip()[:120], vehicle_id=vehicle_id,
                    recognition_enabled=False, enrollment_status="not_enrolled", created_at=utc_now())
        self._db().collection("drivers").document(driver_id).create(data, timeout=3)
        return data

    def save_enrollment(self, actor, driver_id, embedding):
        require_admin(actor)
        db = self._db()
        ref = db.collection("drivers").document(self._id(driver_id))
        if not ref.get(timeout=3).exists:
            raise ValueError("Driver no longer exists.")
        private = dict(embedding=normalize_embedding(embedding).tolist(), model="SFace", version="sface-v1", updated_at=utc_now())
        metadata = dict(recognition_enabled=True, enrollment_status="complete", embedding_version="sface-v1")
        batch = db.batch()
        batch.set(db.collection("driver_biometrics").document(driver_id), private)
        batch.update(ref, metadata)
        batch.commit(timeout=3)
        self.revision += 1
        return {"driver_id": driver_id, **metadata}

    def set_enabled(self, actor, driver_id, enabled):
        require_admin(actor)
        db = self._db()
        driver_id = self._id(driver_id)
        if enabled:
            bio = db.collection("driver_biometrics").document(driver_id).get(timeout=3)
            data = bio.to_dict() or {}
            if data.get("version") != "sface-v1":
                raise ValueError("Complete enrollment before enabling recognition.")
            normalize_embedding(data.get("embedding"))
        db.collection("drivers").document(driver_id).update({"recognition_enabled": bool(enabled)}, timeout=3)
        self.revision += 1

    def delete_enrollment(self, actor, driver_id):
        require_admin(actor)
        db = self._db()
        driver_id = self._id(driver_id)
        batch = db.batch()
        batch.delete(db.collection("driver_biometrics").document(driver_id))
        batch.update(db.collection("drivers").document(driver_id), {"recognition_enabled": False, "enrollment_status": "removed", "embedding_version": None})
        batch.commit(timeout=3)
        self.revision += 1

    def recognition_profiles(self):
        db = self._db()
        # The worker's DriverEmbeddingIndex caches this restricted read.
        drivers = {doc.id: doc.to_dict() for doc in db.collection("drivers").stream(timeout=3)}
        profiles = []
        for doc in db.collection("driver_biometrics").stream(timeout=3):
            driver = drivers.get(doc.id)
            private = doc.to_dict()
            if not driver or not driver.get("recognition_enabled") or private.get("version") != "sface-v1":
                continue
            try:
                embedding = normalize_embedding(private.get("embedding"))
            except ValueError:
                continue
            profiles.append({"driver_id": doc.id, "display_name": driver.get("display_name", doc.id),
                             "recognition_enabled": True, "embedding_version": "sface-v1", "embedding": embedding})
        return profiles

    def mark_recognized(self, driver_id):
        self._db().collection("drivers").document(self._id(driver_id)).update({"last_recognized": utc_now()}, timeout=3)
