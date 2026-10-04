"""Variables d'environnement minimales, définies AVANT l'import de l'application."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test")
os.environ.setdefault("SECRET_KEY", "s" * 48)
os.environ.setdefault("FIELD_ENCRYPTION_KEY", "A" * 43 + "=")
