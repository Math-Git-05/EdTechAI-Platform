import os

from app import create_app


config_name = os.getenv("FLASK_CONFIG", "production").strip().lower() or "production"
app = create_app(config_name)


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}
    port = int(os.getenv("PORT", "5000"))
    app.run(debug=debug, host="0.0.0.0", port=port)
