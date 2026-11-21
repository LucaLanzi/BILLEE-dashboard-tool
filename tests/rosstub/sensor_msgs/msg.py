class _Header:
    stamp = None
    frame_id = ""


class Joy:
    def __init__(self):
        self.header = _Header()
        self.axes = []
        self.buttons = []
