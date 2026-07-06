# Despliegue de la Azure Function

Infraestructura como código (Bicep) + scripts para desplegar la Function con los
**permisos mínimos** que necesita cada mecanismo. Diseñado para que solo tengas
que hacer `az login` y ejecutar un script.

> **Estado actual:** la Function **no está desplegada** en ningún Azure. No se
> pudo desplegar automáticamente porque el despliegue requiere tu identidad de
> Azure (`az login`, suscripción, MFA) y, para el Mecanismo B, consentimiento de
> Global Admin. Todo lo demás está preparado aquí.

## Contenido

| Archivo | Qué hace |
|---------|----------|
| `main.bicep` | Storage + Function App (Linux, Python 3.11) + Managed Identity + App Settings |
| `main.parameters.json` | Parámetros a rellenar (nombres, suscripción, RG de `agent-verse-resource`, endpoint, mapa de agentes) |
| `deploy.ps1` | Despliega el Bicep, asigna roles A y C, y publica el código |
| `grant-graph-permission.ps1` | Otorga el permiso de Graph del Mecanismo B (necesita Global Admin) |

## Permisos que se conceden (mínimo privilegio)

| Mecanismo | Permiso | Ámbito | Lo concede |
|-----------|---------|--------|------------|
| A – Foundry | `Azure AI Developer` | `agent-verse-resource` | `deploy.ps1` |
| C – Etiqueta ARM | `Tag Contributor` | `agent-verse-resource` | `deploy.ps1` |
| B – Graph | `Application.ReadWrite.All` | Todo el tenant (Graph) | `grant-graph-permission.ps1` (**Global Admin**) |

## Prerrequisitos

- `az login` con permisos para crear recursos y asignar roles.
- Azure Functions Core Tools v4 (`func`).
- Editar `deploy/main.parameters.json` con tus valores reales.

## Pasos

```powershell
az login
# 1) Edita deploy/main.parameters.json (nombres únicos, suscripción, RG, endpoint, agentTargetMap)

# 2) Despliega infra + roles A/C + publica el código
./deploy/deploy.ps1 -ResourceGroup rg-block-agent -Location westeurope

# 3) (Opcional pero necesario para el Mecanismo B) Global Admin:
./deploy/grant-graph-permission.ps1 -PrincipalId <objectId-que-imprime-el-paso-2>
```

Al terminar, comprueba salud:

```
GET https://<functionApp>.azurewebsites.net/api/health
```

## Conectar el disparador de presupuesto

1. Crea un **Action Group** con acción **Webhook** →
   `https://<functionApp>.azurewebsites.net/api/budget-alert?code=<clave>`, con el
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
