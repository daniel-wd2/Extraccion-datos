import sys
from pathlib import Path

# Garantizar que el directorio del script esté en el sys.path para importaciones locales
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extraer_forecast import SESSION_FILE
from guardar_sesion_forecast import main as guardar_sesion_main
from telegram_forecast_bot import main as telegram_bot_main


def main() -> None:
    if not SESSION_FILE.exists():
        print("No existe sesion_forecast.json. Se abrira el navegador para guardar la sesion.")
        guardar_sesion_main()

    print("Iniciando bot de Telegram de Forecast...")
    telegram_bot_main()


if __name__ == "__main__":
    main()
