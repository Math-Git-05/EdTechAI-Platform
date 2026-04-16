import os

from dotenv import load_dotenv

load_dotenv()


def _resolve_database_url() -> str:
    db_url = (os.getenv("DATABASE_URL") or "").strip()
    if db_url:
        # Compatibilidad con URLs antiguas.
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        if db_url.startswith("mssql://"):
            db_url = db_url.replace("mssql://", "mssql+pyodbc://", 1)
        return db_url

    # Conexion SQL Server estilo pyodbc usando variables separadas.
    db_server = (os.getenv("DB_SERVER") or "").strip()
    db_name = (os.getenv("DB_NAME") or "").strip()
    if db_server and db_name:
        driver = (os.getenv("DB_DRIVER") or "ODBC Driver 17 for SQL Server").strip()
        driver_enc = driver.replace(" ", "+")
        server_enc = db_server.replace("\\", "%5C")
        trust_server_certificate = (os.getenv("DB_TRUST_SERVER_CERTIFICATE") or "yes").strip()
        trusted_connection = (os.getenv("DB_TRUSTED_CONNECTION") or "yes").strip()
        return (
            f"mssql+pyodbc://@{server_enc}/{db_name}"
            f"?driver={driver_enc}"
            f"&trusted_connection={trusted_connection}"
            f"&TrustServerCertificate={trust_server_certificate}"
        )

    return "sqlite:///edtech.db"


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return default


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-cambiar-en-produccion")
    SECURITY_PASSWORD_SALT = os.getenv("SECURITY_PASSWORD_SALT", "edtech-email-token-salt")

    # DB real por variable de entorno DATABASE_URL.
    SQLALCHEMY_DATABASE_URI = _resolve_database_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    TALLY_WEBHOOK_SECRET = os.getenv("TALLY_WEBHOOK_SECRET", "")
    TALLY_API_KEY = os.getenv("TALLY_API_KEY", "")
    TALLY_FORM_ID = os.getenv("TALLY_FORM_ID", "kdAYAM")
    WTF_CSRF_ENABLED = True

    # Email (SMTP)
    MAIL_SERVER = _env_first("MAIL_SERVER", "SMTP_HOST", default="smtp.gmail.com")
    MAIL_PORT = int(_env_first("MAIL_PORT", "SMTP_PORT", default="587"))
    MAIL_USE_TLS = _env_first("MAIL_USE_TLS", default="true").lower() == "true"
    MAIL_USE_SSL = _env_first("MAIL_USE_SSL", default="false").lower() == "true"
    MAIL_USERNAME = _env_first("MAIL_USERNAME", "SMTP_USER")
    MAIL_PASSWORD = _env_first("MAIL_PASSWORD", "SMTP_PASS")
    MAIL_FROM = _env_first("MAIL_FROM", "SMTP_FROM", default=MAIL_USERNAME or "noreply@edtech.local")
    MAIL_FROM_NOREPLY = _env_first("MAIL_FROM_NOREPLY", "SMTP_FROM_NOREPLY", default=MAIL_FROM)
    MAIL_FROM_SUPPORT = _env_first("MAIL_FROM_SUPPORT", "SMTP_FROM_SUPPORT")
    MAIL_REPLY_TO_SUPPORT = _env_first("MAIL_REPLY_TO_SUPPORT")
    MAIL_BRAND_NAME = _env_first("MAIL_BRAND_NAME", default="EdTech AI")
    AUTH_EMAIL_DEDUP_SECONDS = int(_env_first("AUTH_EMAIL_DEDUP_SECONDS", default="90"))

    # URL base para links de verificacion y recuperacion
    APP_BASE_URL = _env_first("APP_BASE_URL", "PUBLIC_BASE_URL", default="https://edtechai.lat")
    TALLY_SUBMISSIONS_SHEET_URL = os.getenv(
        "TALLY_SUBMISSIONS_SHEET_URL",
        "https://docs.google.com/spreadsheets/d/1BH2w7FbiSbkGV2VjkIydHgYyG9uF5HOlng28Mzm7JHw/edit?usp=sharing",
    )
    SEED_TEST_USERS = os.getenv("SEED_TEST_USERS", "false").strip().lower() == "true"
    ENABLE_STUDENT_DATA_SHADOW_SYNC = (
        os.getenv("ENABLE_STUDENT_DATA_SHADOW_SYNC", "false").strip().lower() == "true"
    )
    ENABLE_FORM_RESPONSE_STORAGE = (
        os.getenv("ENABLE_FORM_RESPONSE_STORAGE", "false").strip().lower() == "true"
    )
    DROP_DEPRECATED_TABLES = os.getenv("DROP_DEPRECATED_TABLES", "false").strip().lower() == "true"


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "default": DevelopmentConfig,
}
