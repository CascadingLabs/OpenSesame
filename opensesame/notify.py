from __future__ import annotations

import shutil
import subprocess
import webbrowser


def notify_takeover(title: str, message: str) -> None:
    if not shutil.which("notify-send"):
        return
    subprocess.run(
        ["notify-send", title, message],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def open_operator(url: str) -> None:
    webbrowser.open(url)
