import os
import pytest
from cryptography.fernet import Fernet

from app.config import Settings, validate_token_encryption_key


def test_token_encryption_key_required():
    """TOKEN_ENCRYPTION_KEY must be set and valid."""
    # Generate a valid Fernet key
    valid_key = Fernet.generate_key().decode()

    # Create settings with valid key
    os.environ["TOKEN_ENCRYPTION_KEY"] = valid_key
    os.environ["DATABASE_URL"] = "postgresql://test"
    os.environ["SECRET_KEY"] = "test-secret"

    settings = Settings()
    # This should not raise
    validate_token_encryption_key(settings)


def test_token_encryption_key_missing_raises():
    """TOKEN_ENCRYPTION_KEY missing should raise ValueError with helpful message."""
    # Clear the env var
    os.environ.pop("TOKEN_ENCRYPTION_KEY", None)
    os.environ["DATABASE_URL"] = "postgresql://test"
    os.environ["SECRET_KEY"] = "test-secret"

    with pytest.raises(
        ValueError, match="TOKEN_ENCRYPTION_KEY is required but not set"
    ):
        settings = Settings()
        validate_token_encryption_key(settings)


def test_token_encryption_key_invalid_raises():
    """Invalid Fernet key should raise ValueError with helpful message."""
    os.environ["TOKEN_ENCRYPTION_KEY"] = "invalid-key"
    os.environ["DATABASE_URL"] = "postgresql://test"
    os.environ["SECRET_KEY"] = "test-secret"

    with pytest.raises(
        ValueError, match="TOKEN_ENCRYPTION_KEY must be a valid Fernet key"
    ):
        settings = Settings()
        validate_token_encryption_key(settings)
