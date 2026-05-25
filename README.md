# Extraccion-datos

Este repo contiene dos scrapers:

- `ferniq_oportunidad_scraper`
- `ferniq_forecast_scraper`

Ambos forman parte del mismo bot de Telegram, asi que ahora se arrancan como un solo proceso para evitar conflictos de `getUpdates`.

## Arranque recomendado

Desde la raiz del repo:

```powershell
.\arrancar_ambos_bots.ps1
```

O en Windows:

```bat
arrancar_ambos_bots.bat
```

Ese lanzador abre una sola ventana y ejecuta:

- `ferniq_oportunidad_scraper\iniciar_todo.py`

## Comandos del bot

- `/sacar_datos`
- `/ultimo_excel`
- `/sacar_forecast`
- `/ultimo_forecast`
- `/estado`
- `/ayuda`

## Credenciales y sesiones

Cada carpeta sigue usando su propio `.env` y sus propios archivos de sesion para el scraping:

- `ferniq_oportunidad_scraper` mantiene su sesion de Etapa
- `ferniq_forecast_scraper` mantiene su sesion de Forecast

Lo que ya no se arranca por separado es Telegram para Forecast.

## Forecast antiguo

`ferniq_forecast_scraper\iniciar_todo_forecast.py` queda solo como ayuda para preparar la sesion de Forecast y recordar que el bot real ya esta unificado.

## Comprobacion sin arrancar

```powershell
.\arrancar_ambos_bots.ps1 -DryRun
```
