from __future__ import annotations

from dataclasses import dataclass
import time


@dataclass
class TextLineExtractor:
    """Extract $GLOVITY ASCII lines from a stream that may also contain binary frames."""

    _line: bytearray = None  # type: ignore[assignment]
    _capturing: bool = False

    def __post_init__(self) -> None:
        self._line = bytearray()

    def feed(self, data: bytes) -> list[str]:
        result: list[str] = []
        for value in data:
            if value == ord("$"):
                self._line = bytearray((value,))
                self._capturing = True
            elif not self._capturing:
                continue
            elif value in (10, 13):
                if self._line:
                    result.append(self._line.decode("ascii", errors="replace"))
                self._line.clear()
                self._capturing = False
            elif 32 <= value <= 126 and len(self._line) < 512:
                self._line.append(value)
            else:
                self._line.clear()
                self._capturing = False
        return result


def send_line(port, command: str) -> None:
    port.write((command + "\r\n").encode("ascii"))
    port.flush()


def wait_for_line(port, extractor: TextLineExtractor, predicates, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = port.read(max(1, getattr(port, "in_waiting", 0)))
        for line in extractor.feed(chunk):
            print(line)
            if any(predicate(line) for predicate in predicates):
                return line
        time.sleep(0.005)
    raise TimeoutError("等待设备响应超时")
