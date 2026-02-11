import jpype
import jpype.imports

# Avvia JVM
jpype.startJVM(classpath=["lib/jt400.jar"])
print("JVM avviata!")

# ORA puoi importare da Java
from java.sql import DriverManager

url = "jdbc:as400://192.168.164.9;libraries=SIGEP_GCCD;naming=sql"
user = "SIGEP_GCCD"
password = "SIGEP"

print("Connessione...")
conn = DriverManager.getConnection(url, user, password)
print("CONNESSO!")

stmt = conn.createStatement()
rs = stmt.executeQuery("SELECT 1 FROM SYSIBM.SYSDUMMY1")

while rs.next():
    print("Risultato:", rs.getInt(1))

rs.close()
stmt.close()
conn.close()

print("TUTTO OK!")