import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from uuid import uuid4

import requests

from services.local_json_service import DATA_DIR, LocalJsonStore


DEFAULT_NOTIFICATION_SETTINGS = {
    "enabled": True,
    "in_app_enabled": True,
    "webhook_enabled": False,
    "webhook_url": "",
    "email_enabled": False,
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_username": "",
    "smtp_password": "",
    "email_from": "",
    "email_to": "",
    "notify_on_sos": True,
    "notify_on_incident": True,
    "notify_on_processing": True,
    "notify_on_sync": True,
}
SENSITIVE_NOTIFICATION_SETTING_KEYS = {"smtp_password", "webhook_url"}


class NotificationService:
    def __init__(self, settings=None):
        self.settings_store = LocalJsonStore(
            DATA_DIR / "notifications" / "notification_settings.json",
            DEFAULT_NOTIFICATION_SETTINGS.copy(),
        )
        self.notification_store = LocalJsonStore(
            DATA_DIR / "notifications" / "notifications.json",
            [],
        )
        self.settings = {
            **DEFAULT_NOTIFICATION_SETTINGS,
            **self.settings_store.read(),
            **(settings or {}),
        }

    def save_settings(self, settings):
        self.settings = {
            **DEFAULT_NOTIFICATION_SETTINGS,
            **settings,
        }
        persistent_settings = {
            key: value
            for key, value in self.settings.items()
            if key not in SENSITIVE_NOTIFICATION_SETTING_KEYS
        }
        return self.settings_store.write(persistent_settings)

    def list_notifications(self, limit=20, unread_only=False):
        notifications = sorted(
            self.notification_store.read(),
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )
        if unread_only:
            notifications = [
                item for item in notifications
                if not item.get("read")
            ]
        return notifications[:limit]

    def unread_count(self):
        return len(self.list_notifications(limit=1000, unread_only=True))

    def mark_all_read(self):
        notifications = self.notification_store.read()
        for item in notifications:
            item["read"] = True
        return self.notification_store.write(notifications)

    def notify(self, title, message, level="info", category="system", payload=None):
        if not self.settings.get("enabled", True):
            return None

        notification = {
            "notification_id": f"note_{uuid4().hex[:8]}",
            "device_id": self.settings.get("device_id", "roadwatch_local_01"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "title": title,
            "message": message,
            "level": level,
            "category": category,
            "payload": payload or {},
            "read": False,
            "delivery": {
                "in_app": False,
                "webhook": "not_configured",
                "email": "not_configured",
            },
        }

        if self.settings.get("in_app_enabled", True):
            notifications = self.notification_store.read()
            notifications.append(notification)
            self.notification_store.write(notifications[-200:])
            notification["delivery"]["in_app"] = True

        notification["delivery"]["webhook"] = self._send_webhook(notification)
        notification["delivery"]["email"] = self._send_email(notification)
        return notification

    def _send_webhook(self, notification):
        if not self.settings.get("webhook_enabled") or not self.settings.get("webhook_url"):
            return "not_configured"
        try:
            response = requests.post(
                self.settings["webhook_url"],
                json=notification,
                timeout=10,
            )
            response.raise_for_status()
            return "sent"
        except Exception as exc:
            return f"failed: {exc}"

    def _send_email(self, notification):
        if not self.settings.get("email_enabled"):
            return "not_configured"
        required = [
            "smtp_host",
            "smtp_username",
            "smtp_password",
            "email_from",
            "email_to",
        ]
        if not all(self.settings.get(key) for key in required):
            return "not_configured"
        try:
            msg = EmailMessage()
            msg["Subject"] = f'Roadwatch: {notification["title"]}'
            msg["From"] = self.settings["email_from"]
            msg["To"] = self.settings["email_to"]
            msg.set_content(notification["message"])
            with smtplib.SMTP(
                self.settings["smtp_host"],
                int(self.settings.get("smtp_port", 587)),
                timeout=15,
            ) as smtp:
                smtp.starttls()
                smtp.login(
                    self.settings["smtp_username"],
                    self.settings["smtp_password"],
                )
                smtp.send_message(msg)
            return "sent"
        except Exception as exc:
            return f"failed: {exc}"
