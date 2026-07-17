set -e
netlab up --snapshot
netlab collect -o saved -l dut
netlab down
