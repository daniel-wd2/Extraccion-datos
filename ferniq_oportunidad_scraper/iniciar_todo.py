from extraer_etapa import SESSION_FILE
from telegram_bot import main as telegram_bot_main


def main() -> None:
    if not SESSION_FILE.exists():
        print(
            "No existe sesion_ferniq.json. El bot arrancara igualmente y "
            "regenerara la sesion cuando haga falta."
        )

    print("Iniciando bot de Telegram...")
    telegram_bot_main()


if __name__ == "__main__":
    main()
