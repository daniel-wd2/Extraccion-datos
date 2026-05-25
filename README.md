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

- `ferniq_oportunidad_scraper` mantiene su sesion de Etapa y el token del bot real
- `ferniq_forecast_scraper` mantiene solo su sesion y credenciales de Forecast

Lo que ya no se arranca por separado es Telegram para Forecast.

## Configuracion recomendada

`ferniq_oportunidad_scraper\.env`

```env
FERNIQ_EMAIL=
FERNIQ_PASSWORD=
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_ID=
FERNIQ_STAGE_MAX_WORKERS=3
USE_SYSTEM_CHROME_PROFILE=
CHROME_PROFILE_DIRECTORY=
CHROME_USER_DATA_DIR=
CHROME_EXECUTABLE=
```

`ferniq_forecast_scraper\.env`

```env
FERNIQ_EMAIL=
FERNIQ_PASSWORD=
FERNIQ_FORECAST_MAX_WORKERS=3
USE_SYSTEM_CHROME_PROFILE=
CHROME_PROFILE_DIRECTORY=
CHROME_USER_DATA_DIR=
CHROME_EXECUTABLE=
```

En otras palabras: el token de Telegram vive solo en `ferniq_oportunidad_scraper`, porque `forecast` ya es una opcion mas del bot principal.

`FERNIQ_STAGE_MAX_WORKERS` y `FERNIQ_FORECAST_MAX_WORKERS` controlan cuantas extracciones en paralelo se lanzan.
El valor recomendado para empezar es `3`. Si la web responde bien, puedes probar `4`. Si ves inestabilidad, baja a `2`.

## Forecast antiguo

`ferniq_forecast_scraper\iniciar_todo_forecast.py` queda solo como ayuda para preparar la sesion de Forecast y recordar que el bot real ya esta unificado.

## Comprobacion sin arrancar

```powershell
.\arrancar_ambos_bots.ps1 -DryRun
```
