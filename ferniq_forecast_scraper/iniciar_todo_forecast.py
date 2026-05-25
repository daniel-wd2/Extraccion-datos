import sys
from pathlib import Path

# Garantizar que el directorio del script esté en el sys.path para importaciones locales
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extraer_forecast import SESSION_FILE
from guardar_sesion_forecast import main as guardar_sesion_main
def main() -> None:
    if not SESSION_FILE.exists():
        print("No existe sesion_forecast.json. Se abrira el navegador para guardar la sesion.")
        guardar_sesion_main()

    print(
        "Forecast ya no arranca como bot de Telegram independiente. "
        "Ahora forma parte del bot principal."
    )
    print(
        "Usa ferniq_oportunidad_scraper\\iniciar_todo.py o "
        "..\\arrancar_ambos_bots.ps1 y lanza /sacar_forecast desde ese bot."
    )


if __name__ == "__main__":
    main()
