# Guía de pruebas (cómo testear cada mecanismo)

Esta guía explica, paso a paso, cómo probar el POC de "bloquear agente cuando se
supera el presupuesto" y comprobar que **cada uno de los tres mecanismos**
funciona y es **reversible**. Hay tres niveles de prueba, de menos a más
esfuerzo:

1. **Prueba offline** — sin Azure, en segundos. Valida toda la lógica.
2. **Prueba local con el host de Functions** — llamadas HTTP reales con `curl`.
3. **Prueba end-to-end en Azure** — con recursos reales y una alerta de
   presupuesto de verdad.

> Recordatorio de la evaluación de viabilidad: el botón "Bloquear" del Centro de
> Administración de M365 **no tiene API pública**, así que probamos el
> equivalente automatizado. Además, los presupuestos de Azure **no** se pueden
> acotar a un único agente dentro de la cuenta de Foundry (solo a
> recurso/grupo de recursos/etiqueta).

---

## 0. Requisitos previos

```powershell
# Desde la raíz del repo
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Para los niveles 2 y 3 necesitas además:

- **Azure Functions Core Tools v4** (`func`) — para ejecutar el host local.
- **Azure CLI** (`az`) — para crear/gestionar recursos y probar permisos.

---

## 1. Prueba offline (recomendada para empezar)

No toca Azure: simula en memoria los tres planos (Foundry, Graph y ARM) y
reproduce los `payloads` de ejemplo por el parser y el dispatcher reales.
Verifica que un bloqueo se aplica y que el **desbloqueo restaura el estado
previo**.

```powershell
.venv\Scripts\python.exe -m tests.test_harness
```

Salida esperada:

```
test_block_then_unblock_all_mechanisms ... ok
test_parse_common_alert ... ok
test_single_mechanism_selection ... ok
----------------------------------------------------------------------
Ran 3 tests in 0.0XXs
OK
```

Qué demuestra cada test:

| Test | Qué valida |
|------|------------|
| `test_parse_common_alert` | Se parsea correctamente una alerta real (Common Alert Schema): `agentId`, gasto, presupuesto y acción. |
| `test_block_then_unblock_all_mechanisms` | Los 3 mecanismos **bloquean** (estado nativo de Foundry, `accountEnabled=false`, etiqueta `Disabled`) y luego el **unblock** restaura cada estado previo. |
| `test_single_mechanism_selection` | Se puede ejecutar un único mecanismo (p. ej. solo `graph`). |

### Probar un solo mecanismo desde la terminal (offline)

```powershell
.venv\Scripts\python.exe -c "import tests.test_harness as t; import unittest; unittest.main(module=t, argv=['x','BlockAgentHarness.test_single_mechanism_selection'], exit=False)"
```

---

## 2. Prueba local con el host de Azure Functions

Ejecuta la Function de verdad y le mandas `payloads` con `curl`. Puedes hacerlo
de dos formas según quieras (o no) tocar Azure.

### 2.1 Preparar configuración

```powershell
copy local.settings.json.example local.settings.json
```

Edita `local.settings.json`:

- Para **solo probar el flujo HTTP + parsing + dispatch** sin credenciales
  reales, no hace falta rellenar nada más (las llamadas a Azure fallarán de
  forma controlada y verás el error por mecanismo en la respuesta).
- Para **probar contra Azure real** desde tu máquina, rellena
  `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` (app registration
  de desarrollo) y el `AGENT_TARGET_MAP` con ids reales.

### 2.2 Arrancar el host

```powershell
func start
```

Comprueba que vive:

```powershell
curl http://localhost:7071/api/health
# {"status":"ok","mechanisms":["foundry","graph","tag"]}
```

### 2.3 Lanzar un bloqueo

```powershell
curl -X POST http://localhost:7071/api/budget-alert `
  -H "Content-Type: application/json" `
  -d "@samples/simplified_block.json"
```

Respuesta (resumen por mecanismo):

```json
{
  "action": "block",
  "agentId": "asst_demo123",
  "mechanisms": ["foundry", "graph", "tag"],
  "results": [
    { "mechanism": "foundry", "success": true, "reversible": true, "detail": "..." },
    { "mechanism": "graph",   "success": true, "reversible": true, "detail": "..." },
    { "mechanism": "tag",     "success": true, "reversible": true, "detail": "..." }
  ],
  "allSucceeded": true
}
```

### 2.4 Desbloquear (revertir)

```powershell
curl -X POST http://localhost:7071/api/budget-alert `
  -H "Content-Type: application/json" `
  -d "@samples/simplified_unblock.json"
```

### 2.5 Probar mecanismos por separado

Cambia el campo `mechanism` del cuerpo a `foundry`, `graph`, `tag` o `all`:

```powershell
curl -X POST http://localhost:7071/api/budget-alert `
  -H "Content-Type: application/json" `
  -d '{ "agentId": "asst_demo123", "mechanism": "graph", "action": "block" }'
```

