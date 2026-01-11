# 1. Heredoc no-op — usually highlights as a string block (faded/quoted look)
: <<'DEAD_CODE'
echo "This is dead"
sleep 10
echo "Never runs"
# Any code here is inert
DEAD_CODE

# 2. if false; then — highlights as inactive control flow (often grayed/dimmed)
if false; then
    echo "Totally skipped"
    for i in 1 2 3; do
        echo "Nope $i"
    done
    echo "Still dead"
fi

# 3. Function wrapper — collapsible/foldable in most editors, looks like a function def
dead_code() {
    echo "Inactive function"
    ls -la
    while true; do
        echo "Infinite but never called"
        break
    done
}
# (Never call dead_code to keep it dead)
