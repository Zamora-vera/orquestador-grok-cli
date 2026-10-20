import time


class Counter:
    def __init__(self):
        self.value = 0

    def increment(self):
        current = self.value
        time.sleep(0.001)
        self.value = current + 1
