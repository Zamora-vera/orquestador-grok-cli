import threading
import time


def parallel_sum(numbers, workers=4):
    total = 0
    def work(part):
        nonlocal total
        for number in part:
            current = total
            time.sleep(0.0005)
            total = current + number
    parts = [numbers[i::workers] for i in range(workers)]
    threads = [threading.Thread(target=work, args=(part,)) for part in parts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return total