### 2.6 Probar el formato de alerta real (Common Alert Schema)

```powershell
curl -X POST http://localhost:7071/api/budget-alert `
  -H "Content-Type: application/json" `
  -d "@samples/common_alert.json"
```

### 2.7 Casos de error que conviene probar

| Caso | Cómo | Resultado esperado |
|------|------|--------------------|
| Sin `agentId` | Envía `{}` | `422` con mensaje de que no se pudo determinar el agente |
| JSON inválido | Envía texto no-JSON | `400` "Request body must be valid JSON" |
| Mecanismo desconocido | `"mechanism": "foo"` | `400` con la lista de mecanismos válidos |
| Fallo parcial | Un mecanismo sin permisos | `207` y `allSucceeded=false`, con el error en ese mecanismo |

---

## 3. Prueba end-to-end en Azure (opcional, la más realista)

### 3.1 Desplegar la Function

```powershell
# Crea el Function App (plan de consumo, runtime Python 3.11) y despliega
az functionapp create --resource-group rg-agents --consumption-plan-location westeurope `
  --runtime python --runtime-version 3.11 --functions-version 4 `
  --name fa-block-agent --storage-account <storageaccount> --os-type Linux
func azure functionapp publish fa-block-agent
```

### 3.2 Identidad y permisos

```powershell
# Managed Identity de sistema
az functionapp identity assign --name fa-block-agent --resource-group rg-agents
```

Los roles de los mecanismos A y C, además de los del storage, **ya los concede el
Bicep** sobre la cuenta Foundry (mínimo privilegio):

- **Mecanismo A (Foundry):** `Azure AI Developer` **+** `Cognitive Services User`
  sobre la cuenta Foundry. `Azure AI Developer` por sí solo **no** cubre el
  data-plane de agentes (`.../agents/*`) → da `403`; por eso hace falta también
  `Cognitive Services User`. Tras asignarlos, el plano de datos tarda 2-5 min.
- **Mecanismo B (Graph):** permiso de aplicación `Application.ReadWrite.All`
  (fuera del Bicep, lo concede `grant-graph-permission.ps1` — necesita Global Admin).
- **Mecanismo C (etiqueta):** `Tag Contributor` sobre la cuenta Foundry.

### 3.3 Configurar los App Settings

Sube las mismas claves de `local.settings.json.example` como *Application
settings* (sin las de `AZURE_CLIENT_SECRET`: en Azure se usa la Managed
Identity).

### 3.4 Conectar la alerta de presupuesto

El **Action Group** y la **alerta métrica** (`TotalTokens` sobre la cuenta Foundry)
**ya los crea el Bicep**, conectados al endpoint `/api/budget-alert`. Solo tienes
que ajustar el umbral (`budgetTokenThreshold`) si quieres otro valor. Si prefieres
un presupuesto de coste real de Cost Management, créalo sobre la cuenta Foundry (o
su RG), nómbralo `budget-<agentId>` y apúntalo al mismo Action Group.

### 3.5 Verificar el resultado en el portal

- **Foundry:** el agente tiene `state=disabled` (bloqueo por estado nativo).
- **Graph/Entra:** el service principal aparece con "Habilitado para que los
  usuarios inicien sesión = No" (`accountEnabled=false`).
- **Etiqueta:** la cuenta Foundry tiene
  `MS-AOAI-Feature-Assistants=Disabled`.

Para revertir, reenvía la alerta con `"action": "unblock"`.

---

## 4. Tabla resumen: qué prueba cada nivel

| Nivel | Toca Azure | Qué comprueba | Comando principal |
|-------|-----------|---------------|-------------------|
| 1. Offline | No | Parsing + dispatch + reversibilidad de los 3 mecanismos | `python -m tests.test_harness` |
| 2. Host local | Opcional | Flujo HTTP real, códigos de estado, selección de mecanismo | `func start` + `curl` |
| 3. Azure E2E | Sí | Bloqueo/desbloqueo real y trigger por presupuesto | `func azure functionapp publish` + alerta |

---

## 5. Solución de problemas

| Síntoma | Causa probable | Solución |
|---------|----------------|----------|
| `ModuleNotFoundError` | venv sin dependencias | `pip install -r requirements.txt` |
| `401/403` en un mecanismo | Faltan permisos de la identidad | Revisa los roles del apartado 3.2 |
| `422 no agent id` | La alerta no lleva el id | Usa `agentId`, `alertContext.AgentId` o nombra el presupuesto `budget-<agentId>` |
| `allSucceeded=false` (`207`) | Un mecanismo falló pero otros no | Mira el campo `results[].detail` de ese mecanismo |
| El agente sigue respondiendo tras el bloqueo Foundry | Falta el rol `Cognitive Services User` (la acción nativa da `403`) o el plano de datos aún no propagó | Asigna `Cognitive Services User` sobre la cuenta Foundry y espera 2-5 min; comprueba `state=disabled` |
