import os


def read_file(base, name):
    return open(os.path.join(base, name)).read()
