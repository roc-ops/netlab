set -e
netlab graph --title "Exclude NMS" --exclude nms dot-no-nms.svg
netlab graph --title "Exclude NMS link" --exclude nms_link dot-no-nms-link.svg
netlab graph --title "Include bgp/core" --include bgp --include core dot-core.svg
