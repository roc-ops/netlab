set -e
netlab up --snapshot --no-config
netlab config -l dut --reload saved
netlab initial -l x1 --no-message
