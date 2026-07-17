set -e
netlab graph --title "Exclude NMS" --exclude nms -e d2 d2-no-nms.svg
netlab graph --title "Exclude NMS link" --exclude nms_link -e d2 d2-no-nms-link.svg
netlab graph --title "Include bgp/core" --include bgp --include core d2-core.svg
