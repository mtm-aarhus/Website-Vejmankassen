# VejmanKassen
![Vejmankassen](Website.PNG)
VejmanKassen is a web-based application hosted on IIS for managing and updating invoice records. It uses Flask as the backend framework and is deployed on the domain `vejmankassen.adm.aarhuskommune.dk`. This guide provides step-by-step instructions for setting up the project and configuring IIS with Waitress as an example of how you can host a python web application. 

---

## Requirements

The project depends on the following Python packages:

```plaintext
flask==3.1.0
pandas==2.2.3
pymssql==2.3.2
sqlalchemy==2.0.36
waitress==3.0.2
flask-login==0.6.3
```

Make sure these are installed in a Python virtual environment before deploying the application.

---

## Prerequisites

1. **Install HTTP Platform Handler**  
   Download and install [HTTP Platform Handler](https://www.iis.net/downloads/microsoft/httpplatformhandler) for IIS. This is required to host Python applications.

2. **ODBC Driver**  
   Ensure an appropriate [ODBC driver](https://go.microsoft.com/fwlink/?linkid=2266337) is installed on the server to allow database connectivity.

3. **Enable IIS and Server Manager**  
   Activate IIS and related features in Windows Features or Server Manager.

4. **Python Installation**  
   Grant the following permissions to Python's installation directory and the VejmanKassen project folder:
   - **Users**: `IIS_IUSRS` and `AppPool\VejmanKassen` or `IIS AppPool\VejmanKassen`
   - **Access Level**: Full Control

---

## Setup Instructions

### 1. Create a Virtual Environment

1. Open a terminal or PowerShell.
2. Navigate to the folder where you want the virtual environment to reside.
3. Create the virtual environment:
   ```shell
   python -m venv .venv
   ```
4. Activate the virtual environment:
   ```shell
   .venv\Scripts\activate
   ```
5. Install the dependencies:
   ```shell
   pip install -r requirements.txt
   ```

### 2. Configure IIS

1. Open **IIS Manager**.
2. Locate your site (e.g., `VejmanKassen`) in the left-hand tree view.

#### 2.1 Configure Environment Variables
1. Select your site and click **Configuration Editor** in the middle panel.
2. Navigate to `system.webServer/httpPlatform` and expand `environmentVariables`.
3. Add the following:
   - `VejmanKassenSQL`: Connection string for your SQL database, including username and password.

#### 2.2 Enable Forwarding of Windows Authentication Tokens
1. Still under `system.webServer/httpPlatform`, set:
   - `forwardWindowsAuthToken`: `True`

---

## Deployment with Waitress

Waitress serves as the WSGI server for the Flask application. Make sure you add a wsgi.py file to the project folder if it isn't created already, with the following line:
```python
from app import app as application
```
Create a `web.config` file in the project directory. An example `web.config` might look like this:

```xml
<configuration>
  <system.webServer>
    <handlers>
      <add name="httpPlatformHandler" path="*" verb="*" modules="httpPlatformHandler" resourceType="Unspecified" />
    </handlers>
    <httpPlatform 
    processPath="C:\PathTo\VejmanKassen\.venv\Scripts\python.exe" 
    arguments="-m waitress --listen=localhost:%HTTP_PLATFORM_PORT% wsgi:application" 
    startupTimeLimit="60" 
    stdoutLogEnabled="true" 
    stdoutLogFile="C:\PathTo\VejmanKassen\log\python-app.log" 
    processesPerApplication="1" forwardWindowsAuthToken="true">
      <environmentVariables>
        <environmentVariable name="PYTHONUNBUFFERED" value="1" />
        <environmentVariable name="PYTHONPATH" value="C:\PathTo\VejmanKassen" />
        <environmentVariable name="VejmanKassenSQL" value="mssql+pymssql://DOMAIN\Username:Password@ServerName/Database" />
      </environmentVariables>
    </httpPlatform>
  </system.webServer>
    <system.web>
        <identity impersonate="false" />
    </system.web>
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

---

## License

This project is maintained by Aarhus Kommune, and published to github for inspiration and version control. 