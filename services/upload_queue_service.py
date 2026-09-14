from uuid import uuid4

from services.local_json_service import DATA_DIR, LocalJsonStore

from core.time_utils import utc_now


class UploadQueueService:
    def __init__(self):
        self.store = LocalJsonStore(DATA_DIR / "uploads" / "upload_queue.json", [])

    def list_tasks(self):
        tasks = [
            task for task in self.store.read()
            if not str(task.get("video_id", "")).startswith("vid_smoke")
        ]
        return sorted(
            tasks,
            key=lambda item: item.get("updated_at", ""),
            reverse=True,
        )

    def create_task(self, video_id, message="Upload queued"):
        tasks = self.list_tasks()
        task = {
            "task_id": f"upload_{uuid4().hex[:10]}",
            "video_id": video_id,
            "status": "pending",
            "progress": 0,
            "message": message,
            "error": None,
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
        tasks.append(task)
        self.store.write(tasks)
        return task

    def update_task(self, task_id, status=None, progress=None, message=None, error=None):
        tasks = self.list_tasks()
        for task in tasks:
            if task.get("task_id") == task_id:
                if status is not None:
                    task["status"] = status
                if progress is not None:
                    task["progress"] = int(progress)
                if message is not None:
                    task["message"] = message
                task["error"] = error
                task["updated_at"] = utc_now()
                self.store.write(tasks)
                return task
        return None

    def active_tasks(self):
        return [
            task for task in self.list_tasks()
            if task.get("status") not in {"complete", "failed"}
        ]

    def retry_task(self, video_id, message="Retrying upload"):
        """Create a new queued attempt while preserving the history of the failure."""
        return self.create_task(video_id, message)
