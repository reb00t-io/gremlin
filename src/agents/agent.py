from dataclasses import dataclass
from typing import Protocol

class CmdResultLike(Protocol):
    returncode: int
    stdout: str
    stderr: str

@dataclass
class CmdResult:
    returncode: int
    stdout: str
    stderr: str
