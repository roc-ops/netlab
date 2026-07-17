check_validate() {
  netlab validate -q $1                         # execute a test
  EXIT_CODE=$?
  if [ $EXIT_CODE -ne $2 ]; then                # did we get the expected exit code?
    echo "===================================================================="
    echo "FAIL: $1 scenario returned exit code $EXIT_CODE (expected $2)"
    echo "===================================================================="
    exit 1
  fi
  echo "===================================================================="
  echo "SUCCESS: $1 passed with exit code $2"
  echo "===================================================================="
  echo
}

check_validate ping_OK 0
check_validate ping_EXPECT 0
check_validate ping_FAIL 1
check_validate ping_WARN 3
check_validate ping_XP_NF 1
