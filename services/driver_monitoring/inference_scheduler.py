"""Small model-independent scheduler; it owns no threads, frames or models."""
from dataclasses import dataclass


@dataclass
class InferenceTask:
    enabled: bool
    priority: int
    target_interval: float
    last_inference: float = -float("inf")


class InferenceScheduler:
    def __init__(self):
        self.tasks = {}

    def configure(self, name, *, enabled, priority, interval):
        if interval <= 0:
            raise ValueError("Inference interval must be positive.")
        if name not in self.tasks:
            self.tasks[name] = InferenceTask(enabled, priority, interval)
        else:
            task = self.tasks[name]
            task.enabled, task.priority, task.target_interval = enabled, priority, interval

    def due(self, name, now):
        task = self.tasks[name]
        return task.enabled and now - task.last_inference >= task.target_interval

    def ready(self, now):
        return sorted((name for name in self.tasks if self.due(name, now)), key=lambda name: -self.tasks[name].priority)

    def mark(self, name, now):
        self.tasks[name].last_inference = now

    def delay(self, now):
        return max(.01, min((max(0, task.last_inference + task.target_interval - now)
                            for task in self.tasks.values() if task.enabled), default=1))

    def snapshot(self):
        return {name: {"enabled": task.enabled, "priority": task.priority,
                       "target_interval": task.target_interval,
                       "last_inference": None if task.last_inference == -float("inf") else task.last_inference}
                for name, task in self.tasks.items()}
