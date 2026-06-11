import cv2


class MovementDetector:
    def __init__(self, threshold=10.0):
        self.previous = None
        self.threshold = threshold

    def detect(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (11, 11), 0)
        if self.previous is None:
            self.previous = gray
            return False, 0.0
        score = float(cv2.absdiff(self.previous, gray).mean())
        self.previous = gray
        return score >= self.threshold, round(score, 2)
