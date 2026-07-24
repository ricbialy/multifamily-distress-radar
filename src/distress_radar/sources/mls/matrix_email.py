from __future__ import annotations

from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path


@dataclass(frozen=True)
class MatrixAttachment:
    filename: str
    payload: bytes
    source_path: Path


class LocalMatrixInbox:
    """Credential-free local inbox used by fixtures and saved email exports."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def attachments(self) -> tuple[MatrixAttachment, ...]:
        results: list[MatrixAttachment] = []
        for path in sorted(self.directory.iterdir()):
            if path.is_file() and path.suffix.casefold() == ".csv":
                results.append(MatrixAttachment(path.name, path.read_bytes(), path))
            elif path.is_file() and path.suffix.casefold() == ".eml":
                message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
                for part in message.iter_attachments():
                    filename = part.get_filename() or ""
                    if filename.casefold().endswith(".csv"):
                        results.append(
                            MatrixAttachment(
                                filename=filename,
                                payload=part.get_payload(decode=True) or b"",
                                source_path=path,
                            )
                        )
        return tuple(results)
