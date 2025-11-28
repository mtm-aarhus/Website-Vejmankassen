# VejmanKassen

Vejmankassen is a web-based application for managing and updating invoice records for road permissions.  
The system is built with **Flask**, uses **SQL Server**, supports **OpenID Connect (via PyOrchestrator)** for authentication, and is designed for production deployment on IIS using **Waitress**.

The application is used by caseworkers and administrative staff for validating, editing and sending fakturalinjer to invoicing, handling conflicts, and generating statistical insights.

---

![Main Screen](screenshots/main%20screen.png)


---

## Requirements

The project depends on the following Python packages:

```plaintext
flask==3.1.2
sqlalchemy==2.0.43
pandas==2.3.2
numpy==2.3.2
pymssql==2.3.7
waitress==3.0.2
requests==2.32.5
PyJWT==2.10.1
flask-wtf==1.2.2
````

Install these inside a Python virtual environment before deploying.

---

## Prerequisites

The original prerequisites are still required:

1. **HTTP Platform Handler for IIS**
   Install from: [https://www.iis.net/downloads/microsoft/httpplatformhandler](https://www.iis.net/downloads/microsoft/httpplatformhandler)
   Required for hosting Python applications.

2. **ODBC Driver for SQL Server**
   Install the recommended version from Microsoft.

3. **Enable IIS & Server Manager**
   Ensure IIS with necessary modules is enabled.

4. **Python Installation (with correct permissions)**
   Ensure the following identities have *Full Control* permissions on both:

   * The Python installation directory
   * The VejmanKassen application folder

   Permissions required:

   * `IIS_IUSRS`
   * `AppPool\VejmanKassen` or `IIS AppPool\VejmanKassen`

---

## 🔐 Authentication (New)

The old IIS-based Windows Authentication has been replaced with:

### **OpenID Connect via PyOrchestrator**

* Users authenticate with a redirect to
  `https://pyorchestrator.aarhuskommune.dk/api/auth/login`
* PyOrchestrator returns a signed JWT token
* Flask reads the user profile from the JWT:

```json
{
  "email": "user@aarhus.dk",
  "name": "User Name",
  "groups": [
    "Vejmankassen-Admin",
    "Vejmankassen-Sagsbehandler"
  ]
}
```

### Supported roles:

* **Vejmankassen-Admin**
* **Vejmankassen-Sagsbehandler**
* **Vejmankassen-BI** (statistics-only)
* Roles control:

  * Edit/delete access
  * "Send to billing"
  * "Fakturer ikke"
  * Resolving conflicts
  * Sync trigger access

---

## 🚀 New Features Since the Previous Version

### ✔ Modernized UI (Bootstrap 5.3)

* Full **dark mode** with automatic system detection
* Improved navbar
* Mobile-friendly layout
* Cleaner modal editing interface
* Real-time row highlighting for destructive actions

### New Pages

#### **1. Ikke faktureret**

* Edit modal with:

  * Live calculation of days & price
  * Inline validation
  * Send til fakturering
  * Fakturer ikke
  * Dynamic totals
* Supports roles & permissions

![Edit Screen](screenshots/edit%20screen.png)

#### **2. Til fakturering**

* Undo sending for processing
* Server-side pagination, search & sorting

#### **3. Faktureret**

* Option to “Fakturer igen” (moves row to *FakturerIkke*)
* Helps correct cases that require reinvoicing

#### **4. Konflikter (New!)**

* Lists all conflicts returned from the mobility workspace / henstillinger engine
* Filters:

  * “Mine konflikter”
  * Status: Open, AutoResolved, UserAccepted
* Actions:

  * Accept conflict
  * Mark as unresolved
* Auto-detection of conflicts solvable in Vejman vs solvable in Vejmankassen

#### **5. Statistik (New!)**

![Statistics](screenshots/statistics.png)

A full metrics dashboard with:

* Status filters
* Date filters (start/slut)
* Type filters (dynamic)
* Live KPIs:

  * Total price
  * Number of invoice lines
  * Average meter / price / days
* Per-type doughnut charts (Bootstrap-themed)
* Server-side table with all filtered rows
* **Full CSV export of entire dataset** (ignores filters)

