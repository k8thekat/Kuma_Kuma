import mysql.connector
import tokens

mydb = mysql.connector.connect(
    host = tokens.mysql_host,
    user = tokens.mysql_user,
    password = tokens.mysql_password
)

print(mydb)