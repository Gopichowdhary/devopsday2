#!/bin/bash
# ==============================================================================
# oracle_auto_patch.sh
#
# Oracle 19c Patch Automation
#
# Configuration values such as PATCH_URL, WORK_DIR, OPATCH_MIN_VERSION,
# and MIN_SPACE_GB are read from an external input file.
#
# Usage:
#   chmod +x oracle_auto_patch.sh
#   ./oracle_auto_patch.sh oracle_patch_input.conf
#
# ==============================================================================

set -o pipefail

CONFIG_FILE="${1:-oracle_patch_input.conf}"

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: Input file not found: $CONFIG_FILE"
    echo "Usage: $0 <input_file>"
    exit 1
fi

# Load input file
# Expected variables:
# PATCH_URL
# WORK_DIR
# OPATCH_MIN_VERSION
# MIN_SPACE_GB
source "$CONFIG_FILE"

# Validate required input values
[[ -n "${PATCH_URL:-}" ]] || { echo "ERROR: PATCH_URL is missing in input file"; exit 1; }
[[ -n "${WORK_DIR:-}" ]] || { echo "ERROR: WORK_DIR is missing in input file"; exit 1; }
[[ -n "${OPATCH_MIN_VERSION:-}" ]] || { echo "ERROR: OPATCH_MIN_VERSION is missing in input file"; exit 1; }
[[ -n "${MIN_SPACE_GB:-}" ]] || { echo "ERROR: MIN_SPACE_GB is missing in input file"; exit 1; }

LOG="/tmp/oracle_patch_$(date +%Y%m%d_%H%M%S).log"

log() {
    echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"
}

success() {
    echo "[$(date '+%H:%M:%S')] SUCCESS: $*" | tee -a "$LOG"
}

warn() {
    echo "[$(date '+%H:%M:%S')] WARNING: $*" | tee -a "$LOG"
}

die() {
    echo "[$(date '+%H:%M:%S')] ERROR: $*" | tee -a "$LOG"
    exit 1
}

run_sql() {
sqlplus -S / as sysdba >> "$LOG" 2>&1 <<EOF
WHENEVER SQLERROR EXIT SQL.SQLCODE
$1
EXIT;
EOF
}

###############################################################################
# Select Database
###############################################################################

