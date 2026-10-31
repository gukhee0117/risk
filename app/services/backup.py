"""Atomic daily whole-DB snapshots; rotate only files owned by this feature."""

import os
import re
from pathlib import Path
from uuid import uuid4

from app.domain import RiskError
from app.repositories.sqlite import Repository
from app.services.batch import batch_lock


def signature(repository):
    with repository.snapshot() as db:
        run = db.execute('SELECT run_id,status FROM batch_run ORDER BY rowid DESC LIMIT 1').fetchone()
        grades = [tuple(r) for r in db.execute('SELECT ticker,analysis_version FROM risk_grade ORDER BY ticker')]
        return (tuple(run) if run else None, grades)


def daily_backup(repository, target, directory: Path | None = None, keep=7):
    directory = directory or repository.path.parent / 'backup'
    # The source and managed snapshots must never have the same file name.
    destination = directory / f'risk-{target.isoformat()}.sqlite3'
    if keep < 1 or destination.resolve() == repository.path.resolve():
        raise RiskError('INVALID_BACKUP_PATH', '백업 경로와 보관 개수를 확인하세요.', 503)
    temporary = directory / f'.risk-backup-{uuid4().hex}.sqlite3'
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with batch_lock(directory / '.backup.lock'):
            if destination.exists():
                try:
                    if signature(Repository(destination)) == signature(repository):
                        return destination
                except RiskError:
                    pass  # Replace an unreadable managed backup using the good source.
            # Publish only a complete SQLite backup, preserving the last good file.
            repository.backup(temporary)
            os.replace(temporary, destination)
            owned = sorted(p for p in directory.iterdir()
                           if re.fullmatch(r'risk-\d{4}-\d{2}-\d{2}\.sqlite3', p.name) and p.is_file())
            for old in owned[:-keep]:
                old.unlink()
        return destination
    except OSError as exc:
        raise RiskError('BACKUP_FAILED', '일관된 백업을 완료하지 못했습니다.', 503) from exc
    finally:
        if temporary.exists():
            temporary.unlink()
