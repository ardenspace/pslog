"""Fernet 마스터 키 기반 secret 암복호화.

설계서: 2026-04-26-ai-task-automation-design.md §9
- 마스터 키: PSLOG_FERNET_KEY 환경변수 (32-byte url-safe base64)
- 프로젝트별 webhook secret을 이 마스터 키로 암호화하여 Project.webhook_secret_encrypted 에 저장
- 키 회전 절차는 별도 운영 문서
"""

import secrets

from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    """매번 새 Fernet 인스턴스 — settings 변경(테스트 reload) 반영."""
    return Fernet(settings.pslog_fernet_key.encode())


def encrypt_secret(plaintext: str) -> bytes:
    """평문 secret → Fernet 암호문 bytes."""
    return _fernet().encrypt(plaintext.encode())


def decrypt_secret(token: bytes) -> str:
    """Fernet 암호문 → 평문 string. 잘못된 키/변조 시 InvalidToken raise."""
    return _fernet().decrypt(token).decode()


def generate_webhook_secret() -> str:
    """프로젝트별 webhook secret 생성 — 32-byte 랜덤 url-safe base64.

    GitHub webhook secret 권장 길이(>=20 bytes) 충족.
    """
    return secrets.token_urlsafe(32)
