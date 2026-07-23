###############################################################################
# Select Database
###############################################################################

echo "Available Databases"

DB_LIST=$(ps -ef | grep pmon | grep -v grep | awk -F_ '{print $NF}')

COUNT=1
for DB in $DB_LIST
do
   echo "$COUNT) $DB"
   eval "DB$COUNT=$DB"
   COUNT=$((COUNT+1))
done

read -p "Select Database Number: " CHOICE

ORACLE_SID=$(eval echo \$DB$CHOICE)

[ -z "$ORACLE_SID" ] && die "Invalid Selection"

ORACLE_HOME=$(grep "^${ORACLE_SID}:" /etc/oratab | cut -d: -f2)

[ -z "$ORACLE_HOME" ] && die "Unable to determine ORACLE_HOME"

export ORACLE_HOME
export ORACLE_SID
export PATH=$ORACLE_HOME/bin:$ORACLE_HOME/OPatch:$PATH

log "Selected SID  : $ORACLE_SID"
log "ORACLE_HOME   : $ORACLE_HOME"

###############################################################################
# Discover Databases Sharing Same ORACLE_HOME
###############################################################################

HOME_DBS=()

while IFS=: read SID HOME FLAG
do
    [[ "$SID" =~ ^# ]] && continue

    if [[ "$HOME" = "$ORACLE_HOME" ]]
    then
        HOME_DBS+=("$SID")
    fi

done < /etc/oratab

log "Databases using same ORACLE_HOME"

for DB in "${HOME_DBS[@]}"
do
    log " -> $DB"
done
