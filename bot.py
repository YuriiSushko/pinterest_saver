import logging
from config import load_config
from telegram_bot import build_app

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

def main() -> None:
    token = load_config()
    app = build_app(token)
    app.run_polling()

if __name__ == "__main__":
    main()
