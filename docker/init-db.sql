\getenv app_user DB_USER
\getenv app_password DB_PASSWORD
\getenv app_database DB_NAME
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
CREATE DATABASE :"app_database" OWNER :"app_user";
CREATE DATABASE ri_fest_test OWNER :"app_user";