### ✔ Mobility Workspace / Henstillinger Support

The application now integrates with the new mobility workspace system to:

* Detect conflicts
* Resolve conflicts
* Display affected fakturalinjer
* Provide better guidance for caseworkers

### ✔ Sync Trigger (New)

Admins and caseworkers can trigger synchronization with:

```
/reset_trigger
```

Protected by:

* 5-minute cooldown
* Role checks
* Status indicator in navbar
* "Last sync" timestamp shown globally

---

## Setup Instructions

### 1. Create a Virtual Environment

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## IIS Configuration

1. Open **IIS Manager**.
2. Locate your site (e.g., `VejmanKassen`) in the left-hand tree view.

#### Enable Forwarding of Windows Authentication Tokens
1. Still under `system.webServer/httpPlatform`, set:
   - `forwardWindowsAuthToken`: `True`

---

### 2.1 Environment Variables

Configure in:

```
system.webServer/httpPlatform/environmentVariables
```

Required variables:

* `VejmanKassenSQL` – connection string to SQL Server
* `FLASK_SECRET_KEY` – session secret
* `PYORCHESTRATOR_JWT_SECRET` – for validating JWT login tokens
* `PyOrchestratorAPIKey` – for triggering sync jobs

### 2.2 Enabling JWT Login

Since login is no longer based on IIS Windows Authentication, you must allow all anonymous traffic in IIS — authentication is handled by OpenIDConnect 2.0

Set:

* **Anonymous Authentication: Enabled**
* **Windows Authentication: Disabled**

JWT validation is performed by Flask using the shared secret.

---

## Deployment With Waitress

The Waitress configuration remains unchanged from the original README.

Example `wsgi.py`:

```python
from app import app as application
```

Example `web.config`:

```xml
<configuration>
  <system.webServer>
    <handlers>
      <add name="httpPlatformHandler"
           path="*"
           verb="*"
           modules="httpPlatformHandler"
           resourceType="Unspecified" />
    </handlers>

    <httpPlatform
      processPath="C:\PathTo\VejmanKassen\.venv\Scripts\python.exe"
      arguments="-m waitress --listen=localhost:%HTTP_PLATFORM_PORT% wsgi:application"
      startupTimeLimit="60"
      stdoutLogEnabled="true"
      stdoutLogFile="C:\PathTo\VejmanKassen\log\python-app.log"
      processesPerApplication="1"
      forwardWindowsAuthToken="false">

      <environmentVariables>
        <environmentVariable name="PYTHONUNBUFFERED" value="1" />
        <environmentVariable name="PYTHONPATH" value="C:\PathTo\VejmanKassen" />
        <environmentVariable name="VejmanKassenSQL" value="…" />
        <environmentVariable name="FLASK_SECRET_KEY" value="…" />
        <environmentVariable name="PYORCHESTRATOR_JWT_SECRET" value="…" />
        <environmentVariable name="PyOrchestratorAPIKey" value="…" />
      </environmentVariables>

    </httpPlatform>
  </system.webServer>
</configuration>
```

---

## Troubleshooting Tips

1. **Permissions Issues**  
   Ensure `IIS_IUSRS` and `AppPool\VejmanKassen` or `IIS AppPool\VejmanKassen` have full access to both the Python installation directory and the project folder.

2. **ODBC Driver Errors**  
   Verify the correct ODBC driver is installed and accessible by the application.

3. **Environment Variables**  
   Double-check that the `VejmanKassenSQL` environment variable is properly set in IIS.

Additional new considerations:

### JWT Issues

If login fails:

* Check that PyOrchestrator returns the `jwt` query parameter.
* Validate that `PYORCHESTRATOR_JWT_SECRET` matches the orchestrator’s signing key.
* Verify that system time is synchronized (tokens have leeway but require reasonable clock accuracy).

### Sync Trigger Issues

Look for:

* Missing `PyOrchestratorAPIKey`
* Cooldown preventing repeated runs
* External orchestrator errors returned in response

---

## License

This project is maintained by ***Aarhus Kommune*** and published to GitHub for transparency and inspiration under the **GPL-3.0 license**.

```
