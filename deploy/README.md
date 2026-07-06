# Despliegue de la Azure Function

Infraestructura como código (Bicep) + scripts para desplegar la Function con los
**permisos mínimos** que necesita cada mecanismo. Diseñado para que solo tengas
que hacer `az login` y ejecutar un script.

> **Estado actual: ✅ YA DESPLEGADA en tu suscripción.** La infraestructura y el
> código están desplegados y funcionando (ver "Lo que ya está desplegado" más
> abajo). Solo queda pendiente el **Mecanismo B (Graph)**, que necesita
> consentimiento de un **Global Admin** (`grant-graph-permission.ps1`). Esta guía
> sirve tanto para reproducir el despliegue desde cero como para operar el que ya
> existe.

## Lo que ya está desplegado

| Recurso | Valor |
|---------|-------|
| Grupo de recursos | `rg-block-agent` (swedencentral) |
| Plan | **Flex Consumption (FC1)** — no Consumo clásico (ver nota del tenant) |
| Function App | `fa-block-agent-jykza1` |
| Endpoint salud | `https://fa-block-agent-jykza1.azurewebsites.net/api/health` |
| Endpoint alerta | `https://fa-block-agent-jykza1.azurewebsites.net/api/budget-alert?code=<clave>` |
| Storage | `stblkagentjykza1` (sin clave compartida, acceso por identidad) |
| Identidad administrada (objectId) | `c22a5fbe-a0b6-41a4-965a-8b7ea16bbd2f` |
| Roles concedidos | `Azure AI Developer` + `Tag Contributor` en `agent-verse-resource`; `Storage Blob Data Owner` + `Storage Queue Data Contributor` en el storage |
| Funciones activas | `budget_alert` (POST, auth FUNCTION) y `health` (GET, anónima) |

### Resultados de pruebas en vivo ya realizadas
- **D1 (salud):** `200 OK` → `{"status":"ok","mechanisms":["foundry","graph","tag"]}`.
- **D8 (errores):** `422` sin agente y `400` con mecanismo inválido. ✔
- **D2/D3 (Mecanismo C – etiqueta ARM):** bloqueo puso `MS-AOAI-Feature-Assistants=Disabled` (estado previo `Enabled`) y el desbloqueo lo revirtió a `Enabled`. Reversible y no destructivo confirmado. ✔
- **Mecanismo A (Foundry):** no probado en vivo porque el proyecto `agent-verse-project` aún no tiene agentes (`agentTargetMap` está vacío).
- **Mecanismo B (Graph):** pendiente de consentimiento de Global Admin.

> **⚠️ Nota de política del tenant (importante):** tu tenant **prohíbe la
> autenticación por clave compartida en Storage** y **deshabilita el basic auth de
> SCM**. Por eso el plan de **Consumo clásico (Y1) no funciona** (su content share
> necesita claves). La solución desplegada usa **Flex Consumption** con storage por
> **identidad administrada** (`AzureWebJobsStorage__accountName` +
> `__credential=managedidentity`) y `allowSharedKeyAccess:false`.

## Contenido

| Archivo | Qué hace |
|---------|----------|
| `main.bicep` | Storage (sin clave, por identidad) + Function App **Flex Consumption** (Python 3.11) + Managed Identity + roles de storage + App Settings |
| `main.parameters.json` | Parámetros ya rellenos con tus valores reales (suscripción, RG de `agent-verse-resource`, endpoint del proyecto, `agentTargetMap`) |
| `deploy.ps1` | Despliega el Bicep, asigna roles A y C, y publica el código (`func ... --python`) |
| `grant-graph-permission.ps1` | Otorga el permiso de Graph del Mecanismo B (necesita Global Admin) |

## Permisos que se conceden (mínimo privilegio)

| Mecanismo | Permiso | Ámbito | Lo concede |
|-----------|---------|--------|------------|
| A – Foundry | `Azure AI Developer` | `agent-verse-resource` | `deploy.ps1` |
| C – Etiqueta ARM | `Tag Contributor` | `agent-verse-resource` | `deploy.ps1` |
| B – Graph | `Application.ReadWrite.All` | Todo el tenant (Graph) | `grant-graph-permission.ps1` (**Global Admin**) |

## Prerrequisitos

- `az login` con permisos para crear recursos y asignar roles.
- Azure Functions Core Tools v4 (`func`) y Bicep (`az bicep`).
- `deploy/main.parameters.json` ya está relleno con tus valores reales.

## Pasos

> Estos pasos **ya se han ejecutado** contra tu suscripción. Repítelos solo si
> quieres recrear la infraestructura desde cero (p. ej. en otro RG/suscripción).

