# IBM i (AS/400) Driver

## Why Java?

IBM does not release a free Python driver for IBM i:

- **ibm_db** uses DRDA and needs a DB2 Connect license (it is the **AS/400 (DB2)** type)
- **pyodbc** needs IBM i Access Client Solutions, downloadable only with an IBM account
- **JT400 (JTOpen)** is the open-source JDBC driver used by Java applications: free, and it works

ErrorEngine uses JT400 through JPype, a bridge between Python and Java: it is the **IBM i (AS/400)** type.

## Requirements

1. **Java 8 or later**, with `JAVA_HOME` set
2. **JPype1 1.5.1**: installed by `requirements.txt` (newer versions have compatibility issues)
3. **jt400.jar** in the `lib/` folder

## Setup

### 1. Install Java

Download it from https://adoptium.net/temurin/releases/ and check:

```bash
java -version
```

### 2. Set JAVA_HOME

**Windows**, in PowerShell as administrator:
```powershell
$javaPath = (Get-Command java).Source | Split-Path | Split-Path
[System.Environment]::SetEnvironmentVariable("JAVA_HOME", $javaPath, "Machine")
```
Then open a new terminal (and restart the ErrorEngine service, if it runs as one).

**Linux/macOS**, in `.bashrc` or `.zshrc`:
```bash
export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))))
```

### 3. Download JT400

From Maven Central, no account needed:
```
https://repo1.maven.org/maven2/net/sf/jt400/jt400/20.0.7/jt400-20.0.7.jar
```

Rename it `jt400.jar` and put it in `lib/`:
```
ErrorEngine/
├── lib/
│   └── jt400.jar
├── serve.py
└── ...
```

### 4. Create the connection

**DB connections** → **New connection**:
- Type: **IBM i (AS/400)**
- Host: IP address or host name
- Port: leave it empty, JT400 finds the ports by itself
- Database: the library, or several separated by commas (e.g. `MYLIB`)
- Username and password of the IBM i user

### 5. Check from the command line (optional)

`scripts/check_ibmi.py` tests the connection with the same driver. It reads `IBMI_HOST`, `IBMI_USER`, `IBMI_PASSWORD` and `IBMI_LIBRARY` from the environment, and asks for what is missing:

```bash
venv/bin/python scripts/check_ibmi.py      # Windows: venv\Scripts\python.exe scripts\check_ibmi.py
```

## Troubleshooting

### "jt400.jar not found"

The file must be exactly `lib/jt400.jar`.

### "No module named 'jpype'" or a crash at startup

JPype is missing or is the wrong version, or `JAVA_HOME` is not set. Reinstall it with `pip install JPype1==1.5.1` in the virtual environment and check `JAVA_HOME`.

### Connection refused or timeout

JT400 uses the IBM i host servers: ports 449 (port mapper), 8470, 8471 (database) and 8476 (sign-on), or 9470, 9471 and 9476 with SSL. They must be reachable from the ErrorEngine server.

### Authentication errors

- Check username and password
- The user needs authority on the library
- Try the same user from another application
