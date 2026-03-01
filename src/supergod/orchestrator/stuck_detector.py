"""Detect repetitive worker output that indicates a stuck loop."""

from collections import Counter, deque


class StuckDetector:
    def __init__(self, window_size: int = 10, threshold: float = 0.7):
        self.window_size = window_size
        self.threshold = threshold
        self._buffers: dict[str, deque[str]] = {}

    def feed(self, subtask_id: str, output_text: str) -> bool:
        text = (output_text or "").strip()
        if not text:
            return False
        if text.startswith("[") and text.endswith("]"):
            return False

        buf = self._buffers.setdefault(
            subtask_id, deque(maxlen=self.window_size)
        )
        buf.append(text[:200])
        if len(buf) < self.window_size:
            return False

        counts = Counter(buf)
        most_common_count = counts.most_common(1)[0][1]
        return (most_common_count / len(buf)) >= self.threshold

    def clear(self, subtask_id: str) -> None:
        self._buffers.pop(subtask_id, None)
