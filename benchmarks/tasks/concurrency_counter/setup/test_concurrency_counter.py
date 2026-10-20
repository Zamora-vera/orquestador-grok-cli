import threading
from counter import Counter


def test_threads():
    counter = Counter()
    def work():
        for _ in range(50):
            counter.increment()
    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert counter.value == 400
