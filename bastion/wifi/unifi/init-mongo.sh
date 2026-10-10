#!/bin/bash
# Creates UniFi's MongoDB user. Mounted into unifi-db at /docker-entrypoint-initdb.d/, so the
# mongo image runs it once, on an empty volume, and never again. The body is the linuxserver
# unifi-network-application README's script verbatim ("do not modify"); the values come from
# the MONGO_* environment in ../docker-compose.yml.

if which mongosh > /dev/null 2>&1; then
  mongo_init_bin='mongosh'
else
  mongo_init_bin='mongo'
fi
"${mongo_init_bin}" <<EOF
use ${MONGO_AUTHSOURCE}
db.auth("${MONGO_INITDB_ROOT_USERNAME}", "${MONGO_INITDB_ROOT_PASSWORD}")
db.createUser({
  user: "${MONGO_USER}",
  pwd: "${MONGO_PASS}",
  roles: [
    "clusterMonitor",
    { db: "${MONGO_DBNAME}", role: "dbOwner" },
    { db: "${MONGO_DBNAME}_stat", role: "dbOwner" },
    { db: "${MONGO_DBNAME}_audit", role: "dbOwner" },
    { db: "${MONGO_DBNAME}_restore", role: "dbOwner" }
  ]
})
EOF
