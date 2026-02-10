# IBM i (AS/400) Driver

## Why Java?

IBM has never released a native Python driver for IBM i (AS/400).

Available alternatives:
- **ibm_db** — Uses DRDA/CLI protocol, requires paid IBM licenses
- **pyodbc** — Requires IBM i Access Client, not downloadable without enterprise IBM account
- **JT400 (JTOpen)** — Open source JDBC driver, free, works

JT400 is the same driver used by enterprise Java applications. The only way to use it from Python is through JPype, a Java-Python bridge.

## Requirements

1. **Java 8+** installed
2. **JPype1 1.5.1** (newer versions have compatibility issues)
3. **jt400.jar** in the `lib/` folder

## Setup

### 1. Install Java

Download from: https://adoptium.net/temurin/releases/

Verify installation:
```bash
java -version
```

### 2. Configure JAVA_HOME

JPype requires the `JAVA_HOME` environment variable.

**Windows** — Open PowerShell as administrator:
```powershell
# Find Java location
$javaPath = (Get-Command java).Source | Split-Path | Split-Path
# Set permanently
[System.Environment]::SetEnvironmentVariable("JAVA_HOME", $javaPath, "Machine")
```
Close and reopen the terminal.

**Linux/macOS**:
```bash
# Add to your .bashrc or .zshrc
export JAVA_HOME=$(dirname $(dirname $(readlink -f $(which java))))
```

Verify:
```bash
echo $JAVA_HOME   # Linux/macOS
echo %JAVA_HOME%  # Windows
```

### 3. Install JPype

**Important**: Use exactly version 1.5.1. Newer versions have compatibility issues.

```bash
pip install JPype1==1.5.1
```

### 4. Download JT400

Download from Maven Central (no account required):
```
https://repo1.maven.org/maven2/net/sf/jt400/jt400/20.0.7/jt400-20.0.7.jar
```

Rename to `jt400.jar` and place in the project's `lib/` folder:
```
ErrorEngine/
├── lib/
│   └── jt400.jar
├── app.py
└── ...
```

### 5. Enable in requirements.txt

Uncomment the line:
```txt
JPype1==1.5.1
```

### 6. Create the Connection

In the web UI:
- Type: **IBM i (AS/400)**
- Host: IP address or hostname
- Database: library name (e.g., `MYLIB`)
- Username and password: AS/400 credentials

## Troubleshooting

### "jt400.jar not found"

Verify the file is exactly at `lib/jt400.jar`.

### Silent crash on startup

Wrong JPype version. Uninstall and reinstall:
```bash
pip uninstall JPype1
pip install JPype1==1.5.1
```

### "No module named 'jpype'"

JPype not installed or JAVA_HOME not configured. Check both.

### "Connection refused"

- Verify the AS/400 is reachable: `ping 192.168.x.x`
- Check port 446 is open: `telnet 192.168.x.x 446`

### Authentication errors

- Verify username/password
- User must have permissions on the specified library
- Try connecting with the same user from another application

### Encoding errors or garbled characters

Add the `ccsid=1208` parameter to the JDBC URL if needed.