select_database() {
    echo "Available Oracle Databases on this Server:"
    echo "------------------------------------------"

    mapfile -t DB_LIST < <(
        ps -ef | grep pmon | grep -v grep | awk -F_ '{print $NF}' | sort
    )

    [[ ${#DB_LIST[@]} -gt 0 ]] || die "No running Oracle databases found"

    declare -A DB_HOME_MAP
    declare -A HOME_DB_MAP

    COUNT=1
    for DB in "${DB_LIST[@]}"
    do
        HOME=$(awk -F: -v db="$DB" '$1 == db {print $2; exit}' /etc/oratab)

        if [[ -z "$HOME" ]]; then
            echo "$COUNT) $DB  - ORACLE_HOME not found in /etc/oratab"
        else
            echo "$COUNT) $DB  - $HOME"
            DB_HOME_MAP["$DB"]="$HOME"
            HOME_DB_MAP["$HOME"]+="$DB "
        fi

        COUNT=$((COUNT+1))
    done

    echo
    echo "Oracle Home grouping:"
    echo "---------------------"

    HOME_COUNT=1
    declare -A HOME_INDEX_MAP

    while IFS= read -r HOME
    do
        echo "$HOME_COUNT) $HOME"
        echo "   Databases: ${HOME_DB_MAP[$HOME]}"
        HOME_INDEX_MAP["$HOME_COUNT"]="$HOME"
        HOME_COUNT=$((HOME_COUNT+1))
    done < <(printf "%s\n" "${!HOME_DB_MAP[@]}" | sort)

    echo

    if [[ ${#HOME_DB_MAP[@]} -eq 1 ]]; then
        SELECTED_HOME=$(printf "%s\n" "${!HOME_DB_MAP[@]}")

        read -r -p "Patch all databases using this Oracle Home? (yes/no): " CONFIRM_ALL

        if [[ "${CONFIRM_ALL,,}" != "yes" ]]; then
            echo "Patching cancelled."
            exit 0
        fi

    else
        echo "Multiple Oracle Homes found."
        echo "You must patch one Oracle Home at a time."
        echo

        read -r -p "Enter the Oracle Home number to patch: " HOME_CHOICE

        SELECTED_HOME="${HOME_INDEX_MAP[$HOME_CHOICE]}"

        [[ -n "$SELECTED_HOME" ]] || die "Invalid Oracle Home selection"

        echo
        echo "Selected Oracle Home:"
        echo "$SELECTED_HOME"
        echo
        echo "Databases using this home: ${HOME_DB_MAP[$SELECTED_HOME]}"
        echo

        read -r -p "Proceed with patching this Oracle Home? (yes/no): " CONFIRM_HOME

        if [[ "${CONFIRM_HOME,,}" != "yes" ]]; then
            echo "Patching cancelled."
            exit 0
        fi
    fi

    ORACLE_HOME="$SELECTED_HOME"
    PATCH_DATABASES="${HOME_DB_MAP[$SELECTED_HOME]}"

    export ORACLE_HOME
    export PATH="$ORACLE_HOME/bin:$ORACLE_HOME/OPatch:$PATH"
    export LD_LIBRARY_PATH="$ORACLE_HOME/lib:${LD_LIBRARY_PATH:-}"

    OPATCH="$ORACLE_HOME/OPatch/opatch"
    DATAPATCH="$ORACLE_HOME/OPatch/datapatch"
    ZIP_FILE="$WORK_DIR/$(basename "$PATCH_URL")"

    log "Selected ORACLE_HOME : $ORACLE_HOME"
    log "Databases to patch   : $PATCH_DATABASES"
    log "Input file           : $CONFIG_FILE"
    log "Patch URL            : $PATCH_URL"
    log "Work Dir             : $WORK_DIR"
}

###############################################################################
# Discover Databases Sharing Same ORACLE_HOME
###############################################################################

discover_home_databases() {
    HOME_DBS=()

    while IFS=: read -r SID HOME FLAG
    do
        [[ "$SID" =~ ^# ]] && continue
        [[ -z "$SID" ]] && continue

        if [[ "$HOME" == "$ORACLE_HOME" ]]; then
            HOME_DBS+=("$SID")
        fi
    done < /etc/oratab

    log "Databases using same ORACLE_HOME:"
    for DB in "${HOME_DBS[@]}"
    do
        log " -> $DB"
    done
}

###############################################################################
# STEP 0: Preflight Checks
###############################################################################

preflight_checks() {
    log "STEP 0: Running preflight checks..."

    [[ -x "$OPATCH" ]] || die "OPatch binary not found: $OPATCH"

    CURRENT_VER=$("$OPATCH" version 2>/dev/null | awk '/OPatch Version/ {print $NF}')

    [[ -n "$CURRENT_VER" ]] || die "Unable to determine OPatch version."

    log "Installed OPatch version : $CURRENT_VER"
    log "Required OPatch version  : >= $OPATCH_MIN_VERSION"

    LOWEST=$(printf '%s\n%s\n' "$OPATCH_MIN_VERSION" "$CURRENT_VER" | sort -V | head -1)

    if [[ "$LOWEST" != "$OPATCH_MIN_VERSION" ]]; then
        die "OPatch version too old."
    fi

    success "OPatch version validation passed."

    AVAIL_KB=$(df -k "$ORACLE_HOME" | awk 'NR==2 {print $4}')
    AVAIL_GB=$(awk "BEGIN {printf \"%.2f\", $AVAIL_KB/1024/1024}")
    REQUIRED_KB=$((MIN_SPACE_GB * 1024 * 1024))

    log "Available disk space : ${AVAIL_GB} GB"
    log "Required disk space  : ${MIN_SPACE_GB} GB"

    (( AVAIL_KB >= REQUIRED_KB )) || die "Insufficient disk space."

    success "Disk space validation passed."
}

###############################################################################
# STEP 1: Download Patch
###############################################################################

download_patch() {
    log "STEP 1: Downloading patch..."

    mkdir -p "$WORK_DIR"

    if [[ -f "$ZIP_FILE" ]]; then
        log "Patch zip already exists."
        log "Skipping download."
        return
    fi

    wget --progress=bar:force \
         --tries=3 \
         --timeout=120 \
         -O "$ZIP_FILE" \
         "$PATCH_URL" >> "$LOG" 2>&1 \
         || die "Patch download failed."

    success "Patch downloaded successfully."
}

###############################################################################
# STEP 2: Unzip Patch
###############################################################################

unzip_patch() {
    log "STEP 2: Checking extracted patch..."

    EXISTING_PATCH_DIR=$(find "$WORK_DIR" \
        -maxdepth 2 \
        -type d \
        | awk -F/ '/\/[0-9]+$/ {print; exit}')

    if [[ -n "$EXISTING_PATCH_DIR" ]]; then
        log "Patch already extracted."
        log "Skipping unzip."
        return
    fi

    log "Extracting patch bundle..."

    unzip -oq "$ZIP_FILE" -d "$WORK_DIR" >> "$LOG" 2>&1 \
        || die "Unzip failed."

    success "Patch extracted successfully."
}

###############################################################################
# STEP 3: Discover DB and JVM Sub-Patches
###############################################################################

discover_sub_patches() {
    log "STEP 3: Discovering DB and JVM sub-patches..."

    DB_PATCH=""
    JVM_PATCH=""

    while IFS= read -r INVENTORY_FILE; do
        PATCH_DIR=$(dirname "$(dirname "$(dirname "$INVENTORY_FILE")")")
        PATCH_ID=$(basename "$PATCH_DIR")

        DESC=$(tr '\n' ' ' < "$INVENTORY_FILE" \
            | sed 's/  */ /g' \
            | grep -oiPm1 '(?<=<patch_description>)[^<]+')

        DESC_LOWER=$(echo "$DESC" | tr '[:upper:]' '[:lower:]')

        log "Patch $PATCH_ID description: $DESC"

        if echo "$DESC_LOWER" | grep -qiE "ojvm|java.?vm|javavm|oracle jvm|java virtual machine"; then
            if [[ -z "$JVM_PATCH" ]]; then
                JVM_PATCH="$PATCH_DIR"
                success "Detected JVM patch : $PATCH_ID"
            fi
            continue
        fi

        if echo "$DESC_LOWER" | grep -qiE "database release update|release update|db ru|rdbms"; then
            if [[ -z "$DB_PATCH" ]]; then
                DB_PATCH="$PATCH_DIR"
                success "Detected DB patch : $PATCH_ID"
            fi
            continue
        fi

        warn "Skipping non-applicable patch : $PATCH_ID"

    done < <(
        find "$WORK_DIR" \
            -type f \
            -path "*/etc/config/inventory.xml" \
            | sort -u
    )

    [[ -n "$DB_PATCH" ]] || die "DB RU patch could not be detected."
    [[ -n "$JVM_PATCH" ]] || die "OJVM patch could not be detected."

    success "Final DB Patch  : $(basename "$DB_PATCH")"
    success "Final JVM Patch : $(basename "$JVM_PATCH")"
}

###############################################################################
# STEP 4: Conflict Checks
###############################################################################

pre_check() {
    log "STEP 4: Running conflict checks..."

    for PATCH in "$DB_PATCH" "$JVM_PATCH"
    do
        PATCH_ID=$(basename "$PATCH")

        log "Checking conflicts for patch $PATCH_ID"

        "$OPATCH" prereq CheckConflictAgainstOHWithDetail \
            -ph "$PATCH" >> "$LOG" 2>&1 \
            || die "Conflict detected for patch $PATCH_ID"

        success "No conflicts for patch $PATCH_ID"
    done
}

###############################################################################
# STEP 5: Stop All Databases
###############################################################################

stop_all_dbs() {
    log "STEP 5: Stopping all databases using same ORACLE_HOME..."

    for DB in "${HOME_DBS[@]}"
    do
        export ORACLE_SID="$DB"

        log "Stopping $DB"

sqlplus -s / as sysdba <<EOF >> "$LOG" 2>&1
shutdown immediate;
exit;
EOF
    done
}

###############################################################################
# STEP 6: Stop Listener
###############################################################################

stop_listener() {
    log "STEP 6: Stopping listener..."

    lsnrctl status >/dev/null 2>&1

    if [[ $? -eq 0 ]]; then
        lsnrctl stop >> "$LOG" 2>&1
        success "Listener stopped."
    else
        log "Listener is not running."
    fi
}

###############################################################################
# STEP 7: Apply Patches
###############################################################################

apply_patches() {
    log "STEP 7: Applying patches..."

    for PATCH in "$DB_PATCH" "$JVM_PATCH"
    do
        PATCH_ID=$(basename "$PATCH")

        log "Applying patch : $PATCH_ID"

        cd "$PATCH" || die "Cannot access patch directory: $PATCH"

        "$OPATCH" apply -silent >> "$LOG" 2>&1 \
            || {
                start_all_dbs
                die "Patch apply failed: $PATCH_ID"
            }

        success "Patch applied successfully : $PATCH_ID"
    done
}

###############################################################################
# STEP 8: Start Listener
###############################################################################

start_listener() {
    log "STEP 8: Starting listener..."

    lsnrctl status >/dev/null 2>&1

    if [[ $? -ne 0 ]]; then
        lsnrctl start >> "$LOG" 2>&1
        success "Listener started."
    else
        log "Listener is already running."
    fi
}

###############################################################################
# STEP 9: Start All Databases
###############################################################################

start_all_dbs() {
    log "STEP 9: Starting all databases..."

    for DB in "${HOME_DBS[@]}"
    do
        export ORACLE_SID="$DB"

        log "Starting $DB"

sqlplus -s / as sysdba <<EOF >> "$LOG" 2>&1
startup;
exit;
EOF
    done
}

###############################################################################
# STEP 10: Run Datapatch
###############################################################################

run_datapatch() {
    log "STEP 10: Running datapatch on all databases..."

    declare -gA DB_STATUS

    for DB in "${HOME_DBS[@]}"
    do
        export ORACLE_SID="$DB"

        ORACLE_HOME=$(grep "^${ORACLE_SID}:" /etc/oratab | cut -d: -f2)

        export ORACLE_HOME
        export PATH="$ORACLE_HOME/bin:$ORACLE_HOME/OPatch:$PATH"

        DATAPATCH="$ORACLE_HOME/OPatch/datapatch"

        [[ -x "$DATAPATCH" ]] || die "datapatch binary not found for $ORACLE_SID"

        log "------------------------------------------------------------"
        log "Running datapatch for Database : $ORACLE_SID"
        log "ORACLE_HOME : $ORACLE_HOME"
        log "------------------------------------------------------------"

        "$DATAPATCH" -verbose >> "$LOG" 2>&1
        RC=$?

        if [[ $RC -eq 0 ]]; then
            DB_STATUS[$ORACLE_SID]="SUCCESS"
            success "Datapatch completed successfully for $ORACLE_SID"
        else
            DB_STATUS[$ORACLE_SID]="FAILED"
            warn "Datapatch failed for $ORACLE_SID"
        fi
    done
}

###############################################################################
# STEP 11: Verify OPatch Inventory
###############################################################################

verify() {

    log "STEP 11: Verifying installed patches..."

    "$OPATCH" lspatches | cut -d';' -f2- | tee -a "$LOG"

    for PATCH in "$DB_PATCH" "$JVM_PATCH"; do

        PATCH_ID=$(basename "$PATCH")

        "$OPATCH" lspatches | awk -F';' '{print $1}' | grep -qx "$PATCH_ID"

        if [[ $? -eq 0 ]]; then
            success "Patch verified : $PATCH_ID"
        else
            warn "Patch NOT found : $PATCH_ID"
        fi

    done
}


###############################################################################
# STEP 12: Verify SQL Registry
###############################################################################

verify_sqlpatch() {
    log "STEP 12: Verifying SQL patch registry..."

    for DB in "${HOME_DBS[@]}"
    do
        export ORACLE_SID="$DB"

        log "Patch Registry for $DB"

sqlplus -s / as sysdba <<EOF >> "$LOG" 2>&1
set lines 200
col description format a45

select patch_id,
       description,
       status,
       action,
       action_time
from dba_registry_sqlpatch
order by action_time desc;

exit;
EOF
    done
}

###############################################################################
# Summary
###############################################################################

summary() {
    echo ""
    echo "=================================================================="
    echo "           ORACLE PATCHING COMPLETED"
    echo "=================================================================="
    echo ""
    echo "Patched Databases"
    echo "-----------------"

    for DB in "${HOME_DBS[@]}"
    do
        printf "   %-20s %s\n" "$DB" "${DB_STATUS[$DB]:-NOT_RUN}"
    done

    echo ""
    echo "Database RU Patch : $(basename "$DB_PATCH")"
    echo "OJVM Patch        : $(basename "$JVM_PATCH")"
    echo "Log File          : $LOG"
    echo "=================================================================="
}

###############################################################################
# Main Execution
###############################################################################

log "=================================================================="
log "Oracle Auto Patching Started"
log "=================================================================="

select_database
discover_home_databases
preflight_checks
download_patch
unzip_patch
discover_sub_patches
pre_check
stop_all_dbs
stop_listener
apply_patches
start_listener
start_all_dbs
run_datapatch
sleep 10
verify
verify_sqlpatch
summary

log "=================================================================="
log "Oracle Auto Patching Finished"
log "=================================================================="