```powershell
az login

# 1) main.parameters.json ya está relleno (suscripción, RG, endpoint, agentTargetMap).

# 2) Despliega infra (Flex Consumption) + roles A/C + publica el código.
#    El publish usa 'func azure functionapp publish <app> --python' (obligatorio el
#    flag --python en Flex sin local.settings.json).
./deploy/deploy.ps1 -ResourceGroup rg-block-agent -Location swedencentral

# 3) (Pendiente — necesario para el Mecanismo B) Global Admin:
./deploy/grant-graph-permission.ps1 -PrincipalId c22a5fbe-a0b6-41a4-965a-8b7ea16bbd2f
```

Comprueba salud (ya responde OK):

```
GET https://fa-block-agent-jykza1.azurewebsites.net/api/health
```

## Conectar el disparador de presupuesto

1. Crea un **Action Group** con acción **Webhook** →
   `https://fa-block-agent-jykza1.azurewebsites.net/api/budget-alert?code=<clave>`
   (obtén la clave con `az functionapp keys list --name fa-block-agent-jykza1 --resource-group rg-block-agent --query "functionKeys.default" -o tsv`), con el
   **esquema de alerta común** activado.
2. Crea un **presupuesto** en Cost Management sobre `agent-verse-resource` (o su
   RG). Nómbralo `budget-<agentId>` para transportar el id del agente.
3. Añade el Action Group a las condiciones del presupuesto (p. ej. 90% / 100%).

---

## Cómo se lleva a cabo cada prueba sobre la Function desplegada

Estas son las comprobaciones que se ejecutan **una vez desplegada** (además de la
suite offline de `TESTING.md`, que no necesita Azure). Cada prueba describe la
acción y **qué se verifica**, no solo el comando.

### Prueba D1 — Salud del servicio
- **Acción:** `GET /api/health`.
- **Se verifica:** responde `200` con `{"status":"ok","mechanisms":["foundry","graph","tag"]}`. Confirma que el host arrancó y cargó los tres mecanismos.

### Prueba D2 — Bloqueo real (los 3 mecanismos)
- **Acción:** `POST /api/budget-alert` con `samples/simplified_block.json` (`mechanism:"all"`).
- **Se verifica en el portal:**
  - Foundry: el agente tiene `metadata.blocked=true`.
  - Entra: el service principal queda con "Habilitado para inicio de sesión = No" (`accountEnabled=false`).
  - Recurso: `agent-verse-resource` tiene la etiqueta `MS-AOAI-Feature-Assistants=Disabled`.
  - Respuesta HTTP `200` y `allSucceeded=true`.

### Prueba D3 — Desbloqueo (reversibilidad)
- **Acción:** `POST /api/budget-alert` con `samples/simplified_unblock.json`.
- **Se verifica:** los tres valores vuelven al estado previo (`blocked=false`, `accountEnabled=true`, etiqueta `Enabled`). Nada se ha borrado.

### Prueba D4 — Mecanismo aislado
- **Acción:** `POST /api/budget-alert` con `{"agentId":"...","mechanism":"graph","action":"block"}`.
- **Se verifica:** en la respuesta `mechanisms:["graph"]` y solo cambia el service principal; Foundry y la etiqueta no se tocan.

### Prueba D5 — Permisos (mínimo privilegio)
- **Acción:** lanzar D2 justo **después** del paso 2 pero **antes** del paso 3 (sin el permiso de Graph).
- **Se verifica:** respuesta `207` con `allSucceeded=false`; `foundry` y `tag` en `success=true`, y `graph` en `success=false` con un `detail` de autorización. Confirma que cada mecanismo depende de su propio rol.

### Prueba D6 — Formato de alerta real
- **Acción:** `POST /api/budget-alert` con `samples/common_alert.json` (Common Alert Schema).
- **Se verifica:** el agente se resuelve desde `alertContext.AgentId` / nombre de presupuesto y el bloqueo se aplica igual que en D2.

### Prueba D7 — Trigger end-to-end
- **Acción:** forzar el presupuesto (o bajar temporalmente el umbral) para que Cost Management dispare el Action Group.
- **Se verifica:** sin intervención manual, el agente aparece bloqueado en el portal minutos después; en los logs de la Function (App Insights) se ve la invocación de `budget-alert`.

### Prueba D8 — Casos de error
- **Acción:** enviar `{}` (sin agente) y un `mechanism` inexistente.
- **Se verifica:** `422` (no se pudo determinar el agente) y `400` (mecanismo inválido con la lista de válidos), respectivamente.
